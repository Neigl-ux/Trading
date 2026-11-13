"""Punktzahl des Ratgebers (0–100) aus Tageskursen.

Dieselben Funktionen nutzen der Sammler (aktuelle Bewertung) und der
Rückblick-Test (Bewertung an jedem vergangenen Monatsende, nur mit den Daten,
die damals vorlagen). Fehlt die Grundlage für einen Faktor, ist er ``None``
und sein Gewicht wird auf die übrigen verteilt.

Faktoren und Gewichte (siehe Konzeption Phase 3):
    Trend 35 %, Risiko 25 %, Qualität/Bewertung 15 %, Handelbarkeit 10 %, Passung 15 %
Qualität/Bewertung braucht Unternehmenskennzahlen; dafür gibt es im Sammler
keine kostenlose Quelle, der Faktor fehlt dort deshalb immer.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

WEIGHTS = {"trend": 0.35, "risk": 0.25, "quality": 0.15, "trade": 0.10, "fit": 0.15}
MIN_BARS = 260          # gut ein Jahr Tageskurse, sonst keine Punktzahl
INTERESTING, NEUTRAL = 65, 40

# Hebel- und Short-Produkte passen nicht zu 5–10 Jahren Anlagedauer
_LEVERAGED = re.compile(r"\b(short|ultra|ultrapro|leveraged|daily|[23]x|x[23]|bear|bull)\b", re.I)
# Geldmarkt und Anleihen: ruhig, aber kein Wachstumsbaustein für den Sparplan
_DEFENSIVE = re.compile(r"overnight|geldmarkt|money market|bond|treasury|government|anleihe|t-bill", re.I)


def _clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def is_leveraged(name: str) -> bool:
    return bool(_LEVERAGED.search(name or ""))


def is_defensive(name: str) -> bool:
    return bool(_DEFENSIVE.search(name or ""))


def max_drawdown(close: np.ndarray) -> float:
    """Größter Rückgang vom bisherigen Hoch (0.25 = −25 %)."""
    if len(close) < 2:
        return 0.0
    peak = np.maximum.accumulate(close)
    return float(np.max(1.0 - close / peak))


def risk_level(vol: float | None, kind: str) -> int | None:
    if kind == "krypto":
        return 5
    if vol is None:
        return None
    for lvl, lim in ((1, 0.12), (2, 0.20), (3, 0.30), (4, 0.45)):
        if vol < lim:
            return lvl
    return 5


def factors(close: np.ndarray, volume: np.ndarray | None = None, *, kind: str = "aktie",
            name: str = "") -> dict[str, Any] | None:
    """Faktoren für den letzten Tag der Reihe. ``close`` aufsteigend, ohne Lücken (NaN entfernt).

    Gibt ``None`` zurück, wenn weniger als MIN_BARS Kurse vorliegen.
    """
    c = np.asarray(close, dtype="float64")
    c = c[np.isfinite(c) & (c > 0)]
    n = len(c)
    if n < MIN_BARS:
        return None
    last = c[-1]
    # --- Trend ---------------------------------------------------------
    r12_1 = c[-22] / c[-253] - 1 if n >= 253 else c[-22] / c[0] - 1
    r6 = last / c[-127] - 1
    sma200 = c[-200:].mean()
    sma200_prev = c[-221:-21].mean()
    sma50 = c[-50:].mean()
    above200 = last > sma200
    golden = sma50 > sma200
    rising = sma200 > sma200_prev
    trend = 100 * (0.35 * _clip01((r12_1 + 0.10) / 0.40) + 0.25 * _clip01((r6 + 0.05) / 0.25)
                   + 0.20 * above200 + 0.10 * golden + 0.10 * rising)
    # --- Risiko --------------------------------------------------------
    rets = np.diff(np.log(c[-253:]))
    vol = float(np.std(rets, ddof=1) * math.sqrt(252))
    mdd = max_drawdown(c[-756:])
    risk = 100 * (0.5 * _clip01((0.50 - vol) / 0.40) + 0.5 * _clip01((0.60 - mdd) / 0.50))
    # --- Handelbarkeit -------------------------------------------------
    trade = None
    turnover = None
    if volume is not None:
        v = np.asarray(volume, dtype="float64")
        if len(v) >= 20 and np.nansum(v[-20:]) > 0:
            v20 = v[-20:]
            turnover = float(np.nanmean(c[-20:] * v20))
            if turnover > 0:
                trade = 100 * _clip01((math.log10(turnover) - 5) / 3)  # 100 Tsd. → 0, 100 Mio. → 100
    # --- Passung (zu deinem Profil: 5–10 Jahre, bis 30 % Rückgang, kein Krypto im Sparplan)
    fit = {"etf": 85.0, "aktie": 60.0, "rohstoff": 50.0, "krypto": 15.0}.get(kind, 50.0)
    if mdd > 0.50:
        fit -= 25
    elif mdd > 0.35:
        fit -= 10
    if is_leveraged(name):
        fit = 0.0
    fit = max(0.0, fit)
    # --- 12-Monats-Hoch (für Nachkauf-Stufen) -------------------------
    high12 = float(c[-252:].max())
    dd12 = float(1 - last / high12)
    return {
        "trend": trend, "risk": risk, "quality": None, "trade": trade, "fit": fit,
        "r12_1": float(r12_1), "r6": float(r6), "above200": bool(above200), "golden": bool(golden),
        "rising": bool(rising), "vol": vol, "mdd3": mdd, "turnover": turnover,
        "high12": high12, "dd12": dd12, "last": float(last), "bars": n,
    }


def total(f: dict[str, Any]) -> float:
    """Gewichteter Durchschnitt der vorhandenen Faktoren."""
    num = den = 0.0
    for key, w in WEIGHTS.items():
        val = f.get(key)
        if val is None:
            continue
        num += w * val
        den += w
    return num / den if den else float("nan")


def grade(score: float) -> str:
    if score >= INTERESTING:
        return "interessant"
    if score >= NEUTRAL:
        return "neutral"
    return "vorsicht"


def _pct(x: float) -> str:
    return f"{x * 100:+.0f} %".replace("+-", "−").replace("-", "−")


def reasons(f: dict[str, Any], kind: str) -> list[str]:
    """Begründung in Klartext: was dafür spricht, was dagegen."""
    out: list[str] = []
    r12 = f["r12_1"]
    if f["above200"] and f["rising"]:
        out.append(f"Aufwärtstrend: Kurs über dem steigenden 200-Tage-Durchschnitt, 12 Monate {_pct(r12)}.")
    elif not f["above200"] and not f["rising"]:
        out.append(f"Abwärtstrend: Kurs unter dem fallenden 200-Tage-Durchschnitt, 12 Monate {_pct(r12)}.")
    else:
        out.append(f"Trend gemischt: 12 Monate {_pct(r12)}, 6 Monate {_pct(f['r6'])}.")
    vol, mdd = f["vol"], f["mdd3"]
    calm = "ruhig" if vol < 0.15 else "mittel" if vol < 0.30 else "stark" if vol < 0.50 else "sehr stark"
    out.append(f"Schwankung {calm} ({vol * 100:.0f} % pro Jahr); größter Rückgang in bis zu 3 Jahren −{mdd * 100:.0f} %.")
    if mdd > 0.30 and kind != "krypto":
        out.append("Der größte Rückgang lag über den 30 %, die du aushältst.")
    if f["dd12"] >= 0.10:
        out.append(f"Aktuell −{f['dd12'] * 100:.0f} % unter dem 12-Monats-Hoch.")
    if f.get("trade") is not None and f["trade"] < 30:
        out.append("Geringer Börsenumsatz: Kauf und Verkauf können teurer werden.")
    if kind == "krypto":
        out.append("Krypto: kommt laut deinen Vorgaben nicht in den Sparplan.")
    return out


def score(close, volume=None, *, kind: str = "aktie", name: str = "") -> dict[str, Any] | None:
    """Komplette Bewertung: Faktoren, Punktzahl, Einstufung, Risiko, Begründung."""
    f = factors(close, volume, kind=kind, name=name)
    if f is None:
        return None
    s = total(f)
    missing = ["Qualität und Bewertung (keine kostenlose Quelle)"]
    if f["trade"] is None:
        missing.append("Handelbarkeit (kein Umsatz bekannt)")
    return {"score": round(s, 1), "grade": grade(s), "risk": risk_level(f["vol"], kind),
            "factors": {k: (None if f[k] is None else round(f[k], 1)) for k in WEIGHTS},
            "facts": f, "why": reasons(f, kind), "missing": missing}
