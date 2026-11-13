"""Fehlende Abendkerzen deutscher Aktien nachträglich mit Kursen der Börse Stuttgart füllen.

Gedacht für Ausfälle des Server-Mitschnitts (z. B. Neustart). Nur Aktien – für ETFs liefert Yahoo
nach Xetra-Schluss keine Kurse. Sicherungen:
  * nur Zeitpunkte, an denen noch keine Kerze gespeichert ist (Tradegate-Kerzen werden nie überschrieben),
  * nur Werte, deren Stuttgarter Kurse am selben Abend nah an den mitgeschriebenen Tradegate-Kursen liegen
    (Median der Abweichung höchstens MAX_DEV), sonst bleibt die Lücke.

Aufruf auf dem Server:
  python -m tradingagent.fill_evening 2026-11-13 17:30 19:00
"""
from __future__ import annotations

import os
import sys

import pandas as pd

from .config import load_settings
from .data.providers.yahoo import _extract
from .live import Recorder
from .storage.sqlite_store import SQLiteStorage

BERLIN = "Europe/Berlin"
MAX_DEV = 0.005  # 0,5 %
MIN_OVERLAP = 3


def candidates(settings) -> list:
    return [i for i in settings.instruments
            if (i.symbol_for("yahoo") or "").endswith(".DE") and i.kind == "aktie" and i.isin]


def download(symbols: list[str], day: pd.Timestamp) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for k in range(0, len(symbols), 100):
        chunk = symbols[k:k + 100]
        try:
            data = yf.download(tickers=chunk, start=day.strftime("%Y-%m-%d"),
                               end=(day + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), interval="5m",
                               group_by="ticker", auto_adjust=True, actions=False, threads=True, progress=False)
        except Exception as err:  # noqa: BLE001
            print(f"Abruf fehlgeschlagen ({len(chunk)} Werte): {err}", flush=True)
            continue
        for sym in chunk:
            out[sym] = _extract(data, sym)
    return out


def fill(storage, items, frames: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Schreibt fehlende 5-Minuten-Kerzen im Zeitraum [start, end) und gibt eine Übersicht zurück."""
    rec = Recorder(storage)
    stats = {"filled_values": 0, "bars": 0, "no_data": 0, "no_check": 0, "too_far": 0}
    day_end = start.tz_convert(BERLIN).normalize() + pd.Timedelta(hours=22)
    for inst in items:
        sg = frames.get(inst.symbol_for("yahoo")[:-3] + ".SG")
        if sg is None or sg.empty:
            stats["no_data"] += 1
            continue
        sg = sg[~sg.index.duplicated()]
        # Prüfung: dieselben Zeitpunkte, an denen Tradegate mitgeschrieben wurde
        tg = storage.load_bars(inst.id, "5m", start, day_end.tz_convert("UTC"))
        both = tg.index.intersection(sg.index)
        if len(both) < MIN_OVERLAP:
            stats["no_check"] += 1
            continue
        dev = ((sg.loc[both, "close"] / tg.loc[both, "close"]) - 1).abs().median()
        if not dev <= MAX_DEV:
            stats["too_far"] += 1
            continue
        want = sg[(sg.index >= start) & (sg.index < end)]
        have = storage.load_bars(inst.id, "5m", start, end).index
        new = want[~want.index.isin(have)]
        if new.empty:
            continue
        storage.upsert_bars(inst.id, "5m", new, source="stuttgart")
        for t in new.index:
            rec._roll_up(inst.id, t)
        stats["filled_values"] += 1
        stats["bars"] += len(new)
    return stats


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    day = pd.Timestamp(argv[0])
    start = pd.Timestamp(f"{argv[0]} {argv[1]}", tz=BERLIN).tz_convert("UTC")
    end = pd.Timestamp(f"{argv[0]} {argv[2]}", tz=BERLIN).tz_convert("UTC")
    settings = load_settings()
    storage = SQLiteStorage(os.environ.get("TA_STORAGE_PATH", str(settings.storage_path)))
    items = candidates(settings)
    print(f"{len(items)} deutsche Aktien, hole Stuttgarter 5-Minuten-Kurse für {argv[0]} …", flush=True)
    frames = download([i.symbol_for("yahoo")[:-3] + ".SG" for i in items], day)
    s = fill(storage, items, frames, start, end)
    print(f"Gefüllt: {s['bars']} Kerzen bei {s['filled_values']} Werten. Übersprungen: {s['no_data']} ohne Stuttgarter Kurse, "
          f"{s['no_check']} ohne Vergleich mit Tradegate, {s['too_far']} mit zu großer Abweichung.")
    print("Die App zeigt die Kerzen nach dem nächsten Sammellauf (spätestens in 5 Minuten).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
