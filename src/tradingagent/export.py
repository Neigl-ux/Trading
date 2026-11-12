"""Export der Kursdaten für die Handy-App.

Ergebnis im Export-Ordner:
- index.json         Übersicht (verschlüsselt): Instrumente, letzte Kurse, Status
- s/<kennung>.json   je Instrument alle Kerzenreihen für die 14 Chart-Ansichten

Die Dateinamen sind zufällig je Lauf, damit sie nichts über die
Beobachtungsliste verraten. Rohdaten von Yahoo werden nur verschlüsselt
veröffentlicht.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Instrument, Settings
from .crypto_box import Box
from .data.resample import resample
from .data.sessions import MARKET_TZ
from .data.timeframes import CHART_VIEWS, DERIVED_FROM, export_lookbacks
from .storage import Storage


def _rows(df: pd.DataFrame) -> list[list[float]]:
    out = []
    for ts, r in df.iterrows():
        out.append([int(ts.timestamp()), _r(r.open), _r(r.high), _r(r.low), _r(r.close), round(float(r.volume), 4)])
    return out


def _r(x: float) -> float:
    x = float(x)
    if abs(x) >= 1000:
        return round(x, 2)
    if abs(x) >= 1:
        return round(x, 4)
    return float(f"{x:.6g}")


def build_series(storage: Storage, instrument: Instrument, now: pd.Timestamp) -> dict[str, list[list[float]]]:
    tz = MARKET_TZ[instrument.market]
    series: dict[str, list[list[float]]] = {}
    for interval, lookback in export_lookbacks().items():
        start = now - lookback
        if interval in DERIVED_FROM:
            src = DERIVED_FROM[interval]
            # etwas mehr laden, damit die erste Woche/der erste Monat vollständig ist
            raw = storage.load_bars(instrument.id, src, start - timedelta(days=35))
            df = resample(raw, interval, tz=tz)
            df = df[df.index >= start - timedelta(days=31)]
        else:
            df = storage.load_bars(instrument.id, interval, start)
        series[interval] = _rows(df)
    return series


def _summary(storage: Storage, instrument: Instrument) -> dict[str, Any]:
    daily = storage.load_bars(instrument.id, "1d")
    intraday = storage.load_bars(instrument.id, "15m")
    last_close = None
    last_ts = None
    if not intraday.empty:
        last_close, last_ts = float(intraday["close"].iloc[-1]), intraday.index[-1] + pd.Timedelta(minutes=15)
    elif not daily.empty:
        last_close, last_ts = float(daily["close"].iloc[-1]), daily.index[-1] + pd.Timedelta(days=1)
    prev_close = float(daily["close"].iloc[-2]) if len(daily) >= 2 else None
    change = None
    if last_close is not None and prev_close:
        # Vergleich mit dem letzten abgeschlossenen Tagesschluss vor heute
        ref = daily[daily.index + pd.Timedelta(days=1) <= (last_ts or daily.index[-1])]
        if len(ref):
            change = last_close / float(ref["close"].iloc[-1]) - 1
    return {
        "last": None if last_close is None else _r(last_close),
        "last_ts": None if last_ts is None else int(last_ts.timestamp()),
        "change_1d": None if change is None else round(change, 5),
    }


def export_all(settings: Settings, storage: Storage, now: pd.Timestamp, *, status: dict[str, Any],
               passphrase: str | None) -> Path:
    """Schreibt alle Dateien für die App. Gibt den Export-Ordner zurück."""
    out_dir = settings.export_dir
    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / "s").mkdir(parents=True)

    box: Box | None = None
    if settings.encrypt:
        if not passphrase:
            raise RuntimeError(
                "Export ist auf Verschlüsselung eingestellt, aber APP_PASSPHRASE fehlt. "
                "Lege das Passwort als GitHub-Secret bzw. Umgebungsvariable an."
            )
        # Salt bleibt über alle Läufe gleich, damit die App den abgeleiteten
        # Schlüssel merken kann und nicht bei jedem Öffnen neu rechnen muss.
        salt_hex_stored = storage.get_meta("export_salt")
        salt = bytes.fromhex(salt_hex_stored) if salt_hex_stored else os.urandom(16)
        if not salt_hex_stored:
            storage.set_meta("export_salt", salt.hex())
        box = Box(passphrase, salt=salt)
    # Dateinamen-Kennungen wechseln bei jedem Lauf
    salt_hex = os.urandom(8).hex()

    def wrap(payload: Any) -> dict[str, Any]:
        return box.seal(payload) if box else {"v": 1, "enc": False, "data": payload}

    entries = []
    for inst in settings.instruments:
        if not settings.markets[inst.market].enabled:
            continue
        file_id = hashlib.sha256(f"{salt_hex}:{inst.id}".encode()).hexdigest()[:20]
        market_cfg = settings.markets[inst.market]
        payload = {
            "id": inst.id,
            "name": inst.name,
            "market": inst.market,
            "delay_minutes": market_cfg.delay_minutes,
            "updated": int(now.timestamp()),
            "series": build_series(storage, inst, now),
        }
        (tmp_dir / "s" / f"{file_id}.json").write_text(json.dumps(wrap(payload), separators=(",", ":")), encoding="utf-8")
        entries.append({"id": inst.id, "name": inst.name, "market": inst.market, "file": f"s/{file_id}.json",
                        "unit": inst.unit, "kind": inst.kind,
                        "delay_minutes": market_cfg.delay_minutes,
                        "live": {"binance": inst.symbol_for("binance")} if inst.market == "crypto" else None,
                        **_summary(storage, inst)})

    index_payload = {
        "updated": int(now.timestamp()),
        "instruments": entries,
        "views": [{"key": v.key, "label": v.label, "interval": v.interval,
                   "lookback_s": int(v.lookback.total_seconds())} for v in CHART_VIEWS],
        "status": status,
    }
    index = wrap(index_payload)
    if box:
        index["kdf"] = box.kdf_header()
    (tmp_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)
    return out_dir
