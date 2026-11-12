from __future__ import annotations

import pandas as pd
import pytest

from tradingagent.config import Instrument
from tradingagent.data.providers import BinanceProvider, KrakenProvider, YahooProvider, fetch_with_fallback
from tradingagent.data.providers.base import MarketDataProvider, ProviderError

NOW = pd.Timestamp("2026-01-05 12:00", tz="UTC")


def test_kraken_parses_and_respects_limit():
    calls = []

    def fake_get(url, params):
        calls.append(params)
        t0 = int(pd.Timestamp("2026-01-05 10:00", tz="UTC").timestamp())
        rows = [[t0 + i * 900, "100", "101", "99", "100.5", "100.2", "3.5", 10] for i in range(8)]
        return {"error": [], "result": {"XXBTZEUR": rows, "last": t0}}

    p = KrakenProvider(http_get=fake_get)
    df = p.fetch("XBTEUR", "15m", pd.Timestamp("2025-01-01", tz="UTC"), NOW)
    assert len(df) == 8 and df["close"].iloc[0] == 100.5
    # Startzeit wird auf 720 Kerzen begrenzt
    assert calls[0]["since"] >= int((NOW - pd.Timedelta(minutes=15 * 720)).timestamp()) - 1


def test_kraken_error_raises():
    p = KrakenProvider(http_get=lambda url, params: {"error": ["EQuery:Unknown asset pair"], "result": {}})
    with pytest.raises(ProviderError):
        p.fetch("XXX", "15m", NOW - pd.Timedelta(hours=1), NOW)


def test_binance_paginates():
    def fake_get(url, params):
        start = params["startTime"]
        n = 1000 if start < int(pd.Timestamp("2026-01-05", tz="UTC").timestamp() * 1000) else 5
        return [[start + i * 60_000, "1", "2", "0.5", "1.5", "10", 0, 0, 0, 0, 0, 0] for i in range(n)]

    p = BinanceProvider(http_get=fake_get)
    df = p.fetch("BTCEUR", "1m", pd.Timestamp("2026-01-04 18:00", tz="UTC"), NOW)
    assert len(df) == 1005
    assert df.index.is_monotonic_increasing


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, **kw):
        idx = pd.date_range("2026-01-05 09:00", periods=3, freq="15min", tz="Europe/Berlin")
        return pd.DataFrame({"Open": [1.0, 2, 3], "High": [2.0, 3, 4], "Low": [0.5, 1, 2],
                             "Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30]}, index=idx)


class _FakeYF:
    Ticker = _FakeTicker


def test_yahoo_uses_module_and_normalizes():
    p = YahooProvider(yf_module=_FakeYF)
    df = p.fetch("SAP.DE", "15m", NOW - pd.Timedelta(days=1), NOW)
    assert len(df) == 3 and str(df.index.tz) == "UTC"


def test_yahoo_rejects_unsupported_interval():
    with pytest.raises(ProviderError):
        YahooProvider(yf_module=_FakeYF).fetch("SAP.DE", "1m", NOW, NOW)


class _Broken(MarketDataProvider):
    name = "a"
    intervals = frozenset({"15m"})

    def fetch(self, *a):
        raise ProviderError("gesperrt (451)")


class _Works(MarketDataProvider):
    name = "b"
    intervals = frozenset({"15m"})

    def fetch(self, symbol, interval, start, now):
        idx = pd.date_range("2026-01-05 10:00", periods=2, freq="15min", tz="UTC")
        return pd.DataFrame({"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "volume": [0, 0]},
                            index=idx, dtype=float)


def test_fallback_chain_uses_next_provider():
    inst = Instrument("BTC-EUR", "Bitcoin", "crypto", {"a": "BTCEUR", "b": "XBTEUR"})
    res = fetch_with_fallback(inst, "15m", NOW - pd.Timedelta(hours=1), NOW, ["a", "b"], {"a": _Broken(), "b": _Works()})
    assert res.provider == "b" and len(res.frame) == 2
    assert "451" in res.errors[0]


def test_fallback_reports_when_all_fail():
    inst = Instrument("BTC-EUR", "Bitcoin", "crypto", {"a": "BTCEUR"})
    res = fetch_with_fallback(inst, "15m", NOW, NOW, ["a"], {"a": _Broken()})
    assert res.provider is None and res.errors


class _FakeYFBatch:
    """Simuliert yf.download mit MultiIndex-Spalten (Ticker, Feld)."""

    def __init__(self, fail=False):
        self.download_calls = 0
        self.ticker_calls = 0
        self.fail = fail
        outer = self

        class Ticker:
            def __init__(self, symbol):
                outer.ticker_calls += 1

            def history(self, **kw):
                return pd.DataFrame()

        self.Ticker = Ticker

    def download(self, tickers, **kw):
        self.download_calls += 1
        if self.fail:
            raise RuntimeError("Too Many Requests")
        idx = pd.date_range("2026-01-05 09:00", periods=4, freq="15min", tz="Europe/Berlin")
        frames = {}
        for t in tickers:
            if t == "GIBTSNICHT.DE":
                frames[t] = pd.DataFrame({c: [float("nan")] * 4 for c in ["Open", "High", "Low", "Close", "Volume"]}, index=idx)
            else:
                frames[t] = pd.DataFrame({"Open": [1.0, 2, 3, 4], "High": [2.0, 3, 4, 5], "Low": [0.5, 1, 2, 3],
                                          "Close": [1.5, 2.5, 3.5, 4.5], "Volume": [1, 2, 3, 4]}, index=idx)
        return pd.concat(frames, axis=1)


def test_yahoo_prefetch_serves_from_cache():
    fake = _FakeYFBatch()
    p = YahooProvider(yf_module=fake)
    start = NOW - pd.Timedelta(days=2)
    p.prefetch(["SAP.DE", "SIE.DE", "GIBTSNICHT.DE"], "15m", start, NOW)
    assert fake.download_calls == 1
    assert len(p.fetch("SAP.DE", "15m", start, NOW)) == 4
    assert len(p.fetch("SIE.DE", "15m", start + pd.Timedelta(hours=1), NOW)) == 4
    assert p.fetch("GIBTSNICHT.DE", "15m", start, NOW).empty
    assert fake.ticker_calls == 0  # nichts einzeln nachgeladen


def test_yahoo_prefetch_failure_is_reported_per_symbol():
    p = YahooProvider(yf_module=_FakeYFBatch(fail=True))
    p.prefetch(["SAP.DE"], "15m", NOW - pd.Timedelta(days=1), NOW)
    with pytest.raises(ProviderError, match="Sammelabruf"):
        p.fetch("SAP.DE", "15m", NOW - pd.Timedelta(days=1), NOW)


def test_kraken_waits_and_retries_on_rate_limit():
    from tradingagent.data.providers.kraken import KrakenProvider
    calls, sleeps = [], []
    answers = [{"error": ["EAPI:Rate limit exceeded"]}, {"error": [], "result": {"XXBTZEUR": [[1700000000, "1", "2", "0.5", "1.5", "1", "3", 5]], "last": 1}}]

    def get(url, params):
        calls.append(params)
        return answers[len(calls) - 1]

    p = KrakenProvider(http_get=get, sleep=sleeps.append, clock=lambda: 0.0)
    df = p.fetch("XBTEUR", "1h", pd.Timestamp("2023-11-14", tz="UTC"), pd.Timestamp("2023-11-15", tz="UTC"))
    assert len(calls) == 2 and 2.0 in sleeps
    assert float(df["close"].iloc[0]) == 1.5
