"""Binance: öffentliche Krypto-Kursdaten, ohne Konto.

Endpunkt: GET {base}/api/v3/klines (bis 1000 Kerzen je Abruf, mit Blättern).
Hinweis: Von US-Rechenzentren (z. B. GitHub Actions) sperrt Binance den
Zugriff (HTTP 451). Dann greift automatisch die nächste Quelle der Kette.
"""

from __future__ import annotations

import pandas as pd

from ..bars import normalize, empty_frame
from ..timeframes import interval_delta
from .base import MarketDataProvider, ProviderError, http_get_json

_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}


class BinanceProvider(MarketDataProvider):
    name = "binance"
    intervals = frozenset(_INTERVALS)

    def __init__(self, http_get=http_get_json, base_url: str = "https://api.binance.com", max_pages: int = 20):
        self._get = http_get
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages
        self.max_history = {k: None for k in _INTERVALS}

    def fetch(self, symbol: str, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
        if interval not in _INTERVALS:
            raise ProviderError(f"Binance unterstützt {interval} nicht")
        step = interval_delta(interval)
        cursor = start
        frames = []
        for _ in range(self.max_pages):
            params = {"symbol": symbol, "interval": interval, "startTime": int(cursor.timestamp() * 1000), "limit": 1000}
            rows = self._get(f"{self.base_url}/api/v3/klines", params)
            if isinstance(rows, dict):  # Fehlerobjekt {"code":..., "msg":...}
                raise ProviderError(f"Binance: {rows.get('msg', rows)}")
            if not rows:
                break
            df = pd.DataFrame([r[:6] for r in rows], columns=["t", "open", "high", "low", "close", "volume"])
            df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
            frames.append(df[["open", "high", "low", "close", "volume"]])
            last = df.index[-1]
            if len(rows) < 1000 or last + step >= now:
                break
            cursor = last + step
        if not frames:
            return empty_frame()
        return normalize(pd.concat(frames))
