"""Speicher-Schnittstelle. Heute SQLite, später optional PostgreSQL auf dem Server."""

from .base import Storage
from .sqlite_store import SQLiteStorage


def open_storage(path) -> Storage:
    return SQLiteStorage(path)


__all__ = ["Storage", "SQLiteStorage", "open_storage"]
