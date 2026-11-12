"""Aktuelle Kurse von Tradegate für alle Werte mit ISIN.

Damit zeigt die Liste in der App denselben Kurs wie die Echtzeit-Anzeige im
Chart (gleiche Börse). Inoffizielle, öffentliche Kursadresse von Tradegate;
fällt sie aus, bleibt der letzte Yahoo-Kurs stehen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import requests

from .config import Settings

URL = "https://www.tradegatebsx.com/refresh.php"
TZ = "Europe/Berlin"


def parse_number(value) -> float | None:
    """Tradegate liefert mal Zahlen (212.75), mal Text ("1 371,80")."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    t = value.replace(" ", "").replace(" ", "").replace(" ", "")
    if not t or t == "-":
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def tradegate_open(ts: pd.Timestamp) -> bool:
    local = ts.tz_convert(TZ)
    return local.weekday() < 5 and (7 * 60 + 30) <= local.hour * 60 + local.minute < 22 * 60


def last_session_end(ts: pd.Timestamp) -> pd.Timestamp:
    """Ende der letzten Tradegate-Handelszeit (werktags 22:00 Uhr) vor ts."""
    local = ts.tz_convert(TZ)
    day = local.normalize()
    for _ in range(8):
        if day.weekday() < 5:
            close = day + pd.Timedelta(hours=22)
            if close <= local:
                return close.tz_convert("UTC")
        day -= pd.Timedelta(days=1)
    return ts


@dataclass
class Quote:
    last: float
    time: pd.Timestamp  # Zeitpunkt, für den der Kurs gilt


def fetch_quotes(settings: Settings, now: pd.Timestamp | None = None, session=None,
                 pause: float = 0.25) -> tuple[dict[str, Quote], list[str]]:
    now = now or pd.Timestamp.now(tz="UTC")
    http = session or requests.Session()
    as_of = now if tradegate_open(now) else last_session_end(now)
    quotes: dict[str, Quote] = {}
    errors: list[str] = []
    for inst in settings.instruments:
        if not inst.isin or not settings.markets[inst.market].enabled:
            continue
        try:
            r = http.get(URL, params={"isin": inst.isin}, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            data = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{inst.id}: {exc}")
            data = None
        last = parse_number(data.get("last")) if isinstance(data, dict) else None
        if last is None and isinstance(data, dict):
            bid, ask = parse_number(data.get("bid")), parse_number(data.get("ask"))
            last = (bid + ask) / 2 if bid is not None and ask is not None else None
        if last is not None:
            quotes[inst.id] = Quote(last, as_of)
        if pause:
            time.sleep(pause)
    return quotes, errors
