"""Abstrakte Speicher-Schnittstelle.

Alle Module sprechen nur mit dieser Schnittstelle. Ein späterer Wechsel auf
PostgreSQL/TimescaleDB auf dem Oracle-Server braucht nur eine neue Klasse.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Storage(ABC):
    @abstractmethod
    def upsert_bars(self, instrument: str, interval: str, df: pd.DataFrame, source: str) -> int:
        """Speichert Kerzen; vorhandene Zeitstempel werden überschrieben. Gibt die Anzahl neuer Kerzen zurück."""

    @abstractmethod
    def load_bars(self, instrument: str, interval: str, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None) -> pd.DataFrame:
        """Lädt Kerzen im Standardformat."""

    @abstractmethod
    def last_ts(self, instrument: str, interval: str) -> pd.Timestamp | None:
        """Startzeit der jüngsten gespeicherten Kerze."""

    @abstractmethod
    def get_meta(self, key: str) -> str | None: ...

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None: ...

    @abstractmethod
    def log_run(self, summary: dict[str, Any]) -> None:
        """Protokolliert einen Sammel-Lauf."""

    @abstractmethod
    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]: ...

    @abstractmethod
    def counts(self) -> list[dict[str, Any]]:
        """Übersicht: Anzahl Kerzen und Zeitraum je Instrument und Intervall."""

    def close(self) -> None:  # pragma: no cover - optional
        pass
