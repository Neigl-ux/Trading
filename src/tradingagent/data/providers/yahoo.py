"""Yahoo Finance über die inoffizielle Bibliothek yfinance.

- Xetra-Kurse (.DE) sind laut Yahoo 15 Minuten verzögert.
- Intraday-Historie ist begrenzt: 5m/15m ca. 60 Tage, 60m ca. 730 Tage.
- Yahoo-Daten sind nur für private Information gedacht und dürfen nicht
  weiterverbreitet werden. Deshalb werden sie nur verschlüsselt exportiert.
- Kurse werden um Splits und Dividenden bereinigt (auto_adjust).

Die Bibliothek kann jederzeit brechen, weil Yahoo keine offizielle
Schnittstelle anbietet. Fehler werden als ProviderError gemeldet.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from ..bars import normalize, empty_frame
from .base import MarketDataProvider, ProviderError

_YF_INTERVAL = {"5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}
_LIMITS = {"5m": timedelta(days=59), "15m": timedelta(days=59), "1h": timedelta(days=729), "1d": None}


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    intervals = frozenset(_YF_INTERVAL)
    max_history = _LIMITS

    def __init__(self, yf_module: Any | None = None):
        self._yf = yf_module

    def _module(self):
        if self._yf is None:
            try:
                import yfinance  # noqa: WPS433 (bewusst lazy, damit Tests ohne yfinance laufen)
            except ImportError as exc:  # pragma: no cover
                raise ProviderError("yfinance ist nicht installiert (pip install yfinance)") from exc
            self._yf = yfinance
        return self._yf

    def fetch(self, symbol: str, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
        if interval not in _YF_INTERVAL:
            raise ProviderError(f"Yahoo unterstützt {interval} nicht")
        start = self.clip_start(interval, start, now)
        yf = self._module()
        try:
            hist = yf.Ticker(symbol).history(
                start=start.to_pydatetime(),
                end=(now + pd.Timedelta(days=1)).to_pydatetime(),
                interval=_YF_INTERVAL[interval],
                auto_adjust=True,
                actions=False,
            )
        except Exception as exc:  # yfinance wirft verschiedenste Fehler
            raise ProviderError(f"Yahoo {symbol} {interval}: {exc}") from exc
        if hist is None or len(hist) == 0:
            return empty_frame()
        df = normalize(hist)
        return df[df.index >= start - pd.Timedelta(days=1)]
