"""Ende-zu-Ende: sammeln, speichern, exportieren, entschlüsseln."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tradingagent.collector import collect
from tradingagent.config import load_settings
from tradingagent.crypto_box import Box, open_sealed
from tradingagent.data.providers.base import MarketDataProvider, ProviderError
from tradingagent.export import export_all
from tradingagent.storage import SQLiteStorage

from .conftest import make_bars

PASS = "ein-langes-test-passwort"


class FakeProvider(MarketDataProvider):
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.intervals = frozenset({"15m", "1d"})
        self.max_history = {}
        self.calls = []

    def fetch(self, symbol, interval, start, now):
        self.calls.append((symbol, interval, start))
        if self.fail:
            raise ProviderError("gesperrt (451)")
        freq = "15min" if interval == "15m" else "1D"
        n = int((now - start) / pd.Timedelta(freq)) + 1
        df = make_bars(start.floor(freq), max(n, 1), freq)
        return df[df.index <= now]


def test_collect_store_export_roundtrip(settings):
    cfg_dir, root = settings
    s = load_settings(cfg_dir, root)
    storage = SQLiteStorage(s.storage_path)
    providers = {"fakea": FakeProvider("fakea", fail=True), "fakeb": FakeProvider("fakeb")}
    now = pd.Timestamp("2026-01-07 12:07", tz="UTC")  # Mittwoch, Xetra offen

    summary = collect(s, storage, providers, now=now)
    d = summary.as_dict()
    assert d["failed"] == 0 and d["new_bars"] > 0
    # Krypto fiel auf die zweite Quelle zurück
    assert storage.last_ts("BTC-EUR", "15m") is not None
    # keine unfertige Kerze gespeichert, Xetra mit 15 Min Verzögerung
    assert storage.last_ts("BTC-EUR", "15m") + pd.Timedelta("15min") <= now
    assert storage.last_ts("SAP", "15m") + pd.Timedelta("15min") <= now - pd.Timedelta("15min")

    # zweiter Lauf lädt inkrementell mit Überlappung
    providers["fakeb"].calls.clear()
    collect(s, storage, providers, now=now + pd.Timedelta(minutes=15))
    starts = {c[1]: c[2] for c in providers["fakeb"].calls if c[0] == "BTC-EUR"}
    assert starts["15m"] >= now - pd.Timedelta(hours=1)

    out = export_all(s, storage, now, status=d, passphrase=PASS)
    index = json.loads((out / "index.json").read_text())
    assert index["enc"] is True and "kdf" in index
    payload = open_sealed(PASS, index["kdf"], index)
    ids = [i["id"] for i in payload["instruments"]]
    assert ids == ["BTC-EUR", "SAP"]
    assert len(payload["views"]) == 15
    # Dateinamen verraten das Instrument nicht
    assert "BTC" not in payload["instruments"][0]["file"]
    sym = json.loads((out / payload["instruments"][0]["file"]).read_text())
    data = open_sealed(PASS, index["kdf"], sym)
    assert set(data["series"]) >= {"5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w", "1M"}
    assert len(data["series"]["15m"]) > 0 and len(data["series"]["1d"]) > 300
    # Klartext steht nirgends in den Dateien
    raw_all = "".join(p.read_text() for p in out.rglob("*.json"))
    assert '"Bitcoin"' not in raw_all and '"SAP"' not in raw_all and '"series"' not in raw_all


def test_export_refuses_without_passphrase(settings):
    cfg_dir, root = settings
    s = load_settings(cfg_dir, root)
    with pytest.raises(RuntimeError, match="APP_PASSPHRASE"):
        export_all(s, SQLiteStorage(":memory:"), pd.Timestamp.now(tz="UTC"), status={}, passphrase=None)


def test_xetra_skipped_on_weekend_after_fetch(settings):
    cfg_dir, root = settings
    s = load_settings(cfg_dir, root)
    storage = SQLiteStorage(":memory:")
    providers = {"fakea": FakeProvider("fakea"), "fakeb": FakeProvider("fakeb")}
    collect(s, storage, providers, now=pd.Timestamp("2026-01-10 10:00", tz="UTC"))  # Samstag
    summary = collect(s, storage, providers, now=pd.Timestamp("2026-01-10 10:15", tz="UTC"))
    sap = [log for log in summary.logs if log.instrument == "SAP" and log.interval == "15m"][0]
    assert sap.skipped


def test_box_rejects_short_passphrase():
    with pytest.raises(ValueError):
        Box("kurz")


def test_all_failed_flag(settings):
    cfg_dir, root = settings
    s = load_settings(cfg_dir, root)
    providers = {"fakea": FakeProvider("fakea", fail=True), "fakeb": FakeProvider("fakeb", fail=True)}
    summary = collect(s, SQLiteStorage(":memory:"), providers, now=pd.Timestamp("2026-01-07 12:00", tz="UTC"))
    assert summary.all_failed


class BatchFake(FakeProvider):
    def __init__(self, name):
        super().__init__(name)
        self.prefetched = []

    def prefetch(self, symbols, interval, start, now):
        self.prefetched.append((tuple(symbols), interval))


def test_prefetch_groups_and_refresh_throttle(settings):
    cfg_dir, root = settings
    s = load_settings(cfg_dir, root)
    object.__setattr__(s, "min_refresh_minutes", {"1d": 60})
    storage = SQLiteStorage(":memory:")
    providers = {"fakea": FakeProvider("fakea"), "fakeb": BatchFake("fakeb")}
    now = pd.Timestamp("2026-01-07 12:07", tz="UTC")
    collect(s, storage, providers, now=now)
    # fakeb ist erste Quelle für Xetra (15m) und für Tageskerzen beider Märkte
    assert ("SAP.DE",) in [p[0] for p in providers["fakeb"].prefetched if p[1] == "15m"]
    daily = [p for p in providers["fakeb"].prefetched if p[1] == "1d"][0]
    assert set(daily[0]) == {"BTC-EUR", "SAP.DE"}
    # 15 Minuten später: Tageskerzen werden noch nicht erneut geholt
    summary = collect(s, storage, providers, now=now + pd.Timedelta(minutes=15))
    skipped = {(l.instrument, l.interval): l.skipped for l in summary.logs}
    assert skipped[("SAP", "1d")] == "kürzlich abgerufen"
    assert skipped[("SAP", "15m")] is None


def test_to_eur_uses_rate_at_candle_time():
    from tradingagent.export import to_eur
    idx = pd.date_range("2026-01-05 14:00", periods=4, freq="15min", tz="UTC")
    usd = pd.DataFrame({"open": [110.0] * 4, "high": [121.0] * 4, "low": [99.0] * 4, "close": [110.0] * 4,
                        "volume": [5.0] * 4}, index=idx)
    fx = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": [1.10, 1.10, 1.25, 1.25], "volume": 0.0}, index=idx)
    daily = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [1.0], "volume": 0.0},
                         index=pd.DatetimeIndex(["2026-01-01"], tz="UTC"))
    eur = to_eur(usd, fx, daily)
    assert eur["close"].round(4).tolist() == [100.0, 100.0, 88.0, 88.0]
    assert eur["volume"].tolist() == [5.0] * 4
    # ohne passenden Intraday-Kurs greift der Tageskurs
    eur2 = to_eur(usd, pd.DataFrame(), daily)
    assert eur2["close"].iloc[0] == pytest.approx(110.0)
    with pytest.raises(ValueError):
        to_eur(usd, pd.DataFrame(), pd.DataFrame())
