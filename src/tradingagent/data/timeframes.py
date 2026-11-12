"""Zeitebenen und die 14 Chart-Ansichten der App.

Gespeichert werden nur wenige Basis-Intervalle (5m, 15m, 1h, 1d). Alle
anderen Kerzenlängen (30m, 4h, 1w, 2w, 1M) werden daraus berechnet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

# Kerzenlänge -> Dauer. Woche und Monat sind kalendarisch, die Dauer hier ist
# nur ein Näherungswert für Plausibilitätsprüfungen.
INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "2w": timedelta(weeks=2),
    "1M": timedelta(days=30),
}

STORED_INTERVALS = ("5m", "15m", "1h", "1d")

# Aus welchem gespeicherten Intervall ein abgeleitetes berechnet wird.
DERIVED_FROM: dict[str, str] = {
    "30m": "15m",
    "4h": "1h",
    "1w": "1d",
    "2w": "1d",
    "1M": "1d",
}


def interval_delta(interval: str) -> timedelta:
    try:
        return INTERVALS[interval]
    except KeyError as exc:
        raise ValueError(f"Unbekanntes Intervall: {interval}") from exc


@dataclass(frozen=True)
class ChartView:
    """Eine Auswahl im Chart: welche Kerze, welcher Zeitraum."""

    key: str
    label: str
    interval: str
    lookback: timedelta


CHART_VIEWS: tuple[ChartView, ...] = (
    ChartView("1min", "1 Min", "1m", timedelta(hours=6)),
    ChartView("5min", "5 Min", "5m", timedelta(days=1)),
    ChartView("15min", "15 Min", "15m", timedelta(days=3)),
    ChartView("30min", "30 Min", "30m", timedelta(days=7)),
    ChartView("60min", "60 Min", "1h", timedelta(days=14)),
    ChartView("1d", "1 Tag", "1d", timedelta(days=92)),
    ChartView("1w", "1 Woche", "1w", timedelta(days=730)),
    ChartView("2w", "2 Wochen", "2w", timedelta(days=1461)),
    ChartView("1mo", "1 Monat", "1M", timedelta(days=3653)),
    ChartView("6mo", "6 Monate", "1d", timedelta(days=183)),
    ChartView("12mo", "12 Monate", "1d", timedelta(days=365)),
    ChartView("3y", "3 Jahre", "1w", timedelta(days=1096)),
    ChartView("5y", "5 Jahre", "1w", timedelta(days=1826)),
    ChartView("10y", "10 Jahre", "1M", timedelta(days=3653)),
)


def export_lookbacks() -> dict[str, timedelta]:
    """Wie viel Historie pro Kerzenlänge exportiert werden muss, damit jede
    Chart-Ansicht vollständig ist. 1m wird nicht exportiert: Krypto lädt die
    App live, bei Xetra fällt die Ansicht auf 5m zurück."""
    needed: dict[str, timedelta] = {}
    for view in CHART_VIEWS:
        if view.interval == "1m":
            continue
        needed[view.interval] = max(needed.get(view.interval, timedelta(0)), view.lookback)
    return needed
