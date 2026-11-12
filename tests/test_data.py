from __future__ import annotations

import pandas as pd
import pytest

from tradingagent.data.bars import drop_unclosed, normalize
from tradingagent.data.quality import check_and_clean, check_freshness, check_gaps, QualityReport
from tradingagent.data.resample import resample
from tradingagent.data.sessions import session_for
from tradingagent.data.timeframes import CHART_VIEWS, export_lookbacks


def test_fourteen_chart_views_in_requested_order():
    labels = [v.label for v in CHART_VIEWS]
    assert labels == ["1 Min", "5 Min", "15 Min", "30 Min", "60 Min", "1 Tag", "1 Woche", "2 Wochen",
                      "1 Monat", "6 Monate", "12 Monate", "3 Jahre", "5 Jahre", "10 Jahre"]


def test_export_lookbacks_cover_every_view():
    needed = export_lookbacks()
    for v in CHART_VIEWS:
        if v.interval != "1m":
            assert needed[v.interval] >= v.lookback


def test_normalize_lowercases_and_converts_timezone():
    idx = pd.date_range("2026-01-05 09:00", periods=2, freq="15min", tz="Europe/Berlin")
    raw = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0.5, 1], "Close": [1.5, 2.5], "Volume": [1, 1]}, index=idx)
    df = normalize(raw)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2026-01-05 08:00", tz="UTC")


def test_drop_unclosed_respects_delay(bars):
    df = bars("2026-01-05 10:00", 4, "15min")  # 10:00, 10:15, 10:30, 10:45
    now = pd.Timestamp("2026-01-05 11:05", tz="UTC")
    assert len(drop_unclosed(df, pd.Timedelta("15min"), now)) == 4  # letzte endet 11:00
    assert len(drop_unclosed(df, pd.Timedelta("15min"), pd.Timestamp("2026-01-05 10:55", tz="UTC"))) == 3
    # mit 15 Min Verzögerung: Grenze 10:50 -> nur Kerzen, die bis 10:50 enden
    assert len(drop_unclosed(df, pd.Timedelta("15min"), now, pd.Timedelta("15min"))) == 3


def test_resample_week_starts_monday_berlin(bars):
    df = bars("2026-01-04 23:00", 14, "1D")  # Tagesbeginn Berlin = 23:00 UTC
    wk = resample(df, "1w", tz="Europe/Berlin")
    first = wk.index[0].tz_convert("Europe/Berlin")
    assert first.weekday() == 0 and first.hour == 0
    week = df[(df.index >= wk.index[0]) & (df.index < wk.index[0] + pd.Timedelta(days=7))]
    assert wk["open"].iloc[0] == week["open"].iloc[0]
    assert wk["close"].iloc[0] == week["close"].iloc[-1]
    assert wk["high"].iloc[0] == week["high"].max()
    assert wk["volume"].iloc[0] == pytest.approx(week["volume"].sum())


def test_resample_30m_and_month(bars):
    df = bars("2026-01-05 08:00", 8, "15min")
    m30 = resample(df, "30m")
    assert len(m30) == 4
    daily = bars("2025-11-30 23:00", 70, "1D")
    mo = resample(daily, "1M", tz="Europe/Berlin")
    assert [ts.tz_convert("Europe/Berlin").day for ts in mo.index] == [1, 1, 1]


def test_quality_removes_broken_rows(bars):
    df = bars("2026-01-05 00:00", 30, "15min")
    df.iloc[3, df.columns.get_loc("high")] = df["low"].iloc[3] * 0.5  # Hoch unter Tief
    df.iloc[5, df.columns.get_loc("close")] = -1
    df.iloc[7, df.columns.get_loc("volume")] = -5
    clean, report = check_and_clean(df, "X", "15m", "crypto")
    assert len(clean) == 27
    kinds = {i.kind for i in report.issues}
    assert {"ohlc_widerspruch", "preis_ungueltig", "volumen_negativ"} <= kinds


def test_quality_flags_jump_but_keeps_it(bars):
    df = bars("2026-01-05 00:00", 100, "15min", seed=3)
    df.loc[df.index[60]:, ["open", "high", "low", "close"]] *= 1.5
    df.iloc[60, df.columns.get_loc("open")] = df["close"].iloc[59]
    df.iloc[60, df.columns.get_loc("low")] = df["close"].iloc[59]
    clean, report = check_and_clean(df, "X", "15m", "crypto", jumps_after=df.index[50])
    assert len(clean) == 100
    assert any(i.kind == "kurssprung" for i in report.issues)
    # alte Sprünge (vor dem letzten Lauf) und Erstbefüllung melden nichts
    _, old = check_and_clean(df, "X", "15m", "crypto", jumps_after=df.index[70])
    _, first = check_and_clean(df, "X", "15m", "crypto")
    assert not any(i.kind == "kurssprung" for i in old.issues + first.issues)


def test_gaps_ignore_holidays_but_find_missing_bars(bars):
    session = session_for("xetra")
    idx = session.expected_index(pd.Timestamp("2026-01-05", tz="UTC"), pd.Timestamp("2026-01-08", tz="UTC"), "15min")
    df = bars("2026-01-05", len(idx), "15min")
    df.index = idx
    report = QualityReport("SAP", "15m")
    check_gaps(df, "SAP", "15m", "xetra", report)
    assert not report.issues
    df2 = df.drop(df.index[5:8])
    report2 = QualityReport("SAP", "15m")
    check_gaps(df2, "SAP", "15m", "xetra", report2)
    assert report2.issues[0].count == 3
    # ein ganzer fehlender Handelstag (Feiertag) ist keine Lücke
    day2 = df.index.tz_convert("Europe/Berlin").date == pd.Timestamp("2026-01-06").date()
    report3 = QualityReport("SAP", "15m")
    check_gaps(df[~day2], "SAP", "15m", "xetra", report3)
    assert not report3.issues


def test_xetra_session_hours():
    s = session_for("xetra")
    assert s.is_open(pd.Timestamp("2026-01-05 09:00", tz="Europe/Berlin").tz_convert("UTC"))
    assert not s.is_open(pd.Timestamp("2026-01-05 17:30", tz="Europe/Berlin").tz_convert("UTC"))
    assert not s.is_open(pd.Timestamp("2026-01-10 12:00", tz="Europe/Berlin").tz_convert("UTC"))  # Samstag


def test_freshness_warns_only_during_session():
    now = pd.Timestamp("2026-01-05 12:00", tz="Europe/Berlin").tz_convert("UTC")
    stale = now - pd.Timedelta(hours=2)
    r = QualityReport("SAP", "15m")
    check_freshness(stale, "15m", "xetra", now, 15, r)
    assert r.issues and r.issues[0].kind == "veraltet"
    r2 = QualityReport("SAP", "15m")
    sunday = pd.Timestamp("2026-01-11 12:00", tz="UTC")
    check_freshness(stale, "15m", "xetra", sunday, 15, r2)
    assert not r2.issues
