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
        # ab der ersten echten Kerze zählen: US-Stundenkerzen beginnen zur halben Stunde (9:30, 10:30 …)
        idx = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
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
    # Euronext Paris/Amsterdam und Madrid: 9:00-17:30 Uhr deutscher Zeit
    "europe": Session(tz="Europe/Berlin", open=time(9, 0), close=time(17, 30), weekdays_only=True),
    # NYSE/Nasdaq: 9:30-16:00 Uhr New Yorker Zeit (15:30-22:00 Uhr deutscher Zeit)
    "us": Session(tz="America/New_York", open=time(9, 30), close=time(16, 0), weekdays_only=True),
    # Devisen: werktags rund um die Uhr (vereinfacht Montag bis Freitag UTC)
    "fx": Session(tz="UTC", open=time(0, 0), close=time(23, 59, 59), weekdays_only=True),
    # London Stock Exchange: 8:00-16:30 Uhr Londoner Zeit
    "uk": Session(tz="Europe/London", open=time(8, 0), close=time(16, 30), weekdays_only=True),
    # Tokio: 9:00-15:30 Uhr (mit Mittagspause, hier vereinfacht durchgehend)
    "japan": Session(tz="Asia/Tokyo", open=time(9, 0), close=time(15, 30), weekdays_only=True),
    # Hongkong: 9:30-16:00 Uhr (mit Mittagspause, vereinfacht)
    "hongkong": Session(tz="Asia/Hong_Kong", open=time(9, 30), close=time(16, 0), weekdays_only=True),
    # Rohstoff-Terminkontrakte (CME/ICE): werktags nahezu rund um die Uhr, vereinfacht New Yorker Werktage
    "futures": Session(tz="America/New_York", open=time(0, 0), close=time(23, 59, 59), weekdays_only=True),
}

MARKET_TZ = {"crypto": "UTC", "xetra": "Europe/Berlin", "europe": "Europe/Berlin", "us": "America/New_York", "fx": "UTC",
             "uk": "Europe/London", "japan": "Asia/Tokyo", "hongkong": "Asia/Hong_Kong", "futures": "America/New_York"}


def session_for(market: str) -> Session:
    return SESSIONS[market]


def berlin_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ZoneInfo("Europe/Berlin"))
