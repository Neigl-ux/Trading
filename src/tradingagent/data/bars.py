"""Einheitliches Kerzenformat.

Jede Datenquelle liefert ein pandas.DataFrame mit
- Index: Startzeit der Kerze, zeitzonenbewusst in UTC
- Spalten: open, high, low, close, volume (float)

So lassen sich Anbieter austauschen, ohne den Rest des Systems anzufassen.
"""

from __future__ import annotations

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="ts")
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS}, index=idx)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Bringt ein beliebiges OHLCV-Frame in das Standardformat."""
    if df is None or len(df) == 0:
        return empty_frame()
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    missing = [c for c in COLUMNS if c not in out.columns]
    if "volume" in missing:
        out["volume"] = 0.0
        missing.remove("volume")
    if missing:
        raise ValueError(f"Spalten fehlen: {missing}")
    out = out[COLUMNS].astype("float64")
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out.index = idx.rename("ts")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def drop_unclosed(df: pd.DataFrame, interval_delta: pd.Timedelta, now: pd.Timestamp,
                  delay: pd.Timedelta = pd.Timedelta(0)) -> pd.DataFrame:
    """Entfernt Kerzen, die noch nicht abgeschlossen sind.

    Eine Kerze gilt als abgeschlossen, wenn ihr Ende vor (jetzt - Verzögerung)
    liegt. Nur abgeschlossene Kerzen werden gespeichert, damit später kein
    Signal auf einer Kerze entsteht, die sich noch ändert (Look-ahead-Schutz).
    """
    if df.empty:
        return df
    cutoff = now - delay
    return df[df.index + interval_delta <= cutoff]
