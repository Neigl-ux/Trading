"""SQLite-Speicher: eine Datei, keine Installation nötig."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from ..data.bars import COLUMNS, empty_frame
from .base import Storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    instrument TEXT NOT NULL,
    interval   TEXT NOT NULL,
    ts         INTEGER NOT NULL,   -- Kerzenbeginn, Unix-Sekunden UTC
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (instrument, interval, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    summary TEXT NOT NULL
);
"""


class SQLiteStorage(Storage):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def upsert_bars(self, instrument: str, interval: str, df: pd.DataFrame, source: str) -> int:
        if df.empty:
            return 0
        before = self._count(instrument, interval)
        rows = [
            (instrument, interval, int(ts.timestamp()), float(r.open), float(r.high), float(r.low),
             float(r.close), float(r.volume), source)
            for ts, r in df.iterrows()
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(instrument, interval, ts) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                "volume=excluded.volume, source=excluded.source",
                rows,
            )
        return self._count(instrument, interval) - before

    def _count(self, instrument: str, interval: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM bars WHERE instrument=? AND interval=?", (instrument, interval)
        ).fetchone()[0]

    def load_bars(self, instrument: str, interval: str, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None) -> pd.DataFrame:
        sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE instrument=? AND interval=?"
        params: list[Any] = [instrument, interval]
        if start is not None:
            sql += " AND ts >= ?"
            params.append(int(start.timestamp()))
        if end is not None:
            sql += " AND ts < ?"
            params.append(int(end.timestamp()))
        sql += " ORDER BY ts"
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return empty_frame()
        df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
        df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True).rename("ts")
        return df.astype("float64")

    def last_ts(self, instrument: str, interval: str) -> pd.Timestamp | None:
        row = self.conn.execute(
            "SELECT MAX(ts) FROM bars WHERE instrument=? AND interval=?", (instrument, interval)
        ).fetchone()
        return None if row[0] is None else pd.Timestamp(row[0], unit="s", tz="UTC")

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, value))

    def log_run(self, summary: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO runs (started, summary) VALUES (?, ?)",
                              (summary.get("started", ""), json.dumps(summary, default=str)))
            # nur die letzten 2000 Läufe behalten (ca. 3 Wochen bei 15-Minuten-Takt)
            self.conn.execute("DELETE FROM runs WHERE id <= (SELECT MAX(id) FROM runs) - 2000")

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT summary FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def counts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT instrument, interval, COUNT(*), MIN(ts), MAX(ts) FROM bars "
            "GROUP BY instrument, interval ORDER BY instrument, interval"
        ).fetchall()
        return [
            {"instrument": r[0], "interval": r[1], "bars": r[2],
             "first": pd.Timestamp(r[3], unit="s", tz="UTC"), "last": pd.Timestamp(r[4], unit="s", tz="UTC")}
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()
