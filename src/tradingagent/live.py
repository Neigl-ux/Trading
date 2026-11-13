"""Live-Dienst für den Server (systemd „ta-radar“): alles, was laufend passieren muss.

Jede Runde (etwa jede Minute):
  1. Tradegate-Kurse ALLER Werte mit ISIN abfragen (während 7:30–22 Uhr), parallel.
  2. Für die App eine verschlüsselte Live-Datei schreiben (site/data/live.json): ein einziger
     Abruf liefert der App alle Kurse, statt dass das Handy selbst Hunderte Abfragen macht.
  3. Für den Sammler die Kurse als quotes.json ablegen (er muss Tradegate dann nicht selbst fragen).
  4. Kurse deutscher Werte außerhalb der Xetra-Zeit (8–9 und 17:30–22 Uhr) als 5-Minuten-Kerzen
     mitschreiben. Xetra liefert dafür keine Daten, Tradegate handelt aber.
  5. Liveradar: starke Bewegungen und Nachkauf-Stufen per Push (siehe radar.py).
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import Instrument, load_settings
from .crypto_box import Box
from .quotes import URL as TG_URL, parse_number
from .radar import BERLIN, Radar, tradegate_open

SITE = Path(os.environ.get("TA_EXPORT_DIR", "/var/lib/trading-agent/site/data"))
QUOTES_FILE = Path(os.environ.get("TA_QUOTES_FILE", "/var/lib/trading-agent/quotes.json"))
WORKERS = int(os.environ.get("TA_LIVE_WORKERS", "6"))


def xetra_hours(ts: pd.Timestamp) -> bool:
    loc = ts.tz_convert("Europe/Berlin")
    m = loc.hour * 60 + loc.minute
    return loc.weekday() < 5 and 9 * 60 <= m < 17 * 60 + 30


def recordable(inst: Instrument) -> bool:
    """Nur Werte, deren gespeicherte Kurse in Euro von Xetra stammen (Yahoo-Kürzel .DE)."""
    y = inst.symbol_for("yahoo") or ""
    return bool(inst.isin) and y.endswith(".DE")


class Recorder:
    """Sammelt Tradegate-Kurse zu 5-Minuten-Kerzen und speichert fertige Kerzen."""

    def __init__(self, storage):
        self.storage = storage
        self.open: dict[str, dict[str, Any]] = {}

    def add(self, vid: str, ts: pd.Timestamp, price: float) -> None:
        bucket = ts.floor("5min")
        cur = self.open.get(vid)
        if cur and cur["t"] != bucket:
            self._flush(vid, cur)
            cur = None
        if cur is None:
            self.open[vid] = {"t": bucket, "o": price, "h": price, "l": price, "c": price}
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], price), min(cur["l"], price), price

    def flush_old(self, now: pd.Timestamp) -> None:
        for vid, cur in list(self.open.items()):
            if cur["t"] + pd.Timedelta(minutes=5) <= now:
                self._flush(vid, cur)
                del self.open[vid]

    def _flush(self, vid: str, cur: dict[str, Any]) -> None:
        if xetra_hours(cur["t"]):
            return  # während der Xetra-Zeit gelten die Xetra-Kerzen
        df = pd.DataFrame({"open": [cur["o"]], "high": [cur["h"]], "low": [cur["l"]], "close": [cur["c"]], "volume": [0.0]},
                          index=pd.DatetimeIndex([cur["t"]]))
        try:
            self.storage.upsert_bars(vid, "5m", df, source="tradegate")
        except Exception as err:  # noqa: BLE001 – Datenbank kurz gesperrt: Kerze fehlt, Lücken-Reparatur hilft nicht, aber der Dienst läuft weiter
            print(f"Mitschnitt {vid}: {err}", flush=True)


class Live:
    def __init__(self):
        self.settings = load_settings()
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "Mozilla/5.0 (trading-agent live)"
        self.items = [i for i in self.settings.instruments
                      if i.isin and self.settings.markets[i.market].enabled and i.market != "crypto"]
        self.radar = Radar(session=self.http)
        self.box = None
        pw = os.environ.get("APP_PASSPHRASE")
        salt = os.environ.get("TA_EXPORT_SALT_B64")
        if pw and salt:
            import base64

            self.box = Box(pw, salt=base64.b64decode(salt))
        self.recorder = None
        if os.environ.get("TA_STORAGE_PATH"):
            from .storage.sqlite_store import SQLiteStorage

            self.recorder = Recorder(SQLiteStorage(os.environ["TA_STORAGE_PATH"]))
        self.last: dict[str, dict[str, Any]] = {}

    def sweep(self) -> dict[str, tuple[float, float | None]]:
        out: dict[str, tuple[float, float | None]] = {}

        def one(inst: Instrument):
            try:
                r = self.http.get(TG_URL, params={"isin": inst.isin}, timeout=10)
                data = r.json() if r.status_code == 200 else None
            except (requests.RequestException, ValueError):
                return
            if not isinstance(data, dict):
                return
            last = parse_number(data.get("last"))
            if last is None or last <= 0:
                return
            delta = parse_number(str(data.get("delta", "")).replace("%", "").replace("+", ""))
            out[inst.id] = (last, delta / 100 if delta is not None else None)

        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(one, self.items))
        return out

    def write(self, now: pd.Timestamp, quotes: dict[str, tuple[float, float | None]]) -> None:
        ts = int(now.timestamp())
        for vid, (price, delta) in quotes.items():
            self.last[vid] = {"last": price, "time": ts, "d": delta}
        # für den Sammler (nur lokal)
        tmp = QUOTES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: {"last": v["last"], "time": v["time"]} for k, v in self.last.items()}), encoding="utf-8")
        tmp.replace(QUOTES_FILE)
        # für die App (verschlüsselt wie alle Daten)
        if self.box and SITE.parent.exists():
            payload = {"updated": ts, "open": tradegate_open(now.to_pydatetime()),
                       "q": {k: [v["last"], v["time"]] for k, v in self.last.items()}}
            doc = self.box.seal(payload)
            # neben dem Datenordner, weil der Sammler den Datenordner bei jedem Export ersetzt
            tmp = SITE.parent / "live.json.tmp"
            tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
            tmp.replace(SITE.parent / "live.json")

    def tick(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        dt = datetime.now(BERLIN)
        quotes: dict[str, tuple[float, float | None]] = {}
        if tradegate_open(dt):
            quotes = self.sweep()
            self.write(now, quotes)
            if self.recorder:
                for vid, (price, _) in quotes.items():
                    inst = self.radar.watch.get(vid)
                    inst = inst.inst if inst else next((i for i in self.items if i.id == vid), None)
                    if inst is not None and recordable(inst):
                        self.recorder.add(vid, now, price)
        if self.recorder:
            self.recorder.flush_old(now)
        # Radar: dieselben Kurse, dazu Krypto und Indizes
        self.radar.tick(dt, tradegate=quotes)


def main() -> int:
    live = Live()
    print(f"Live-Dienst: {len(live.items)} Tradegate-Werte, Radar {len(live.radar.watch)} Werte, "
          f"Live-Datei {'an' if live.box else 'aus (APP_PASSPHRASE/Salz fehlt)'}, "
          f"Mitschnitt {'an' if live.recorder else 'aus'}", flush=True)
    while True:
        start = time.time()
        try:
            live.tick()
        except Exception as err:  # noqa: BLE001 – der Dienst läuft weiter
            print(f"Fehler: {err}", flush=True)
        time.sleep(max(5.0, 60 - (time.time() - start)))


if __name__ == "__main__":
    raise SystemExit(main())
