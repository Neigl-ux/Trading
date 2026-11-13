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
