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


def test_quotes_number_parsing_and_session():
    from tradingagent.quotes import last_session_end, parse_number, tradegate_open
    assert parse_number("1 371,80") == 1371.8 and parse_number(212.75) == 212.75 and parse_number("-") is None
    sat = pd.Timestamp("2026-11-14 12:00", tz="UTC")
    assert not tradegate_open(sat)
    assert last_session_end(sat) == pd.Timestamp("2026-11-13 22:00", tz="Europe/Berlin").tz_convert("UTC")
    assert tradegate_open(pd.Timestamp("2026-11-12 19:36", tz="UTC"))


def test_summary_uses_tradegate_and_day_reference(settings):
    from tradingagent.export import _Loader, _summary
    from tradingagent.quotes import Quote
    from tradingagent.config import Instrument
    storage = SQLiteStorage(":memory:")
    # Xetra-Kerzen: gestern Schluss 200, heute bis 17:30 Schluss 208,30
    y = pd.date_range("2026-11-11 08:00", "2026-11-11 16:25", freq="5min", tz="UTC")
    t = pd.date_range("2026-11-12 08:00", "2026-11-12 16:25", freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 300.0, "low": 1.0, "close": 205.0, "volume": 1.0}, index=y.append(t))
    df.loc[y[-1], "close"] = 200.0
    df.loc[t[-1], "close"] = 208.30
    storage.upsert_bars("AIR", "5m", df, "yahoo")
    inst = Instrument("AIR", "Airbus", "xetra", {"yahoo": "AIR.DE"}, isin="NL0000235190")
    now = pd.Timestamp("2026-11-12 19:36", tz="UTC")
    plain = _summary(_Loader(storage, inst), now)
    assert plain["last"] == 208.3 and plain["prev_close"] == 200.0
    live = _summary(_Loader(storage, inst), now, Quote(206.85, now))
    assert live["last"] == 206.85 and live["last_source"] == "tradegate"
    assert live["change_1d"] == pytest.approx(206.85 / 200 - 1)


def test_live_symbols_finnhub_and_binance():
    from tradingagent.config import Instrument
    from tradingagent.export import live_symbols
    us = Instrument(id="BRK-B", name="Berkshire", market="us", symbols={"yahoo": "BRK-B"}, unit="$")
    assert live_symbols(us) == {"finnhub": "BRK.B"}
    idx = Instrument(id="SPX", name="S&P 500", market="us", symbols={"yahoo": "^GSPC"}, kind="index")
    assert live_symbols(idx) is None
    btc = Instrument(id="BTC-EUR", name="Bitcoin", market="crypto", symbols={"binance": "BTCEUR"}, kind="krypto")
    assert live_symbols(btc) == {"binance": "BTCEUR"}
    sap = Instrument(id="SAP", name="SAP", market="xetra", symbols={"yahoo": "SAP.DE"})
    assert live_symbols(sap) is None


def test_universe_merges_without_duplicates(tmp_path):
    import shutil
    from tradingagent.config import CONFIG_DIR, load_settings
    shutil.copy(CONFIG_DIR / "settings.yaml", tmp_path / "settings.yaml")
    shutil.copy(CONFIG_DIR / "instruments.yaml", tmp_path / "instruments.yaml")
    (tmp_path / "universe.yaml").write_text(
        "instruments:\n"
        "  - {id: SAP-X, name: SAP doppelt, market: xetra, kind: aktie, isin: DE0007164600, yahoo: SAP.DE}\n"
        "  - {id: HLAG, name: Hapag-Lloyd, market: xetra, kind: aktie, isin: DE000HLAG475, wkn: HLAG47, yahoo: HLAG.DE}\n",
        encoding="utf-8")
    s = load_settings(tmp_path, tmp_path)
    ids = [i.id for i in s.instruments]
    assert "SAP-X" not in ids  # gleiche ISIN wie der handverlesene Wert
    hlag = next(i for i in s.instruments if i.id == "HLAG")
    assert hlag.tier == "extended" and hlag.wkn == "HLAG47"
    assert s.tiers["extended"]["max_new_per_run"] > 0


def test_extended_tier_limits_first_backfill(tmp_path):
    from tradingagent.collector import collect
    from tradingagent.config import Instrument, MarketConfig, Settings
    from tradingagent.storage import SQLiteStorage
    from tradingagent.data.providers.base import MarketDataProvider
    import pandas as pd

    class Flat(MarketDataProvider):
        name = "yahoo"
        intervals = frozenset({"5m", "1h", "1d"})
        max_history = {}

        def fetch(self, symbol, interval, start, now):
            idx = pd.date_range(now - pd.Timedelta(days=2), now - pd.Timedelta(hours=1), freq="1h", tz="UTC")
            return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    insts = [Instrument(id=f"X{i}", name="x", market="fx", symbols={"yahoo": f"X{i}"}, tier="extended") for i in range(5)]
    settings = Settings(storage_path=tmp_path / "db.sqlite", export_dir=tmp_path / "out", encrypt=False,
                        markets={"fx": MarketConfig("fx", True, ["yahoo"], ["5m", "1h"], ["yahoo"])},
                        backfill_days={}, daily_full_refresh_days=7, overlap_bars=3, instruments=insts,
                        tiers={"extended": {"intervals": ["1h"], "max_new_per_run": 2}})
    store = SQLiteStorage(settings.storage_path)
    now = pd.Timestamp("2026-11-11 12:00", tz="UTC")
    s = collect(settings, store, {"yahoo": Flat()}, now=now)
    loaded = {log.instrument for log in s.logs if log.skipped is None and log.interval == "1h"}
    assert loaded == {"X0", "X1"} and any("folgen" in n for n in s.notes)
    # Tageskerzen sofort für alle
    assert {log.instrument for log in s.logs if log.skipped is None and log.interval == "1d"} == {f"X{i}" for i in range(5)}
    s2 = collect(settings, store, {"yahoo": Flat()}, now=now + pd.Timedelta(minutes=15))
    assert {"X2", "X3"} <= {log.instrument for log in s2.logs if log.skipped is None}


def test_daily_gap_repair_reaches_back(tmp_path):
    """Erster Lauf des Tages holt die feinen Kerzen der letzten Tage erneut (Lücken füllen)."""
    import pandas as pd
    from tradingagent import collector
    from tradingagent.config import load_settings
    from tradingagent.storage.sqlite_store import SQLiteStorage

    s = load_settings()
    st = SQLiteStorage(tmp_path / "db.sqlite")
    inst = next(i for i in s.instruments if i.tier == "core" and i.market != "krypto")
    cfg = s.markets[inst.market]
    now = pd.Timestamp("2026-11-13T17:00Z")
    idx = pd.date_range(now - pd.Timedelta(hours=2), periods=12, freq="5min")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    st.upsert_bars(inst.id, "5m", df, source="test")
    normal = collector._plan(s, st, inst, "5m", cfg, now, repair=False)
    fixed = collector._plan(s, st, inst, "5m", cfg, now, repair=True)
    if normal.skip:
        return
    assert fixed.start <= now - pd.Timedelta(days=3) < normal.start


def test_us_hourly_bars_at_half_past_are_not_gaps():
    import pandas as pd
    from tradingagent.data.quality import QualityReport, check_gaps

    days = pd.bdate_range("2026-11-09", "2026-11-13")
    idx = [pd.Timestamp(f"{d.date()} {h}:30", tz="America/New_York").tz_convert("UTC") for d in days for h in range(9, 16)]
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=pd.DatetimeIndex(idx))
    rep = QualityReport("AAPL", "1h")
    check_gaps(df, "AAPL", "1h", "us", rep)
    assert not [i for i in rep.issues if i.kind == "luecke"]


def test_collect_state_file(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from tradingagent import __main__ as m

    monkeypatch.setenv("TA_QUOTES_FILE", str(tmp_path / "q.json"))
    s = SimpleNamespace(export_dir=tmp_path / "site" / "data")
    m._collect_state(s, running=True, started=100)
    m._collect_state(s, running=False, finished=400, duration=300)
    st = json.loads((tmp_path / "site" / "collect.json").read_text())
    assert st["started"] == 100 and st["duration"] == 300 and st["next"] == 700 and st["running"] is False
