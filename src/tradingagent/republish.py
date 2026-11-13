"""Schnelle Veröffentlichung auf GitHub Pages, wenn nur die App geändert wurde.

Hat ein Push nur Dateien außerhalb von src/ und config/ geändert (z. B. app/), sind die Kursdaten
unverändert gültig. Dann werden die aktuell veröffentlichten Daten-Dateien übernommen, statt alles neu
zu exportieren – das spart mehrere Minuten. Bei jedem Problem gilt: normaler Export.
"""
from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .crypto_box import open_sealed
from .notify import APP_URL

DATA_PREFIXES = ("src/", "config/")


def only_app_changed(event_path: str | None = None) -> bool:
    path = event_path or os.environ.get("GITHUB_EVENT_PATH")
    if os.environ.get("GITHUB_EVENT_NAME") != "push" or not path:
        return False
    try:
        ev = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    commits = ev.get("commits") or []
    if not commits or len(commits) >= 20:  # GitHub kürzt lange Listen: dann lieber vollständig exportieren
        return False
    files = [f for c in commits for k in ("added", "modified", "removed") for f in c.get(k, [])]
    return bool(files) and not any(f.startswith(DATA_PREFIXES) for f in files)


def fetch_published(export_dir: Path, passphrase: str, session=None, base: str | None = None) -> int:
    """Lädt alle veröffentlichten Daten-Dateien nach export_dir. Gibt die Anzahl zurück (0 = nicht möglich)."""
    import requests

    http = session or requests.Session()
    base = base or f"{APP_URL}data/"

    def get(rel: str) -> bytes | None:
        try:
            r = http.get(base + rel, timeout=30)
            return r.content if r.status_code == 200 and r.content.startswith(b"{") else None
        except Exception:  # noqa: BLE001
            return None

    raw = get("index.json")
    if raw is None:
        return 0
    doc = json.loads(raw)
    try:
        idx = open_sealed(passphrase, doc["kdf"], doc) if doc.get("enc") else doc.get("data", doc)
    except Exception:  # noqa: BLE001 – anderes Passwort oder Salz: neu exportieren
        return 0
    files = [e["file"] for e in idx.get("instruments", []) if e.get("file")]
    if idx.get("advisor"):
        files.append(idx["advisor"])
    with ThreadPoolExecutor(16) as pool:
        blobs = list(pool.map(get, files))
    if any(b is None for b in blobs):
        return 0
    tmp = export_dir.with_name(export_dir.name + ".republish")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, blob in zip(files, blobs):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    (tmp / "index.json").write_bytes(raw)
    shutil.rmtree(export_dir, ignore_errors=True)
    tmp.rename(export_dir)
    return len(files) + 1
