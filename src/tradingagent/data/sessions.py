"""Handelszeiten der Märkte."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd


@dataclass(frozen=True)
class Session:
    tz: str
    open: time | None  # None = rund um die Uhr
    close: time | None
    weekdays_only: bool

    def is_open(self, ts: pd.Timestamp) -> bool:
        if self.open is None:
            return True
        local = ts.tz_convert(self.tz)
        if self.weekdays_only and local.weekday() >= 5:
            return False
        return self.open <= local.time() < self.close

    def expected_index(self, start: pd.Timestamp, end: pd.Timestamp, freq: str) -> pd.DatetimeIndex:
        """Alle Kerzen-Startzeiten, die im Zeitraum zu erwarten sind (ohne Feiertage)."""
        idx = pd.date_range(start.ceil(freq), end, freq=freq, tz="UTC", inclusive="left")
        if self.open is None:
            return idx
        local = idx.tz_convert(self.tz)
        mask = (local.time >= self.open) & (local.time < self.close)
        if self.weekdays_only:
            mask &= local.weekday < 5
        return idx[mask]


# Xetra: werktags 9:00-17:30 Uhr. Feiertage werden nicht fest hinterlegt;
# liefert die Quelle an einem Werktag gar keine Kerzen, wertet die
# Qualitätsprüfung das als geschlossenen Handelstag.
SESSIONS: dict[str, Session] = {
    "crypto": Session(tz="UTC", open=None, close=None, weekdays_only=False),
    "xetra": Session(tz="Europe/Berlin", open=time(9, 0), close=time(17, 30), weekdays_only=True),
}

MARKET_TZ = {"crypto": "UTC", "xetra": "Europe/Berlin"}


def session_for(market: str) -> Session:
    return SESSIONS[market]


def berlin_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ZoneInfo("Europe/Berlin"))
