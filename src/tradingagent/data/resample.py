"""Kerzen in längere Zeitebenen umrechnen (z. B. 15m -> 30m, 1d -> 1w)."""

from __future__ import annotations

import pandas as pd

from .bars import COLUMNS, empty_frame
from .timeframes import DERIVED_FROM

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

# pandas-Regeln, jeweils mit Kerzenbeginn als Zeitstempel.
_RULES = {
    "30m": "30min",
    "4h": "4h",
    "1w": "W-MON",
    "2w": "2W-MON",
    "1M": "MS",
}


def resample(df: pd.DataFrame, target: str, tz: str = "UTC") -> pd.DataFrame:
    """Rechnet ein Standard-Frame in die Zeitebene `target` um.

    `tz` bestimmt, in welcher Zeitzone Tages-, Wochen- und Monatsgrenzen
    liegen. Für Xetra ist das Europe/Berlin. Leere Zeiträume (Wochenende,
    Feiertage) erzeugen keine Kerzen.
    """
    if target not in _RULES:
        raise ValueError(f"Keine Umrechnung nach {target} definiert")
    if df.empty:
        return empty_frame()
    local = df.tz_convert(tz)
    if target in ("1w", "2w"):
        # Wochen beginnen montags; label/closed links -> Zeitstempel = Wochenbeginn.
        out = local.resample(_RULES[target], label="left", closed="left").agg(_AGG)
    elif target == "1M":
        out = local.resample("MS").agg(_AGG)
    else:
        out = local.resample(_RULES[target], label="left", closed="left").agg(_AGG)
    out = out.dropna(subset=["open", "close"])
    out.index = out.index.tz_convert("UTC").rename("ts")
    return out[COLUMNS]


def source_interval(target: str) -> str:
    return DERIVED_FROM[target]
