"""Kraken: öffentliche Krypto-Kursdaten, ohne Konto und ohne Schlüssel.

Endpunkt: GET https://api.kraken.com/0/public/OHLC
Kraken liefert pro Abruf höchstens die letzten 720 Kerzen eines Intervalls.
Die letzte Kerze ist noch nicht abgeschlossen und wird später verworfen.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd

from ..bars import normalize, empty_frame
from .base import MarketDataProvider, ProviderError, http_get_json

_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


class KrakenProvider(MarketDataProvider):
    name = "kraken"
    intervals = frozenset(_INTERVAL_MINUTES)
    url = "https://api.kraken.com/0/public/OHLC"

    #: Mindestabstand zwischen zwei Abrufen (Kraken erlaubt öffentlich etwa einen Abruf pro Sekunde)
    min_gap = 1.05
    #: Wartezeiten bei „Rate limit exceeded“ (exponentiell)
    backoff = (2.0, 4.0, 8.0)

    def __init__(self, http_get=http_get_json, sleep=time.sleep, clock=time.monotonic):
        self._get = http_get
        self._sleep = sleep
        self._clock = clock
        self._last_call = None
        # 720 Kerzen je Intervall
        self.max_history = {k: timedelta(minutes=m * 720) for k, m in _INTERVAL_MINUTES.items()}

    def fetch(self, symbol: str, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
        if interval not in _INTERVAL_MINUTES:
            raise ProviderError(f"Kraken unterstützt {interval} nicht")
        start = self.clip_start(interval, start, now)
        params = {"pair": symbol, "interval": _INTERVAL_MINUTES[interval], "since": int(start.timestamp()) - 1}
        data = self._call(params)
        errors = data.get("error") or []
        if errors:
            raise ProviderError(f"Kraken: {', '.join(errors)}")
        result = data.get("result") or {}
        rows = next((v for k, v in result.items() if k != "last"), None)
        if not rows:
            return empty_frame()
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
        df.index = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
        df = normalize(df[["open", "high", "low", "close", "volume"]])
        return df[df.index >= start]

    def _call(self, params):
        """Abruf mit Mindestabstand; bei Kraken-Ratenlimit mit wachsender Pause erneut versuchen."""
        for wait in (*self.backoff, None):
            if self._last_call is not None:
                gap = self._clock() - self._last_call
                if gap < self.min_gap:
                    self._sleep(self.min_gap - gap)
            self._last_call = self._clock()
            data = self._get(self.url, params)
            errors = data.get("error") or []
            if wait is not None and any("Rate limit" in e or "Too many requests" in e for e in errors):
                print(f"Kraken-Ratenlimit erreicht, warte {wait:.0f} s")
                self._sleep(wait)
                continue
            return data
        return data
