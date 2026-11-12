"""Kursquellen und Fallback-Kette."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ...config import Instrument
from .base import MarketDataProvider, ProviderError
from .binance import BinanceProvider
from .kraken import KrakenProvider
from .yahoo import YahooProvider

REGISTRY = {
    "kraken": KrakenProvider,
    "binance": BinanceProvider,
    "yahoo": YahooProvider,
}


def build_providers(names: list[str] | None = None) -> dict[str, MarketDataProvider]:
    names = names or list(REGISTRY)
    return {n: REGISTRY[n]() for n in names}


@dataclass
class FetchResult:
    frame: pd.DataFrame
    provider: str | None
    errors: list[str]


def fetch_with_fallback(instrument: Instrument, interval: str, start: pd.Timestamp, now: pd.Timestamp,
                        chain: list[str], providers: dict[str, MarketDataProvider]) -> FetchResult:
    """Fragt die Quellen der Reihe nach, bis eine Daten liefert."""
    errors: list[str] = []
    for name in chain:
        provider = providers.get(name)
        symbol = instrument.symbol_for(name)
        if provider is None or symbol is None or not provider.supports(interval):
            continue
        try:
            df = provider.fetch(symbol, interval, start, now)
        except ProviderError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if df.empty:
            errors.append(f"{name}: keine Daten")
            continue
        return FetchResult(df, name, errors)
    return FetchResult(pd.DataFrame(), None, errors or ["keine passende Quelle konfiguriert"])


__all__ = [
    "MarketDataProvider", "ProviderError", "KrakenProvider", "BinanceProvider", "YahooProvider",
    "build_providers", "fetch_with_fallback", "FetchResult",
]
