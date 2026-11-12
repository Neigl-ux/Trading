"""Gemeinsame Schnittstelle aller Kursquellen.

Jede Quelle implementiert `fetch`. Der Rest des Systems kennt nur diese
Schnittstelle, daher lassen sich Anbieter austauschen oder ergänzen.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, Callable

import pandas as pd
import requests

USER_AGENT = "tradingagent/0.2 (private research project)"


class ProviderError(Exception):
    """Quelle nicht erreichbar, gesperrt oder Antwort unbrauchbar."""


class MarketDataProvider(ABC):
    name: str = "base"
    #: Intervalle, die die Quelle direkt liefern kann
    intervals: frozenset[str] = frozenset()
    #: Wie weit die Quelle pro Intervall zurückreicht (None = unbegrenzt)
    max_history: dict[str, timedelta | None] = {}

    def supports(self, interval: str) -> bool:
        return interval in self.intervals

    def clip_start(self, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.Timestamp:
        limit = self.max_history.get(interval)
        if limit is None:
            return start
        earliest = now - limit
        return max(start, earliest)

    @abstractmethod
    def fetch(self, symbol: str, interval: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
        """Kerzen ab `start` bis jetzt im Standardformat (siehe data.bars)."""


HttpGet = Callable[..., Any]


def http_get_json(url: str, params: dict[str, Any] | None = None, *, timeout: float = 15.0,
                  retries: int = 3, backoff: float = 2.0,
                  session: requests.Session | None = None) -> Any:
    """GET mit Wiederholungen bei Netzwerkfehlern und 5xx/429."""
    sess = session or requests
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = sess.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            last_err = exc
        else:
            if resp.status_code == 451 or resp.status_code == 403:
                raise ProviderError(f"{url}: Zugriff aus diesem Land/Rechenzentrum gesperrt ({resp.status_code})")
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = ProviderError(f"{url}: HTTP {resp.status_code}")
            elif resp.status_code != 200:
                raise ProviderError(f"{url}: HTTP {resp.status_code} {resp.text[:200]}")
            else:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise ProviderError(f"{url}: keine gültige JSON-Antwort") from exc
        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1))
    raise ProviderError(f"{url}: nicht erreichbar nach {retries} Versuchen ({last_err})")
