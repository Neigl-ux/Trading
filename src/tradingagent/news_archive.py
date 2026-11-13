"""Nachrichten-Archiv: Tageswerte aus den GDELT-1.0-Tagesdateien (seit April 2013).

Je Tag eine gezippte Datei mit allen erkannten Ereignissen der Weltnachrichten. Wir behalten nur
Tageskennzahlen (wenige Byte je Tag), nicht die Rohdaten:

    n           Zahl der Artikel (Summe NumArticles)
    tone        mittlerer Tonfall, gewichtet nach Artikeln (negativ = überwiegend negative Berichte)
    conflict    Anteil der Artikel über „material conflict“ (QuadClass 4: Gewalt, Angriffe, Sanktionen …)
    goldstein   mittlere Goldstein-Skala (−10 = destabilisierend, +10 = stabilisierend)
    tone_us/eu/cn, conflict_us/eu/cn   dasselbe für Ereignisse in den USA, Europa und China

Danach prüft ``study`` gegen die Kurse, ob diese Werte etwas über die folgenden Wochen und Monate
sagen. Erst wenn das so ist, dürfen Nachrichten in die Punktzahl einfließen.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://data.gdeltproject.org/events/"
OUT = ROOT / "status" / "news_history.csv"
FIELDS = ["date", "n", "tone", "conflict", "goldstein", "tone_us", "conflict_us", "tone_eu", "conflict_eu",
          "tone_cn", "conflict_cn"]
# FIPS-Ländercodes (GDELT 1.0)
EU = {"GM", "FR", "UK", "IT", "SP", "NL", "BE", "AU", "SZ", "SW", "NO", "DA", "FI", "EI", "PO", "PL", "EZ",
      "LO", "HU", "RO", "BU", "GR", "HR", "SI", "LU", "EN", "LG", "LH"}
# Spalten der Tagesdatei (0-basiert)
C_QUAD, C_GOLD, C_ART, C_TONE, C_GEO = 29, 30, 33, 34, 51


def list_days(session: requests.Session) -> list[str]:
    html = session.get(BASE + "index.html", timeout=60).text
    return sorted(set(re.findall(r"(\d{8})\.export\.CSV\.zip", html)))


def aggregate(raw: bytes) -> dict[str, float]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            df = pd.read_csv(fh, sep="\t", header=None, usecols=[C_QUAD, C_GOLD, C_ART, C_TONE, C_GEO],
                             dtype={C_GEO: "string"}, quoting=csv.QUOTE_NONE, on_bad_lines="skip", low_memory=False)
    df.columns = ["quad", "gold", "art", "tone", "geo"]
    for c in ("quad", "gold", "art", "tone"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["art", "tone"])
    df = df[df["art"] > 0]

    def part(sub: pd.DataFrame) -> tuple[float, float, float, float]:
        w = sub["art"].to_numpy(float)
        if w.sum() <= 0:
            return 0.0, math.nan, math.nan, math.nan
        tone = float(np.average(sub["tone"], weights=w))
        conflict = float(w[(sub["quad"] == 4).to_numpy()].sum() / w.sum())
        g = sub["gold"].notna().to_numpy()
        gold = float(np.average(sub["gold"].to_numpy()[g], weights=w[g])) if g.any() else math.nan
        return float(w.sum()), tone, conflict, gold

    n, tone, conflict, gold = part(df)
    out = {"n": n, "tone": tone, "conflict": conflict, "goldstein": gold}
    for key, mask in (("us", df["geo"] == "US"), ("eu", df["geo"].isin(EU)), ("cn", df["geo"] == "CH")):
        _, t, c, _ = part(df[mask.fillna(False)])
        out[f"tone_{key}"], out[f"conflict_{key}"] = t, c
    return out


def update(max_days: int | None = None, workers: int = 8) -> int:
    """Lädt alle fehlenden Tage nach. Gibt die Zahl der neuen Tage zurück."""
    session = requests.Session()
    session.headers["User-Agent"] = "trading-agent (private research)"
    have: dict[str, dict[str, Any]] = {}
    if OUT.exists():
        for row in csv.DictReader(OUT.open(encoding="utf-8")):
            have[row["date"]] = row
    days = [d for d in list_days(session) if d >= "20130401" and f"{d[:4]}-{d[4:6]}-{d[6:]}" not in have]
    if max_days:
        days = days[:max_days]
    print(f"Nachrichten-Archiv: {len(have)} Tage vorhanden, {len(days)} fehlen", flush=True)

    def fetch(d: str) -> tuple[str, dict[str, float] | None]:
        for attempt in range(3):
            try:
                r = session.get(f"{BASE}{d}.export.CSV.zip", timeout=180)
                if r.status_code == 404:
                    return d, None
                r.raise_for_status()
                return d, aggregate(r.content)
            except Exception as err:  # noqa: BLE001 – Netz/Datei: nochmal versuchen, sonst auslassen
                if attempt == 2:
                    print(f"  {d}: {err}", flush=True)
        return d, None

    new = 0
    with ThreadPoolExecutor(workers) as pool:
        for i, (d, agg) in enumerate(pool.map(fetch, days)):
            if agg:
                key = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                have[key] = {"date": key, **{k: (round(v, 5) if isinstance(v, float) and not math.isnan(v) else "")
                                             for k, v in agg.items()}}
                new += 1
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{len(days)}", flush=True)
                _write(have)
    _write(have)
    return new


def _write(rows: dict[str, dict[str, Any]]) -> None:
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])


# ----------------------------------------------------------------------------
# Rückblick: sagt die Nachrichtenlage etwas über die Kurse danach?
# ----------------------------------------------------------------------------

SIGNALS = {
    "tone": ("Tonfall weltweit", -1),          # niedriger Tonfall = schlecht
    "conflict": ("Konfliktanteil weltweit", 1),  # hoher Anteil = schlecht
    "tone_us": ("Tonfall USA", -1),
    "tone_eu": ("Tonfall Europa", -1),
    "tone_cn": ("Tonfall China", -1),
}
TARGETS = {"^GSPC": "S&P 500", "EUNL.DE": "MSCI World (Euro-ETF)", "^GDAXI": "DAX"}
HORIZONS = {"1M": 21, "3M": 63, "12M": 252}


def signal_frame(hist: pd.DataFrame) -> pd.DataFrame:
    """Je Signal: 20-Tage-Mittel, verglichen mit dem Jahr davor (z-Wert, nur Vergangenheitsdaten)."""
    out = pd.DataFrame(index=hist.index)
    for col, (_, sign) in SIGNALS.items():
        x = hist[col].astype(float)
        m20 = x.rolling(20, min_periods=15).mean()
        base = x.rolling(260, min_periods=200)
        z = (m20 - base.mean().shift(20)) / base.std().shift(20)
        out[col] = z * sign  # > 0 heißt immer: Lage schlechter als üblich
    return out


def current_state(hist: pd.DataFrame | None = None) -> dict[str, Any] | None:
    """Aktuelle Nachrichtenlage aus dem Archiv: z-Werte der Signale (> 0 = schlechter als im Jahr davor)."""
    try:
        if hist is None:
            hist = pd.read_csv(OUT, parse_dates=["date"]).set_index("date")
    except (OSError, ValueError):
        return None
    if len(hist) < 300:
        return None
    hist = hist.sort_index()
    sig = signal_frame(hist).dropna(how="all")
    if sig.empty:
        return None
    last = sig.iloc[-1]
    z = {k: (None if pd.isna(last[k]) else round(float(last[k]), 2)) for k in SIGNALS}
    worst = max(v for v in (z["tone"], z["conflict"]) if v is not None) if any(z[k] is not None for k in ("tone", "conflict")) else None
    level = None if worst is None else "angespannt" if worst >= 1.0 else "etwas angespannt" if worst >= 0.5 else "ruhig"
    tail = hist["tone"].tail(120).rolling(7, min_periods=3).mean().dropna()
    return {"date": str(sig.index[-1].date()), "z": z, "level": level,
            "names": {k: v[0] for k, v in SIGNALS.items()},
            "series": [[str(d.date()), round(float(v), 3)] for d, v in tail.items()]}


def study(prices: dict[str, pd.DataFrame], hist: pd.DataFrame | None = None) -> dict[str, Any]:
    if hist is None:
        hist = pd.read_csv(OUT, parse_dates=["date"]).set_index("date")
    hist = hist.sort_index()
    sig = signal_frame(hist)
    res: dict[str, Any] = {"days": int(len(hist)), "from": str(hist.index.min().date()), "to": str(hist.index.max().date()),
                           "targets": {}}
    for sym, label in TARGETS.items():
        df = prices.get(sym)
        if df is None or df.empty:
            continue
        c = df["close"].copy()
        c.index = c.index.tz_localize(None).normalize() if c.index.tz is not None else c.index.normalize()
        c = c[~c.index.duplicated(keep="last")]
        arr = c.to_numpy(float)
        fwd = {k: pd.Series(np.r_[arr[h:] / arr[:-h] - 1, [np.nan] * h], index=c.index) for k, h in HORIZONS.items()}
        # größter Rückgang in den nächsten 3 Monaten
        dd = []
        for i in range(len(arr)):
            seg = arr[i:i + 64]
            dd.append(float(1 - seg.min() / seg[0]) if len(seg) == 64 else np.nan)
        fwd["dd3M"] = pd.Series(dd, index=c.index)
        t: dict[str, Any] = {"label": label, "signals": {}}
        s_all = sig.reindex(c.index, method="ffill", limit=3).copy()
        # Gegenprobe: Kursverlauf der letzten 20 Tage, genauso als z-Wert (> 0 = schlechter als im Jahr davor)
        r20 = c.pct_change(20)
        base_r = r20.rolling(260, min_periods=200)
        s_all["price"] = -((r20 - base_r.mean().shift(20)) / base_r.std().shift(20))
        base = {k: float(v.mean()) for k, v in fwd.items()}
        t["all"] = {k: round(v, 4) for k, v in base.items()}
        # Monatsenden als Stichproben (weniger Überlappung als Tage)
        me = c.groupby([c.index.year, c.index.month]).tail(1).index
        for col, (name, _) in [*SIGNALS.items(), ("price", ("Gegenprobe: Kursverlauf 20 Tage", 1))]:
            z = s_all[col].reindex(me)
            ok = z.notna()
            if ok.sum() < 36:
                continue
            zz = z[ok]
            q = pd.qcut(zz, 5, labels=False, duplicates="drop")
            rows = {}
            for k, v in fwd.items():
                vv = v.reindex(zz.index)
                rows[k] = {f"Q{int(b) + 1}": round(float(vv[q == b].mean()), 4) for b in sorted(q.dropna().unique())}
                good = vv.notna()
                rows[k]["corr"] = round(float(np.corrcoef(zz[good], vv[good])[0, 1]), 3) if good.sum() > 10 else None
            worst = zz[q == q.max()]
            t["signals"][col] = {"name": name, "months": int(ok.sum()), "by_quintile": rows,
                                 "worst_quintile_months": int(len(worst))}
        zt, zp = s_all["tone"].reindex(me), s_all["price"].reindex(me)
        ok = zt.notna() & zp.notna()
        if ok.sum() >= 36:
            qt = pd.qcut(zt[ok], 5, labels=False, duplicates="drop")
            qp = pd.qcut(zp[ok], 5, labels=False, duplicates="drop")
            calm_price = qp < 3  # Kurs im Monat davor nicht unter den schlechtesten 40 %
            sel_bad, sel_good = (qt == qt.max()) & calm_price, (qt == qt.min()) & calm_price
            dd3 = fwd["dd3M"].reindex(zt[ok].index)
            r3 = fwd["3M"].reindex(zt[ok].index)
            t["tone_without_price"] = {"n_bad": int(sel_bad.sum()), "n_good": int(sel_good.sum()),
                                       "dd3_bad": round(float(dd3[sel_bad].mean()), 4), "dd3_good": round(float(dd3[sel_good].mean()), 4),
                                       "ret3_bad": round(float(r3[sel_bad].mean()), 4), "ret3_good": round(float(r3[sel_good].mean()), 4)}
        res["targets"][sym] = t
    return res


def report(res: dict[str, Any]) -> str:
    pct = lambda x: "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f} %".replace(".", ",").replace("-", "−")
    lines = ["# Nachrichten-Rückblick", "",
             f"Tageswerte aus GDELT 1.0 von {res['from']} bis {res['to']} ({res['days']} Tage). Signal = 20-Tage-Mittel im Vergleich "
             "zum Jahr davor; Q5 = die 20 % der Monate mit der schlechtesten Nachrichtenlage, Q1 = die besten. "
             "„Korrelation“ > 0 hieße: schlechtere Lage → höhere Rendite danach.", ""]
    for sym, t in res["targets"].items():
        lines += [f"## {t['label']}", "", f"Alle Monate: Ø 1 Monat {pct(t['all']['1M'])}, 3 Monate {pct(t['all']['3M'])}, "
                  f"12 Monate {pct(t['all']['12M'])}, Ø größter Rückgang in 3 Monaten −{t['all']['dd3M'] * 100:.1f} %".replace(".", ",") + ".", "",
                  "| Signal | Monate | 3 M nach Q1 (beste Lage) | 3 M nach Q5 (schlechteste) | 12 M nach Q1 | 12 M nach Q5 | Rückgang 3 M Q1 | Rückgang 3 M Q5 | Korrelation 3 M |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for col, s in t["signals"].items():
            b = s["by_quintile"]
            lines.append(f"| {s['name']} | {s['months']} | {pct(b['3M'].get('Q1'))} | {pct(b['3M'].get('Q5'))} | {pct(b['12M'].get('Q1'))} | "
                         f"{pct(b['12M'].get('Q5'))} | −{(b['dd3M'].get('Q1') or 0) * 100:.1f} % | −{(b['dd3M'].get('Q5') or 0) * 100:.1f} % | {b['3M'].get('corr')} |".replace(".", ","))
        tw = t.get("tone_without_price")
        if tw:
            lines += ["", f"Nur Monate, in denen der Kurs vorher NICHT stark gefallen war: nach schlechtem Tonfall ({tw['n_bad']} Monate) "
                      f"3 Monate {pct(tw['ret3_bad'])}, Rückgang −{tw['dd3_bad'] * 100:.1f} %; nach gutem Tonfall ({tw['n_good']} Monate) "
                      f"{pct(tw['ret3_good'])}, Rückgang −{tw['dd3_good'] * 100:.1f} %.".replace("−0.", "−0,").replace(".", ",").rstrip(",") + ""]
        lines.append("")
    lines += ["## Grenzen", "",
              "- Fünf Signale × drei Indizes × mehrere Zeiträume: Einzelne „Treffer“ können Zufall sein.",
              "- Monatsenden überlappen sich bei 3 und 12 Monaten; die Fälle sind nicht unabhängig.",
              "- GDELT erkennt Ereignisse maschinell; Tonfall misst die Wortwahl der Berichte, nicht die Wirtschaftslage.",
              "- Vergangene Zusammenhänge müssen nicht bestehen bleiben."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--study", action="store_true")
    a = ap.parse_args(argv)
    n = update(a.max_days)
    print(f"neu: {n} Tage", flush=True)
    if a.study:
        from .backtest import load_yahoo

        res = study(load_yahoo(list(TARGETS)))
        res["created"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        (ROOT / "status" / "news_study.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        md = report(res)
        (ROOT / "docs" / "NACHRICHTEN_RUECKBLICK.md").write_text(md, encoding="utf-8")
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
