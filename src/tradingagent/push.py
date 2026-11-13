"""Push-Nachrichten des Ratgebers (läuft stündlich in einem eigenen Ablauf).

- Nachkauf-Stufe erreicht (Bausteine des Sparplans): sofort, einmal je Stufe und Rückgang.
- Lagebericht: alle 2 Tage morgens.

Nachrichten nennen nur Wert und Anlass – keine Beträge, nichts aus dem Portfolio.
Der Zustand (was schon gesendet wurde) liegt in data/push_state.json, das der
Ablauf im Actions-Cache aufbewahrt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from urllib.parse import quote

from . import notify
from .config import ROOT, load_settings
from .storage import open_storage

STATE_PATH = ROOT / "data" / "push_state.json"
REPORT_EVERY_H = 47
REPORT_HOURS = range(7, 11)   # Berliner Zeit
STALE_H = 3


def _name(ids: dict[str, str], vid: str) -> str:
    return ids.get(vid, vid)


def stage_message(e: dict[str, Any]) -> tuple[str, str, list[str]]:
    dd = f"−{e['dd'] * 100:.0f} %"
    if e.get("check_news"):
        return (f"Erst Nachricht prüfen: {e['name']}",
                f"{e['name']} liegt {dd} unter dem 12-Monats-Hoch (Stufe {e['stage']}). Zu dem Wert gibt es auffällige "
                "Nachrichten. Bitte erst die Hinweise im Ratgeber lesen.", ["warning"])
    if not e.get("allowed", True):
        return (f"Rückgang ohne Nachkauf: {e['name']}",
                f"{e['name']} liegt {dd} unter dem 12-Monats-Hoch, die Punktzahl ({e['score']}) liegt aber unter 65. "
                "Laut Regel keine Nachkauf-Stufe.", ["chart_with_downwards_trend"])
    return (f"Nachkauf-Stufe {e['stage']}: {e['name']}",
            f"{e['name']} liegt {dd} unter dem 12-Monats-Hoch. Details und Rückblick-Ergebnis im Ratgeber.",
            ["shopping_cart"])


def report_message(r: dict[str, Any], names: dict[str, str]) -> str:
    lines = []
    world = r.get("world")
    if world:
        lines.append(f"Weltlage: {world}.")
    plan = r.get("plan") or []
    if plan:
        core = next((p for p in plan if p["role"] == "Kern"), None)
        sats = [p for p in plan if p["role"] != "Kern"]
        txt = f"Sparplan: Kern {_name(names, core['id'])}" if core else "Sparplan"
        if sats:
            txt += ", Satelliten " + ", ".join(_name(names, p["id"]) for p in sats)
        lines.append(txt + ".")
    if r.get("changes"):
        lines.append("Änderung: " + " ".join(c["text"] for c in r["changes"][-2:]))
    else:
        lines.append("Keine Änderung am Vorschlag.")
    for m in r.get("market") or []:
        if m["stage"] > 0:
            lines.append(f"{m['label']} −{m['dd'] * 100:.0f} % unter dem 12-Monats-Hoch.")
    up = r.get("up") or []
    down = r.get("down") or []
    if up:
        lines.append("Besser bewertet: " + ", ".join(f"{_name(names, v)} +{d}" for v, d in up) + ".")
    if down:
        lines.append("Schlechter bewertet: " + ", ".join(f"{_name(names, v)} {d}" for v, d in down) + ".")
    if r.get("stages"):
        lines.append("Nachkauf-Stufen: " + ", ".join(f"{e['name']} Stufe {e['stage']}" for e in r["stages"]) + ".")
    return " ".join(lines)


def run(storage, now: pd.Timestamp, state: dict[str, Any], names: dict[str, str],
        send: Callable[..., bool]) -> dict[str, Any]:
    updated = int(storage.get_meta("adv_updated") or 0)
    if now.timestamp() - updated > STALE_H * 3600:
        print("Ratgeber-Daten zu alt, keine Nachrichten.")
        return state
    sent: dict[str, int] = state.setdefault("sent", {})
    if not state.get("initialized"):
        # beim ersten Lauf nur Stufen der letzten 24 Stunden melden, nicht die ganze Vorgeschichte
        for e in json.loads(storage.get_meta("adv_events") or "[]"):
            if e["ts"] < now.timestamp() - 86400:
                sent[e["key"]] = e["ts"]
        state["initialized"] = True
    for e in json.loads(storage.get_meta("adv_events") or "[]"):
        if e["key"] in sent:
            continue
        title, msg, tags = stage_message(e)
        target = f"#chart/{quote(str(e['id']), safe='')}" if e.get("id") else "#advisor"
        if send(title, msg, tags=tags, priority=4, click=notify.APP_URL + target):
            sent[e["key"]] = int(now.timestamp())
    berlin = now.tz_convert("Europe/Berlin")
    last = state.get("last_report", 0)
    if now.timestamp() - last >= REPORT_EVERY_H * 3600 and berlin.hour in REPORT_HOURS:
        r = json.loads(storage.get_meta("adv_report") or "{}")
        if r and send("Lagebericht Ratgeber", report_message(r, names), tags=["newspaper"], priority=3,
                      click=notify.APP_URL + "#advisor"):
            state["last_report"] = int(now.timestamp())
    cutoff = now.timestamp() - 400 * 86400
    state["sent"] = {k: v for k, v in sent.items() if v >= cutoff}
    return state


def main() -> int:
    settings = load_settings()
    storage = open_storage(settings.storage_path)
    names = {i.id: i.name for i in settings.instruments}
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    if not os.environ.get("NTFY_TOPIC"):
        print("NTFY_TOPIC fehlt – keine Nachrichten.")
        return 0
    state = run(storage, pd.Timestamp.now(tz="UTC"), state, names, notify.send)
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
