"""Kennzahlen für Top/Flop und Screener: gegen unabhängig nachgerechnete Werte prüfen."""

import numpy as np
import pandas as pd
import pytest

from tradingagent import metrics as M


class FakeLoader:
    def __init__(self, frames):
        self.frames = frames

    def load(self, interval, start=None):
        df = self.frames.get(interval, pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
        if start is not None and not df.empty:
            df = df[df.index >= start]
        return df


def bars(start, step_s, closes, volume=10.0):
    idx = pd.date_range(start, periods=len(closes), freq=f"{step_s}s", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": volume}, index=idx)


NOW = pd.Timestamp("2026-11-12 20:00", tz="UTC")


def crypto_frames():
    # 5m-Kerzen über 3 Tage, linear von 100 auf 130 steigend
    n5 = 3 * 288
    f5 = bars(NOW - pd.Timedelta(minutes=5 * n5), 300, np.linspace(100, 130, n5))
    f1h = bars(NOW - pd.Timedelta(hours=24 * 40), 3600, np.linspace(50, 130, 24 * 40))
    d = 460
    f1d = bars((NOW - pd.Timedelta(days=d)).normalize(), 86400, np.linspace(40, 130, d), volume=1000)
    return {"5m": f5, "15m": f5.iloc[0:0], "1h": f1h, "1d": f1d}


def test_perf_windows_match_manual_reference():
    frames = crypto_frames()
    last = 131.0
    out = M.compute(FakeLoader(frames), NOW, last, NOW, crypto=True)
    f5 = frames["5m"]
    for name, secs in (("5m", 300), ("1h", 3600), ("24h", 86400)):
        T = NOW - pd.Timedelta(seconds=secs)
        ref = f5[f5.index + pd.Timedelta(minutes=5) <= T]["close"].iloc[-1]
        assert out["perf"][name] == pytest.approx(last / ref - 1, abs=1e-6)
        assert out["abs"][name] == pytest.approx(last - ref, rel=1e-4)
    # 7 Tage: aus der Stundenreihe
    f1h = frames["1h"]
    T = NOW - pd.Timedelta(days=7)
    ref = f1h[f1h.index + pd.Timedelta(hours=1) <= T]["close"].iloc[-1]
    assert out["perf"]["7d"] == pytest.approx(last / ref - 1, abs=1e-6)


def test_missing_data_is_none_not_zero():
    # Nur Tageskerzen der letzten 10 Tage: kurze Fenster und Indikatoren nicht berechenbar
    f1d = bars((NOW - pd.Timedelta(days=10)).normalize(), 86400, np.linspace(10, 11, 10), volume=0)
    out = M.compute(FakeLoader({"1d": f1d}), NOW, 11.0, NOW)
    assert "5m" not in out["perf"] and "24h" not in out["perf"]
    assert "rsi14" not in out["ind"] and "ema200" not in out["ind"]
    assert out.get("turnover_24h") is None and out.get("rel_volume") is None


def test_delayed_series_does_not_fake_short_windows():
    # 5m-Reihe endet 40 Minuten vor dem Kurszeitpunkt (verzögert): 5m/15m nicht berechenbar
    f5 = bars(NOW - pd.Timedelta(days=2), 300, np.linspace(100, 110, 2 * 288 - 8))
    out = M.compute(FakeLoader({"5m": f5}), NOW, 111.0, NOW)
    assert "5m" not in out["perf"] and "15m" not in out["perf"]
    assert "24h" in out["perf"]


def test_rsi_matches_wilder_reference():
    rng = np.random.default_rng(1)
    s = pd.Series(100 + rng.normal(0, 1, 120).cumsum())
    # unabhängige Schleifen-Rechnung nach Wilder (geglättet mit alpha=1/14, Start mit erstem Wert)
    d = np.diff(s.values)
    ag, al = max(d[0], 0), max(-d[0], 0)
    for x in d[1:]:
        ag = ag + (max(x, 0) - ag) / 14
        al = al + (max(-x, 0) - al) / 14
    expected = 100 - 100 / (1 + ag / al)
    assert M.rsi(s) == pytest.approx(expected, rel=1e-9)


def test_indicators_and_signals():
    out = M.compute(FakeLoader(crypto_frames()), NOW, 131.0, NOW, crypto=True)
    ind = out["ind"]
    # stetig steigend: RSI hoch, Kurs über allen EMAs, MACD über Signal
    assert ind["rsi14"] > 70
    assert 131.0 > ind["ema20"] > ind["ema50"] > ind["ema200"]
    assert ind["macd"] > ind["macd_signal"]
    assert out["new_high_24h"] is True and out["new_low_24h"] is False
    assert out["turnover_24h"] > 0 and out["rel_volume"] is not None
    assert out["spark"][-1] == 131.0 and len(out["spark"]) <= 33


def test_golden_and_death_cross():
    fast = pd.Series([1, 1, 1, 3, 3.0])
    slow = pd.Series([2, 2, 2, 2, 2.0])
    assert M._cross(fast, slow) == "golden"
    assert M._cross(slow, fast) == "death"
    assert M._cross(pd.Series([3, 3, 3.0]), pd.Series([1, 1, 1.0])) is None


def test_day_spark_starts_at_berlin_midnight():
    out = M.compute(FakeLoader(crypto_frames()), NOW, 131.0, NOW, crypto=True)
    sp = out["spark_day"]
    assert 3 <= len(sp) <= 49 and sp[-1] == 131.0
    # 20:00 UTC = 21:00 Berlin: Tagesverlauf seit 23:00 UTC des Vortags, also weniger als die 24-Std.-Reihe
    f5 = crypto_frames()["5m"]
    start = f5[f5.index >= pd.Timestamp("2026-11-11 23:00", tz="UTC")]["close"].iloc[0]
    assert sp[0] == pytest.approx(start, rel=1e-4)
