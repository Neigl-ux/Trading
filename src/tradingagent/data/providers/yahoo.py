"""Yahoo Finance über die inoffizielle Bibliothek yfinance.

- Xetra- und Euronext-Kurse sind laut Yahoo 15 Minuten verzögert, US-Kurse
  nahezu in Echtzeit.
- Intraday-Historie ist begrenzt: 5m/15m ca. 60 Tage, 60m ca. 730 Tage.
- Yahoo-Daten sind nur für private Information gedacht und dürfen nicht
  weiterverbreitet werden. Deshalb werden sie nur verschlüsselt exportiert.
- Kurse werden um Splits und Dividenden bereinigt (auto_adjust).

Viele Werte werden gebündelt in einem Aufruf geholt (`prefetch`). Das schont
Yahoo und hält den 15-Minuten-Lauf kurz. Die Bibliothek kann jederzeit
brechen, weil Yahoo keine offizielle Schnittstelle anbietet.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

import pandas as pd

from ..bars import normalize, empty_frame
from .base import MarketDataProvider, ProviderError

_YF_INTERVAL = {"5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}
_LIMITS = {"5m": timedelta(days=59), "15m": timedelta(days=59), "1h": timedelta(days=729), "1d": None}
_BATCH_SIZE = 60


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    intervals = frozenset(_YF_INTERVAL)
    max_history = _LIMITS

    def __init__(self, yf_module: Any | None = None):
        self._yf = yf_module
        # (symbol, interval) -> (start, frame oder Fehlertext)
        self._cache: dict[tuple[str, str], tuple[pd.Timestamp, pd.DataFrame | str]] = {}

    def _module(self):
        if self._yf is None:
            try:
                import logging

                import yfinance  # bewusst erst hier, damit Tests ohne yfinance laufen

                # yfinance meldet geschlossene Börsen als „possibly delisted“; das verwirrt im Protokoll.
                logging.getLogger("yfinance").setLevel(logging.CRITICAL)
            except ImportError as exc:  # pragma: no cover
                raise ProviderError("yfinance ist nicht installiert (pip install yfinance)") from exc
            self._yf = yfinance
        return self._yf

    # ---------- gebündelt ----------
    def prefetch(self, symbols: Iterable[str], interval: str, start: pd.Timestamp, now: pd.Timestamp) -> None:
        """Holt viele Werte auf einmal und legt sie für `fetch` bereit."""
        if interval not in _YF_INTERVAL:
            return
        symbols = list(dict.fromkeys(symbols))
        start = self.clip_start(interval, start, now)
        yf = self._module()
        for i in range(0, len(symbols), _BATCH_SIZE):
            chunk = symbols[i:i + _BATCH_SIZE]
            try:
                data = yf.download(
                    tickers=chunk,
                    start=start.to_pydatetime(),
                    end=(now + pd.Timedelta(days=1)).to_pydatetime(),
                    interval=_YF_INTERVAL[interval],
                    group_by="ticker",
                    auto_adjust=True,
                    actions=False,
                    threads=True,
                    progress=False,
                )
            except Exception as exc:
                for sym in chunk:
                    self._cache[(sym, interval)] = (start, f"Sammelabruf fehlgeschlagen: {exc}")
                continue
            for sym in chunk:
                frame = _extract(data, sym)
                self._cache[(sym, interval)] = (start, frame)

    # ---------- einzeln ----------
    def fetch(self, symbol: str, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
        if interval not in _YF_INTERVAL:
            raise ProviderError(f"Yahoo unterstützt {interval} nicht")
        start = self.clip_start(interval, start, now)
        hit = self._cache.get((symbol, interval))
        if hit is not None and hit[0] <= start + pd.Timedelta(minutes=1):
            cached = hit[1]
            if isinstance(cached, str):
                raise ProviderError(f"Yahoo {symbol} {interval}: {cached}")
            return cached[cached.index >= start - pd.Timedelta(days=1)]
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


def _extract(data: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    """Holt die Spalten eines Werts aus dem Ergebnis von yf.download."""
    if data is None or len(data) == 0:
        return empty_frame()
    cols = data.columns
    if isinstance(cols, pd.MultiIndex):
        if symbol in cols.get_level_values(0):
            sub = data[symbol]
        elif symbol in cols.get_level_values(-1):
            sub = data.xs(symbol, axis=1, level=-1)
        else:
            return empty_frame()
    else:
        sub = data
    sub = sub.dropna(how="all")
    if sub.empty:
        return empty_frame()
    return normalize(sub)
