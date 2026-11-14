"""Modellversionen: Datei je Version (joblib) und ein Verzeichnis in SQLite.

* Jede trainierte Version bekommt eine eindeutige Nummer (Zeitstempel + Modellart + Kurz-Prüfsumme).
* Gespeichert werden Trainingszeitpunkt, Datenumfang, Einstellungen, Kennzahlen, Zulassungsstatus.
* „Champion“ je Modellart = die beste ZUGELASSENE Version. Eine neue Version ersetzt ihn nur, wenn sie
  die Zulassung besteht UND besser abschneidet. Nicht zugelassene Versionen bleiben gespeichert.
* „Aktuell“ = die zuletzt trainierte Version (für Prognosen mit dem Hinweis „nicht freigegeben“).
* Ältere Versionen lassen sich wiederherstellen (restore).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

from ..config import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
  version TEXT PRIMARY KEY, kind TEXT, created TEXT, level INTEGER, data_from TEXT, data_to TEXT,
  n_rows INTEGER, params TEXT, metrics TEXT, status TEXT, reason TEXT, file TEXT);
CREATE TABLE IF NOT EXISTS pointers (name TEXT PRIMARY KEY, version TEXT, since TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, started TEXT, finished TEXT, seconds REAL, ok INTEGER, message TEXT);
"""


def model_dir() -> Path:
    d = Path(os.environ.get("TA_MODEL_DIR", str(ROOT / "models")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, root: Path | None = None):
        self.root = root or model_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "registry.sqlite", timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- Versionen
    def save(self, kind: str, model: Any, *, level: int, data_from: str, data_to: str, n_rows: int,
             params: dict, metrics: dict, admitted: bool, reason: str) -> str:
        blob = json.dumps({"kind": kind, "params": params, "data_to": data_to, "metrics": metrics}, sort_keys=True, default=str)
        now = datetime.now(timezone.utc)
        digest = hashlib.sha1((blob + now.isoformat()).encode()).hexdigest()[:6]
        version = f"{now:%Y%m%d-%H%M%S}-{kind}-{digest}"
        path = self.root / f"{version}.joblib"
        joblib.dump(model, path, compress=3)
        status = "zugelassen" if admitted else "nicht_freigegeben"
        with self.db:
            self.db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (version, kind, _now(), level, data_from, data_to, n_rows, json.dumps(params, default=str),
                             json.dumps(metrics, default=str), status, reason, path.name))
            self.db.execute("INSERT OR REPLACE INTO pointers VALUES (?,?,?)", (f"aktuell:{kind}", version, _now()))
        return version

    def promote_if_better(self, kind: str, version: str, score: float, admitted: bool) -> tuple[bool, str]:
        """Champion-Wechsel nur bei bestandener Zulassung und besserer Bewertung als der bisherige Champion."""
        if not admitted:
            return False, "nicht zugelassen – bisheriger Champion bleibt"
        cur = self.pointer(f"champion:{kind}")
        if cur:
            old = json.loads(self.get(cur)["metrics"]).get("bewertung", -1e9)
            if score <= old:
                return False, f"zugelassen, aber nicht besser als Champion {cur}"
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO pointers VALUES (?,?,?)", (f"champion:{kind}", version, _now()))
        return True, "neuer Champion"

    def restore(self, kind: str, version: str) -> None:
        row = self.get(version)
        if not row or row["kind"] != kind or row["status"] != "zugelassen":
            raise ValueError("Nur zugelassene Versionen derselben Modellart können Champion werden")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO pointers VALUES (?,?,?)", (f"champion:{kind}", version, _now()))

    def get(self, version: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM versions WHERE version=?", (version,)).fetchone()

    def pointer(self, name: str) -> str | None:
        r = self.db.execute("SELECT version FROM pointers WHERE name=?", (name,)).fetchone()
        return r["version"] if r else None

    def load(self, version: str) -> Any:
        row = self.get(version)
        if not row:
            raise KeyError(version)
        return joblib.load(self.root / row["file"])

    def versions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM versions ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"], d["metrics"] = json.loads(d["params"]), json.loads(d["metrics"])
            out.append(d)
        return out

    # ---- Läufe
    def log_run(self, job: str, started: str, seconds: float, ok: bool, message: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO runs (job, started, finished, seconds, ok, message) VALUES (?,?,?,?,?,?)",
                            (job, started, _now(), seconds, int(ok), message[:2000]))

    def runs(self, limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def close(self) -> None:
        self.db.close()
