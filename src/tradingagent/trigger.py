"""Startet den GitHub-Workflow „Daten sammeln“ zuverlässig (für den Server, alle 15 Minuten).

GitHubs eigener Zeitplan lässt nachts und bei Last viele Läufe ausfallen. Der Server ruft
deshalb selbst die GitHub-API auf: Läuft gerade kein Sammel-Lauf, wird einer gestartet.
Benötigt GH_TOKEN (fein abgestufter Token, nur dieses Repository, Recht „Actions: Read and write“)
und GH_REPO (z. B. Neigl-ux/Trading).
"""
from __future__ import annotations

import os
import sys

import requests

WORKFLOW = os.environ.get("TA_WORKFLOW", "collect.yml")


def main() -> int:
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GH_REPO")
    if not token or not repo:
        print("GH_TOKEN oder GH_REPO fehlt.")
        return 2
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}"
    busy = 0
    for status in ("in_progress", "queued"):
        r = requests.get(f"{base}/runs", params={"status": status, "per_page": 5}, headers=h, timeout=20)
        if r.status_code != 200:
            print(f"GitHub antwortet {r.status_code}: {r.text[:200]}")
            return 1
        busy += r.json().get("total_count", 0)
    if busy:
        print(f"Es läuft bereits ein Sammel-Lauf ({busy}); nichts zu tun.")
        return 0
    r = requests.post(f"{base}/dispatches", json={"ref": os.environ.get("TA_REF", "main")}, headers=h, timeout=20)
    if r.status_code != 204:
        print(f"Start fehlgeschlagen ({r.status_code}): {r.text[:200]}")
        return 1
    print("Sammel-Lauf gestartet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
