"""Rückblick-Test des Ratgebers.

Zwei Fragen, beide nur mit Daten, die zum jeweiligen Zeitpunkt vorlagen:

1. Nachkauf-Stufen: Lohnt es sich, 1.000 € Reserve gestaffelt bei Rückgängen
   (−10/−15/−20/−25/−30 % vom 12-Monats-Hoch → 10/15/20/25/30 %) anzulegen,
   statt alles sofort? Geprüft für jeden Monatsanfang als Startpunkt, Ergebnis
   nach 3 und 5 Jahren.
2. Punktzahl: Haben Werte mit hoher Punktzahl danach besser abgeschnitten als
   Werte mit niedriger? Punktzahl an jedem Monatsende, gemessen wird die
   Entwicklung der folgenden 3, 6 und 12 Monate.

Grenzen (stehen auch im Ergebnis): Kurse von Yahoo inkl. Dividenden
(auto_adjust), in Handelswährung. Getestet werden die Werte, die es heute gibt;
Firmen, die zwischenzeitlich verschwunden sind, fehlen (Überlebenden-Verzerrung,
macht Einzelaktien zu gut). Keine Gebühren, keine Steuern.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import scoring
from .config import ROOT, load_settings

STAGES = [(0.10, 0.10), (0.15, 0.15), (0.20, 0.20), (0.25, 0.25), (0.30, 0.30)]  # (Rückgang, Anteil der Reserve)

# Reihen für den Nachkauf-Test: (Name, Yahoo-Symbol, ab)
PYRAMID_SERIES = [
    ("MSCI World (iShares, in €)", "EUNL.DE", "2009-10-01"),
    ("S&P 500 inkl. Dividenden", "^SP500TR", "1990-01-01"),
    ("DAX", "^GDAXI", "1990-01-01"),
    ("Nasdaq 100", "^NDX", "1990-01-01"),
]
SCORE_FROM = "2012-01-01"
HORIZONS = {"3M": 63, "6M": 126, "12M": 252}


# ----------------------------------------------------------------------------
# 1. Nachkauf-Stufen
# ----------------------------------------------------------------------------

def rolling_high(close: np.ndarray) -> np.ndarray:
    return pd.Series(close).rolling(252, min_periods=1).max().to_numpy()


def simulate_pyramid(close: np.ndarray, start: int, end: int, *, cash_rate: float = 0.0,
                     allowed: np.ndarray | None = None, deploy_after: int | None = None,
                     high: np.ndarray | None = None) -> dict[str, float]:
    """Spielt die Staffel für 1 Einheit Reserve von Index ``start`` bis ``end`` durch.

    ``close`` braucht mindestens 252 Kurse vor ``start`` (für das 12-Monats-Hoch).
    ``allowed[i]`` False = Stufe an Tag i nicht erlaubt (z. B. Punktzahl unter 65).
    ``deploy_after`` = nach so vielen Handelstagen den Rest sofort anlegen.
    """
    cash, units = 1.0, 0.0
    hit: set[int] = set()
    daily_rate = (1 + cash_rate) ** (1 / 252) - 1
    bought_first: int | None = None
    if high is None:
        high = rolling_high(close)
    for i in range(start, end + 1):
        cash *= 1 + daily_rate
        dd = 1 - close[i] / high[i]
        if dd <= 1e-9:
            hit.clear()  # neues 12-Monats-Hoch: Stufen wieder frei
        for k, (thr, share) in enumerate(STAGES):
            if k in hit or dd < thr - 1e-9:
                continue
            if allowed is not None and not allowed[i]:
                continue
            amt = min(share, cash)
            if amt > 0:
                units += amt / close[i]
                cash -= amt
                bought_first = bought_first if bought_first is not None else i - start
            hit.add(k)
        if deploy_after is not None and i - start == deploy_after and cash > 0:
            units += cash / close[i]
            cash = 0.0
    value = units * close[end] + cash
    return {"value": value, "cash_left": cash, "first_buy_days": -1 if bought_first is None else bought_first}


def pyramid_study(close: pd.Series, horizon_days: int, *, cash_rate: float = 0.0,
                  allowed: pd.Series | None = None, deploy_after: int | None = None) -> dict[str, Any] | None:
    s = close.dropna()
    c = s.to_numpy()
    idx = s.index
    allow = None if allowed is None else allowed.reindex(idx).fillna(False).to_numpy(bool)
    # Startpunkte: erster Handelstag jedes Monats, mit 1 Jahr Vorlauf und vollem Zeitraum danach
    months = pd.Series(np.arange(len(idx)), index=idx).groupby([idx.year, idx.month]).first().to_numpy()
    starts = [int(i) for i in months if i >= 252 and i + horizon_days < len(c)]
    if not starts:
        return None
    rel, cash_left, never = [], [], 0
    high = rolling_high(c)
    for st in starts:
        end = st + horizon_days
        lump = c[end] / c[st]
        r = simulate_pyramid(c, st, end, cash_rate=cash_rate, allowed=allow, deploy_after=deploy_after, high=high)
        rel.append(r["value"] / lump - 1)
        cash_left.append(r["cash_left"])
        never += r["first_buy_days"] < 0
    rel_a = np.array(rel)
    return {
        "windows": len(starts),
        "from": str(idx[starts[0]].date()), "to": str(idx[starts[-1]].date()),
        "win_share": round(float(np.mean(rel_a >= 0)), 3),
        "median_diff": round(float(np.median(rel_a)), 4),
        "mean_diff": round(float(np.mean(rel_a)), 4),
        "worst_diff": round(float(np.min(rel_a)), 4),
        "best_diff": round(float(np.max(rel_a)), 4),
        "avg_cash_left": round(float(np.mean(cash_left)), 3),
        "never_bought_share": round(never / len(starts), 3),
    }


def score_allowed(close: pd.Series, kind: str = "etf", name: str = "", threshold: float = scoring.INTERESTING) -> pd.Series:
    """Für jeden Tag: Hätte die Punktzahl an diesem Tag die Stufe erlaubt? (Monatsweise berechnet.)"""
    s = close.dropna()
    c = s.to_numpy()
    out = pd.Series(False, index=s.index)
    month_end = pd.Series(np.arange(len(s)), index=s.index).groupby([s.index.year, s.index.month]).last().to_numpy()
    prev = 0
    ok = False
    for i in month_end:
        out.iloc[prev:i + 1] = ok  # gilt bis zum nächsten Monatsende
        res = scoring.score(c[:i + 1], kind=kind, name=name)
        ok = bool(res and res["score"] >= threshold)
        prev = i + 1
    out.iloc[prev:] = ok
    return out


def run_pyramid(prices: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for label, sym, start in PYRAMID_SERIES:
        df = prices.get(sym)
        if df is None or df.empty:
            rows.append({"series": label, "symbol": sym, "error": "keine Daten"})
            continue
        close = df["close"][df.index >= pd.Timestamp(start, tz=df.index.tz)]
        allowed = score_allowed(close, kind="etf", name=label)
        entry: dict[str, Any] = {"series": label, "symbol": sym, "years": round(len(close) / 252, 1)}
        for years in (3, 5):
            h = 252 * years
            entry[f"{years}J"] = {
                "staffel": pyramid_study(close, h),
                "staffel_zins2": pyramid_study(close, h, cash_rate=0.02),
                "staffel_rest_nach_12M": pyramid_study(close, h, deploy_after=252),
                "staffel_nur_ab_65": pyramid_study(close, h, allowed=allowed),
            }
        rows.append(entry)
    return rows


# ----------------------------------------------------------------------------
# 2. Punktzahl
# ----------------------------------------------------------------------------

def score_study(prices: dict[str, pd.DataFrame], meta: dict[str, dict[str, str]],
                bench: pd.Series | None) -> dict[str, Any]:
    """Punktzahl an jedem Monatsende → Entwicklung danach, getrennt nach Einstufung und Art."""
    recs: list[dict[str, Any]] = []
    t0 = pd.Timestamp(SCORE_FROM)
    for sym, df in prices.items():
        info = meta.get(sym)
        if info is None or df.empty:
            continue
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        c = df["close"].to_numpy()
        v = df["volume"].to_numpy() if "volume" in df else None
        idx = df.index
        month_end = pd.Series(np.arange(len(idx)), index=idx).groupby([idx.year, idx.month]).last().to_numpy()
        for i in month_end:
            if idx[i].tz_localize(None) < t0 or i < scoring.MIN_BARS:
                continue
            res = scoring.score(c[:i + 1], None if v is None else v[:i + 1], kind=info["kind"], name=info["name"])
            if res is None:
                continue
            rec = {"sym": sym, "kind": info["kind"], "month": idx[i].strftime("%Y-%m"), "score": res["score"],
                   "grade": res["grade"]}
            for key, h in HORIZONS.items():
                if i + h < len(c):
                    rec[key] = c[i + h] / c[i] - 1
            if i + 252 < len(c):
                rec["dd12"] = scoring.max_drawdown(c[i:i + 253])
            recs.append(rec)
    if not recs:
        return {"error": "keine Daten"}
    df = pd.DataFrame(recs)

    def summarize(g: pd.DataFrame) -> dict[str, Any]:
        out: dict[str, Any] = {"n": int(len(g))}
        for key in HORIZONS:
            x = g[key].dropna() if key in g else pd.Series(dtype=float)
            if len(x):
                out[key] = {"mean": round(float(x.mean()), 4), "median": round(float(x.median()), 4),
                            "pos_share": round(float((x > 0).mean()), 3), "n": int(len(x))}
        if "dd12" in g and g["dd12"].notna().any():
            d = g["dd12"].dropna()
            out["dd12_mean"] = round(float(d.mean()), 4)
            out["dd12_over30"] = round(float((d > 0.30).mean()), 3)
        return out

    result: dict[str, Any] = {"months": [df["month"].min(), df["month"].max()], "instruments": int(df["sym"].nunique())}
    for kind in ("etf", "aktie"):
        sub = df[df["kind"] == kind]
        if sub.empty:
            continue
        result[kind] = {g: summarize(sub[sub["grade"] == g]) for g in ("interessant", "neutral", "vorsicht")}
        result[kind]["alle"] = summarize(sub)
        # Monatsweise: Durchschnitt „interessant“ minus Durchschnitt aller Werte desselben Monats
        diffs = []
        for _, m in sub.dropna(subset=["12M"]).groupby("month"):
            top = m[m["grade"] == "interessant"]["12M"]
            if len(top) >= 3 and len(m) >= 10:
                diffs.append(top.mean() - m["12M"].mean())
        if diffs:
            d = np.array(diffs)
            result[kind]["top_vs_all_12M"] = {"months": len(d), "mean": round(float(d.mean()), 4),
                                              "better_share": round(float((d > 0).mean()), 3)}
    if bench is not None and not bench.empty:
        b = bench.dropna()
        bi = b.index
        me = pd.Series(np.arange(len(bi)), index=bi).groupby([bi.year, bi.month]).last().to_numpy()
        fw = [b.iloc[i + 252] / b.iloc[i] - 1 for i in me if bi[i].tz_localize(None) >= t0 and i + 252 < len(b)]
        if fw:
            result["benchmark_12M"] = {"name": "MSCI World (EUNL)", "mean": round(float(np.mean(fw)), 4),
                                       "median": round(float(np.median(fw)), 4)}
    return result


# ----------------------------------------------------------------------------
# Daten laden und Bericht schreiben
# ----------------------------------------------------------------------------

def load_yahoo(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        raw = yf.download(chunk, period="max", interval="1d", auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
        for sym in chunk:
            try:
                d = raw[sym] if len(chunk) > 1 else raw
            except KeyError:
                continue
            d = d.rename(columns=str.lower)[["close", "volume"]].dropna(subset=["close"])
            if d.index.tz is None:
                d.index = d.index.tz_localize("UTC")
            if len(d):
                out[sym] = d
    return out


def universe_meta() -> dict[str, dict[str, str]]:
    settings = load_settings()
    meta = {}
    for inst in settings.instruments:
        sym = inst.symbol_for("yahoo")
        if sym and inst.tier == "core" and inst.kind in ("aktie", "etf"):
            meta[sym] = {"kind": inst.kind, "name": inst.name, "id": inst.id}
    return meta


def run(loader: Callable[[list[str]], dict[str, pd.DataFrame]] = load_yahoo) -> dict[str, Any]:
    meta = universe_meta()
    symbols = sorted(set(meta) | {s for _, s, _ in PYRAMID_SERIES})
    prices = loader(symbols)
    result = {
        "created": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "pyramid": run_pyramid(prices),
        "score": score_study({k: v for k, v in prices.items() if k in meta}, meta,
                             prices.get("EUNL.DE", pd.DataFrame()).get("close")),
        "limits": [
            "Kurse von Yahoo inklusive Dividenden, in Handelswährung, ohne Gebühren und Steuern.",
            "Getestet werden Werte, die es heute gibt. Verschwundene Firmen fehlen; das lässt Einzelaktien besser aussehen.",
            "Qualität/Bewertung und Nachrichten fehlen im Test (keine freie Historie).",
            "Startpunkte im Monatsabstand überlappen sich stark; sie sind keine unabhängigen Versuche.",
            "Vergangene Ergebnisse sagen künftige nicht sicher voraus.",
        ],
    }
    return result


def _p(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.1f} %".replace(".", ",").replace("-", "−")


def report_markdown(r: dict[str, Any]) -> str:
    lines = ["# Rückblick-Test des Ratgebers", "", f"Erstellt: {r['created']} (UTC)", "",
             "## 1. Nachkauf-Stufen gegen sofortiges Anlegen", "",
             "Für jeden Monatsanfang als Startpunkt: 1.000 € entweder sofort angelegt oder nach der Staffel "
             "(−10/−15/−20/−25/−30 % vom 12-Monats-Hoch). „Vorteil“ = Ergebnis der Staffel geteilt durch Sofortanlage, minus 1.", ""]
    names = {"staffel": "Staffel, Rest bleibt Bargeld (0 %)", "staffel_zins2": "Staffel, Rest auf Tagesgeld (2 %)",
             "staffel_rest_nach_12M": "Staffel, Rest nach 12 Monaten anlegen", "staffel_nur_ab_65": "Staffel nur bei Punktzahl ab 65"}
    for row in r["pyramid"]:
        if "error" in row:
            lines.append(f"- {row['series']}: {row['error']}")
            continue
        lines += [f"### {row['series']} ({row['years']} Jahre Daten)", "",
                  "| Variante | Zeitraum | Startpunkte | Staffel besser | Vorteil Median | schlechtester | bester | Rest unangelegt (Ø) |",
                  "|---|---|---|---|---|---|---|---|"]
        for years in ("3J", "5J"):
            for key, label in names.items():
                s = row[years].get(key)
                if not s:
                    continue
                lines.append(f"| {label} | {years} | {s['windows']} | {s['win_share'] * 100:.0f} % | {_p(s['median_diff'])} | "
                             f"{_p(s['worst_diff'])} | {_p(s['best_diff'])} | {s['avg_cash_left'] * 100:.0f} % |")
        lines.append("")
    sc = r["score"]
    lines += ["## 2. Hat die Punktzahl etwas vorhergesagt?", ""]
    if "error" in sc:
        lines.append(sc["error"])
    else:
        lines.append(f"{sc['instruments']} Werte, Monatsenden {sc['months'][0]} bis {sc['months'][1]}.")
        lines.append("")
        for kind, title in (("etf", "ETFs"), ("aktie", "Aktien")):
            k = sc.get(kind)
            if not k:
                continue
            lines += [f"### {title}", "", "| Einstufung | Fälle | Ø 3 Monate | Ø 6 Monate | Ø 12 Monate | 12 Monate im Plus | Ø größter Rückgang 12 M | Rückgang über 30 % |",
                      "|---|---|---|---|---|---|---|---|"]
            for g in ("interessant", "neutral", "vorsicht", "alle"):
                s = k.get(g) or {}
                if not s.get("n"):
                    continue
                lines.append(f"| {g} | {s['n']} | {_p(s.get('3M', {}).get('mean'))} | {_p(s.get('6M', {}).get('mean'))} | "
                             f"{_p(s.get('12M', {}).get('mean'))} | {s.get('12M', {}).get('pos_share', 0) * 100:.0f} % | "
                             f"−{s.get('dd12_mean', 0) * 100:.0f} % | {s.get('dd12_over30', 0) * 100:.0f} % |")
            t = k.get("top_vs_all_12M")
            if t:
                lines.append("")
                lines.append(f"„Interessant“ gegen alle Werte desselben Monats: im Schnitt {_p(t['mean'])} über 12 Monate, "
                             f"besser in {t['better_share'] * 100:.0f} % von {t['months']} Monaten.")
            lines.append("")
        b = sc.get("benchmark_12M")
        if b:
            lines.append(f"Maßstab {b['name']}: Ø {_p(b['mean'])} über 12 Monate.")
            lines.append("")
    lines += ["## Grenzen", ""] + [f"- {x}" for x in r["limits"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    r = run()
    out_json = ROOT / "status" / "backtest.json"
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report_markdown(r)
    (ROOT / "docs" / "RUECKBLICK.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
