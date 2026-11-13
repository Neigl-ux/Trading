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
    files = [f for c in commits for k in ("added", "modified", "removed") for f in c.get(k, [])]
    if not files:  # Actions liefert die Dateilisten nicht immer mit: über die GitHub-API vergleichen
        files = _compare_files(ev)
    changed_data = [f for f in files if f.startswith(DATA_PREFIXES)]
    print(f"Geänderte Dateien: {len(files)}, davon Daten/Code: {len(changed_data)}", flush=True)
    return bool(files) and not changed_data


def _compare_files(ev: dict) -> list[str]:
    before, after = ev.get("before"), ev.get("after")
    repo = os.environ.get("GITHUB_REPOSITORY") or (ev.get("repository") or {}).get("full_name")
    if not (before and after and repo) or set(before) == {"0"}:
        return []
    try:
        import requests

        r = requests.get(f"https://api.github.com/repos/{repo}/compare/{before}...{after}", timeout=20,
                         headers={"Accept": "application/vnd.github+json"})
        data = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        return []
    files = data.get("files") or []
    if len(files) >= 300:  # Liste gekürzt
        return []
    return [f["filename"] for f in files] + [f["previous_filename"] for f in files if f.get("previous_filename")]


def fetch_published(export_dir: Path, passphrase: str, session=None, base: str | None = None) -> int:
    """Lädt alle veröffentlichten Daten-Dateien nach export_dir. Gibt die Anzahl zurück (0 = nicht möglich)."""
    import requests

    http = session or requests.Session()
    base = base or f"{APP_URL}data/"

    problems: list[str] = []

    def get(rel: str) -> bytes | None:
        import time as _t

        for attempt in range(4):
            try:
                r = http.get(base + rel, timeout=30)
                if r.status_code == 200 and r.content[:1] == b"{":
                    return r.content
                why = f"HTTP {r.status_code}"
            except Exception as err:  # noqa: BLE001
                why = type(err).__name__
            if attempt < 3:
                _t.sleep(1.5 * (attempt + 1))
        problems.append(f"{rel}: {why}")
        return None

    raw = get("index.json")
    if raw is None:
        print(f"Index nicht abrufbar: {problems}", flush=True)
        return 0
    doc = json.loads(raw)
    try:
        idx = open_sealed(passphrase, doc["kdf"], doc) if doc.get("enc") else doc.get("data", doc)
    except Exception as err:  # noqa: BLE001 – anderes Passwort oder Salz: neu exportieren
        print(f"Index nicht lesbar: {type(err).__name__}", flush=True)
        return 0
    files = [e["file"] for e in idx.get("instruments", []) if e.get("file")]
    if idx.get("advisor"):
        files.append(idx["advisor"])
    with ThreadPoolExecutor(8) as pool:
        blobs = list(pool.map(get, files))
    if any(b is None for b in blobs):
        print(f"{len(problems)} von {len(files)} Dateien fehlen, z. B. {problems[:3]}", flush=True)
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
