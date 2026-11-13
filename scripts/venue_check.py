"""Einmaliger Test: Liefern Yahoo-Börsenplätze mit langen Handelszeiten (Frankfurt .F, Stuttgart .SG, München .MU,
Düsseldorf .DU, Berlin .BE, Hamburg .HM) 5-Minuten-Kerzen nach Xetra-Schluss (17:30–22:00)?
Ergebnis landet in docs/VENUE_CHECK.md."""
import random
import sys

import pandas as pd
import yfinance as yf

sys.path.insert(0, "src")
from tradingagent.config import load_settings  # noqa: E402

SUFFIXES = ["F", "SG", "MU", "DU", "BE", "HM"]
BERLIN = "Europe/Berlin"

settings = load_settings()
de = [i for i in settings.instruments if (i.symbol_for("yahoo") or "").endswith(".DE")]
random.seed(7)
core = [i for i in de if i.tier == "core"]
ext = [i for i in de if i.tier != "core"]
sample = random.sample(core, min(25, len(core))) + random.sample(ext, min(25, len(ext)))


def evening(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    loc = df.index.tz_convert(BERLIN)
    m = loc.hour * 60 + loc.minute
    return df[(m >= 17 * 60 + 30) & (m < 22 * 60) & (loc.weekday < 5)]


rows = []
for inst in sample:
    base = inst.symbol_for("yahoo")[:-3]
    try:
        de_df = yf.Ticker(base + ".DE").history(period="5d", interval="5m")
    except Exception:  # noqa: BLE001
        de_df = pd.DataFrame()
    ref = float(de_df["Close"].iloc[-1]) if de_df is not None and len(de_df) else None
    rec = {"id": inst.id, "name": inst.name[:28], "kind": inst.kind, "tier": inst.tier}
    for suf in SUFFIXES:
        try:
            df = yf.Ticker(f"{base}.{suf}").history(period="5d", interval="5m")
        except Exception:  # noqa: BLE001
            df = pd.DataFrame()
        ev = evening(df)
        days = ev.index.tz_convert(BERLIN).normalize().nunique() if ev is not None and len(ev) else 0
        per_day = round(len(ev) / days, 1) if days else 0.0
        dev = None
        if ref and ev is not None and len(ev):
            dev = round(abs(float(ev["Close"].iloc[0]) / ref - 1) * 100, 2)
        rec[suf] = per_day
        rec[suf + "_dev"] = dev
    rows.append(rec)
    print(rec, flush=True)

res = pd.DataFrame(rows)
lines = ["# Test: Kursverläufe nach Xetra-Schluss bei Yahoo", "",
         f"Stichprobe: {len(sample)} Werte mit Xetra-Kürzel (25 Kernwerte, 25 erweiterte), letzte 5 Handelstage, "
         "Zeitfenster 17:30–22:00 Uhr. Volle Abdeckung wären 54 Kerzen pro Abend.", "",
         "| Börsenplatz | Werte mit Abendkursen | Ø Kerzen pro Abend (wenn vorhanden) | Ø Abweichung zur letzten Xetra-Kerze |",
         "|---|---|---|---|"]
for suf in SUFFIXES:
    have = res[res[suf] > 0]
    lines.append(f"| .{suf} | {len(have)} von {len(res)} | {have[suf].mean() if len(have) else 0:.1f} | "
                 f"{have[suf + '_dev'].mean() if len(have) else float('nan'):.2f} % |")
lines += ["", "## Einzelwerte (Kerzen pro Abend)", "", "| Wert | Art | " + " | ".join("." + s for s in SUFFIXES) + " |",
          "|---|---|" + "---|" * len(SUFFIXES)]
for r in rows:
    lines.append(f"| {r['name']} | {r['kind']}/{r['tier']} | " + " | ".join(str(r[s]) for s in SUFFIXES) + " |")
open("docs/VENUE_CHECK.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines[:12]))
