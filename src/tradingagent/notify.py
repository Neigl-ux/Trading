"""Push-Benachrichtigungen aufs Handy über ntfy (https://ntfy.sh, kostenlos).

Der Kanal (NTFY_TOPIC) ist ein zufälliger, geheimer Name; er steht nur als
GitHub-Secret bzw. Umgebungsvariable bereit, nie im Code. Nachrichten nennen
nur Wert und Anlass, keine Beträge und nichts aus dem Portfolio.
"""

from __future__ import annotations

import os

import requests

APP_URL = "https://neigl-ux.github.io/Trading/"


def send(title: str, message: str, *, tags: list[str] | None = None, priority: int = 3,
         click: str = APP_URL, topic: str | None = None, session=None) -> bool:
    topic = topic or os.environ.get("NTFY_TOPIC")
    if not topic:
        print("Keine Benachrichtigung: NTFY_TOPIC fehlt.")
        return False
    http = session or requests
    r = http.post("https://ntfy.sh/", json={"topic": topic, "title": title, "message": message,
                                           "tags": tags or [], "priority": priority, "click": click}, timeout=15)
    if r.status_code != 200:
        print(f"ntfy antwortet {r.status_code}: {r.text[:200]}")
        return False
    return True


def main() -> int:
    ok = send("Trading-Agent: Test", "Push-Benachrichtigungen sind eingerichtet. Hier erscheinen künftig Lageberichte und Nachkauf-Hinweise.",
              tags=["white_check_mark"])
    print("Testnachricht gesendet." if ok else "Testnachricht nicht gesendet.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
