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


FX_ID = "EURUSD"  # Kurs: US-Dollar je Euro


def to_eur(df: pd.DataFrame, fx_same: pd.DataFrame, fx_daily: pd.DataFrame) -> pd.DataFrame:
    """Rechnet Dollar-Kerzen zum Wechselkurs ihres Zeitpunkts in Euro um.

    Genommen wird der letzte bekannte EUR/USD-Kurs derselben Kerzenlänge,
    sonst der letzte Tageskurs, ganz am Anfang der Historie der erste bekannte.
    """
    if df.empty:
        return df
    rate = pd.Series(float("nan"), index=df.index)
    for fx, tol in ((fx_same, pd.Timedelta(days=4)), (fx_daily, pd.Timedelta(days=14))):
        if fx is None or fx.empty:
            continue
        r = fx["close"].reindex(df.index, method="ffill", tolerance=tol)
        rate = rate.fillna(r)
    if rate.isna().all():
        raise ValueError("Kein EUR/USD-Kurs vorhanden")
    rate = rate.bfill().ffill()
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = df[col] / rate
    return out


class _Loader:
    """Lädt Kerzen eines Werts, bei Dollar-Werten gleich in Euro umgerechnet."""

    def __init__(self, storage: Storage, instrument: Instrument):
        self.storage = storage
        self.inst = instrument
        self.convert = instrument.unit == "$" and instrument.id != FX_ID
        self._fx: dict[str, pd.DataFrame] = {}

    def fx(self, interval: str) -> pd.DataFrame:
        if interval not in self._fx:
            self._fx[interval] = self.storage.load_bars(FX_ID, interval)
        return self._fx[interval]

    def load(self, interval: str, start: pd.Timestamp | None = None) -> pd.DataFrame:
        df = self.storage.load_bars(self.inst.id, interval, start)
        if self.convert and not df.empty:
            df = to_eur(df, self.fx(interval), self.fx("1d"))
        return df

    @property
    def unit(self) -> str:
        return "€" if self.convert else self.inst.unit


def build_series(loader: _Loader, now: pd.Timestamp) -> dict[str, list[list[float]]]:
    tz = MARKET_TZ[loader.inst.market]
    series: dict[str, list[list[float]]] = {}
    for interval, lookback in export_lookbacks().items():
        start = now - lookback
        if interval in DERIVED_FROM:
            src = DERIVED_FROM[interval]
            # etwas mehr laden, damit die erste Woche/der erste Monat vollständig ist
            raw = loader.load(src, start - timedelta(days=35))
            df = resample(raw, interval, tz=tz)
            df = df[df.index >= start - timedelta(days=31)]
        else:
            df = loader.load(interval, start)
        series[interval] = _rows(df)
    return series


def _summary(loader: _Loader) -> dict[str, Any]:
    daily = loader.load("1d")
    intraday = loader.load("15m")
    last_close = None
    last_ts = None
    if not intraday.empty:
        last_close, last_ts = float(intraday["close"].iloc[-1]), intraday.index[-1] + pd.Timedelta(minutes=15)
    elif not daily.empty:
        last_close, last_ts = float(daily["close"].iloc[-1]), daily.index[-1] + pd.Timedelta(days=1)
    change = None
    if last_close is not None and len(daily) >= 2:
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
        loader = _Loader(storage, inst)
        try:
            series = build_series(loader, now)
            summary = _summary(loader)
        except ValueError:  # noch kein Wechselkurs vorhanden: vorerst in Dollar zeigen
            loader.convert = False
            series = build_series(loader, now)
            summary = _summary(loader)
        payload = {
            "id": inst.id,
            "name": inst.name,
            "market": inst.market,
            "delay_minutes": market_cfg.delay_minutes,
            "updated": int(now.timestamp()),
            "converted_from": "USD" if loader.convert else None,
            "series": series,
        }
        (tmp_dir / "s" / f"{file_id}.json").write_text(json.dumps(wrap(payload), separators=(",", ":")), encoding="utf-8")
        entries.append({"id": inst.id, "name": inst.name, "market": inst.market, "file": f"s/{file_id}.json",
                        "unit": loader.unit, "kind": inst.kind, "isin": inst.isin,
                        "converted_from": "USD" if loader.convert else None,
                        "delay_minutes": market_cfg.delay_minutes,
                        "live": {"binance": inst.symbol_for("binance")} if inst.market == "crypto" else None,
                        **summary})

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
