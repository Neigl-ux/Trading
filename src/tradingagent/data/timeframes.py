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
    "12h": timedelta(hours=12),
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
    "12h": "1h",
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


# Zehn Kerzenlängen (wie im Auftrag) plus fünf lange Zeiträume. Der Zeitraum
# gibt an, wie viel Historie exportiert wird; die App kann darin zoomen.
CHART_VIEWS: tuple[ChartView, ...] = (
    ChartView("1m", "1m", "1m", timedelta(hours=16)),
    ChartView("5m", "5m", "5m", timedelta(days=5)),
    ChartView("15m", "15m", "15m", timedelta(days=15)),
    ChartView("30m", "30m", "30m", timedelta(days=30)),
    ChartView("1h", "1h", "1h", timedelta(days=60)),
    ChartView("4h", "4h", "4h", timedelta(days=365)),
    ChartView("12h", "12h", "12h", timedelta(days=700)),
    ChartView("1D", "1D", "1d", timedelta(days=1100)),
    ChartView("1W", "1W", "1w", timedelta(days=3653)),
    ChartView("1M", "1M", "1M", timedelta(days=3653)),
    ChartView("6M", "6 Mon", "1d", timedelta(days=183)),
    ChartView("1J", "1 Jahr", "1d", timedelta(days=365)),
    ChartView("3J", "3 Jahre", "1w", timedelta(days=1096)),
    ChartView("5J", "5 Jahre", "1w", timedelta(days=1826)),
    ChartView("10J", "10 Jahre", "1M", timedelta(days=3653)),
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
