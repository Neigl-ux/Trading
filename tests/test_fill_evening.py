import numpy as np
import pandas as pd

from tradingagent import fill_evening
from tradingagent.config import Instrument
from tradingagent.storage.sqlite_store import SQLiteStorage


def _bars(idx, base):
    c = np.full(len(idx), base, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 0.0}, index=idx)


def test_fills_only_gap_and_only_when_close_to_tradegate():
    st = SQLiteStorage(":memory:")
    sap = Instrument("SAP", "SAP", "xetra", {"yahoo": "SAP.DE"}, kind="aktie", isin="DE0007164600")
    bay = Instrument("BAYN", "Bayer", "xetra", {"yahoo": "BAYN.DE"}, kind="aktie", isin="DE000BAY0017")
    tg_idx = pd.date_range("2026-11-13T18:00Z", "2026-11-13T18:30Z", freq="5min")  # 19:00–19:30 Berlin
    st.upsert_bars("SAP", "5m", _bars(tg_idx, 200.0), "tradegate")
    st.upsert_bars("BAYN", "5m", _bars(tg_idx, 30.0), "tradegate")
    sg_idx = pd.date_range("2026-11-13T16:30Z", "2026-11-13T18:30Z", freq="5min")
    frames = {"SAP.SG": _bars(sg_idx, 200.4), "BAYN.SG": _bars(sg_idx, 31.0)}  # Bayer 3 % daneben
    start, end = pd.Timestamp("2026-11-13T16:30Z"), pd.Timestamp("2026-11-13T18:00Z")
    s = fill_evening.fill(st, [sap, bay], frames, start, end)
    assert s["filled_values"] == 1 and s["too_far"] == 1 and s["bars"] == 18
    sap5 = st.load_bars("SAP", "5m")
    assert sap5.loc[pd.Timestamp("2026-11-13T18:00Z"), "close"] == 200.0  # Tradegate bleibt
    assert st.load_bars("BAYN", "5m", start, end).empty
    assert len(st.load_bars("SAP", "1h", start, end)) == 1  # 18–19 Uhr Berlin; 17–18 Uhr enthält Xetra-Zeit
