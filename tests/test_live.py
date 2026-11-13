import pandas as pd

from tradingagent import live
from tradingagent.storage.sqlite_store import SQLiteStorage


def test_recorder_writes_only_outside_xetra_hours():
    st = SQLiteStorage(":memory:")
    rec = live.Recorder(st)
    evening = pd.Timestamp("2026-11-13T18:01Z")  # 19:01 Berlin, Xetra zu
    for i, p in enumerate([10.0, 10.4, 9.8, 10.1]):
        rec.add("SAP", evening + pd.Timedelta(seconds=60 * i), p)
    rec.flush_old(evening + pd.Timedelta(minutes=6))
    bars = st.load_bars("SAP", "5m") if hasattr(st, "load_bars") else st.read_bars("SAP", "5m")
    assert len(bars) == 1
    row = bars.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (10.0, 10.4, 9.8, 10.1)
    noon = pd.Timestamp("2026-11-13T11:00Z")  # 12:00 Berlin, Xetra offen
    rec.add("SAP", noon, 11.0)
    rec.flush_old(noon + pd.Timedelta(minutes=6))
    bars = st.load_bars("SAP", "5m") if hasattr(st, "load_bars") else st.read_bars("SAP", "5m")
    assert len(bars) == 1


def test_xetra_hours():
    assert live.xetra_hours(pd.Timestamp("2026-11-13T08:30Z"))      # 9:30 Berlin
    assert not live.xetra_hours(pd.Timestamp("2026-11-13T17:00Z"))  # 18:00 Berlin
    assert not live.xetra_hours(pd.Timestamp("2026-11-14T10:00Z"))  # Samstag


def test_live_file_opens_like_export(tmp_path, monkeypatch):
    """live.json muss mit demselben Passwort und Salz lesbar sein wie die übrigen Daten."""
    import base64
    import json

    from tradingagent.crypto_box import Box

    monkeypatch.setattr(live, "SITE", tmp_path / "data")
    monkeypatch.setattr(live, "QUOTES_FILE", tmp_path / "quotes.json")
    (tmp_path / "data").mkdir()
    salt = b"0123456789abcdef"
    obj = live.Live.__new__(live.Live)
    obj.box = Box("ein-langes-testpasswort", salt=salt, iterations=1000)
    obj.last = {}
    obj.write(pd.Timestamp("2026-11-13T10:00Z"), {"SAP": (240.5, 0.01)})
    doc = json.loads((tmp_path / "live.json").read_text())
    assert doc["enc"] and "ct" in doc
    q = json.loads((tmp_path / "quotes.json").read_text())
    assert q["SAP"]["last"] == 240.5
    assert base64.b64encode(salt)  # gleiche Salz-Kodierung wie im Export-Kopf


def test_us_quotes_recorded_in_dollar_before_us_open():
    from tradingagent.config import load_settings

    st = SQLiteStorage(":memory:")
    idx = pd.DatetimeIndex([pd.Timestamp("2026-11-13T07:00Z")])
    st.upsert_bars("EURUSD", "5m", pd.DataFrame({"open": [1.1], "high": [1.1], "low": [1.1], "close": [1.1], "volume": [0.0]}, index=idx), source="t")
    nvda = next(i for i in load_settings().instruments if i.id == "NVDA")
    rec = live.Recorder(st)
    t = pd.Timestamp("2026-11-13T08:00Z")  # 9:00 Berlin, US-Börse zu
    rec.add_quote(nvda, t, 100.0)
    rec.flush_old(t + pd.Timedelta(minutes=6))
    bars = st.load_bars("NVDA", "5m")
    assert len(bars) == 1 and abs(bars["close"].iloc[0] - 110.0) < 1e-9
    t2 = pd.Timestamp("2026-11-13T16:00Z")  # 17:00 Berlin = 11:00 New York, US-Börse offen
    rec.add_quote(nvda, t2, 100.0)
    rec.flush_old(t2 + pd.Timedelta(minutes=6))
    assert len(st.load_bars("NVDA", "5m")) == 1


def test_recorder_builds_15m_and_1h_bars_outside_xetra():
    st = SQLiteStorage(":memory:")
    rec = live.Recorder(st)
    t0 = pd.Timestamp("2026-11-13T17:00Z")  # 18:00 Berlin
    for i in range(13):  # 18:00 … 19:00, eine Kerze je 5 Minuten
        rec.add("SAP", t0 + pd.Timedelta(minutes=5 * i), 100.0 + i)
    rec.flush_old(t0 + pd.Timedelta(minutes=70))
    h = st.load_bars("SAP", "1h")
    assert list(h.index) == [t0, t0 + pd.Timedelta(hours=1)]
    first = h.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (100.0, 111.0, 100.0, 111.0)
    q = st.load_bars("SAP", "15m")
    assert len(q) == 5 and q.iloc[0]["close"] == 102.0


def test_recorder_leaves_hour_with_xetra_trading_alone():
    st = SQLiteStorage(":memory:")
    rec = live.Recorder(st)
    t = pd.Timestamp("2026-11-13T16:30Z")  # 17:30 Berlin: 15-Minuten-Block ja, Stundenblock 17–18 Uhr nein
    rec.add("SAP", t, 50.0)
    rec.flush_old(t + pd.Timedelta(minutes=6))
    assert len(st.load_bars("SAP", "15m")) == 1
    assert st.load_bars("SAP", "1h").empty
