"""Nachrichten für den Ratgeber über die freie GDELT-DOC-2.0-Schnittstelle.

GDELT durchsucht weltweite Online-Nachrichten in 65 Sprachen (maschinell ins
Englische übersetzt) der letzten 3 Monate, ohne Schlüssel. Die Betreiber bitten
um höchstens eine Anfrage alle 5 Sekunden; deshalb arbeitet jeder Sammel-Lauf
nur wenige Anfragen mit festem Zeitbudget ab und merkt sich den Stand.

Zwei Arten von Abfragen:
- Weltlage: Tonfall-Verlauf (30 Tage) zu Wirtschaft, Zinsen, Handel, Konflikten, Regionen.
- Ereignisse je Aktie: Artikel der letzten 3 Tage zu Klage, Gewinnwarnung, Betrug,
  Insolvenz, Rückruf, Sanktionen, Herabstufung, Ermittlungen.

Nachrichten ändern die Punktzahl nicht (siehe Konzeption). Sie erzeugen Hinweise
und werden für einen späteren Rückblick-Test täglich mitgeschrieben.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import pandas as pd
import requests

API = "https://api.gdeltproject.org/api/v2/doc/doc"
SPACING_S = 5.5
WORLD_REFRESH_H = 6
VALUE_REFRESH_H = 12
FLAG_MIN_ARTICLES = 2

WORLD_TOPICS: dict[str, tuple[str, str]] = {
    "economy": ("Weltwirtschaft", '("global economy" OR recession OR "economic outlook" OR "world economy")'),
    "rates": ("Zinsen und Notenbanken", '("central bank" OR "interest rates" OR "Federal Reserve" OR "European Central Bank")'),
    "trade": ("Handel und Zölle", '(tariffs OR "trade war" OR "export controls" OR "trade dispute")'),
    "conflict": ("Kriege und Konflikte", '(war OR invasion OR "military escalation" OR "armed conflict")'),
    "us": ("USA", '("US economy" OR "Wall Street" OR "US stocks")'),
    "europe": ("Europa", '("eurozone" OR "European economy" OR "European stocks")'),
    "china": ("China", '("Chinese economy" OR "China economy" OR "Chinese stocks")'),
}

EVENT_TERMS = ('(lawsuit OR "profit warning" OR fraud OR bankruptcy OR insolvency OR recall OR sanctions '
               'OR downgrade OR investigation OR "guidance cut")')

_SUFFIX = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ag|se|sa|s\.a|plc|nv|n\.v|ltd|limited|"
                     r"holding|holdings|group|kgaa|vz|st|adr|class|cl|the|registered|reg|shares?|a|b|c)\b\.?", re.I)


def clean_name(name: str) -> str | None:
    """Firmenname für die Suche: ohne Rechtsform, Klammern, Aktiengattung."""
    n = re.sub(r"\(.*?\)", " ", name or "")
    n = re.sub(r"[^\w&\- ]", " ", n)
    n = _SUFFIX.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip(" -&")
    words = n.split()[:3]
    n = " ".join(words)
    return n if len(n) >= 4 else None


class Gdelt:
    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.http = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self._last = -1e9
        self.requests = 0

    def get(self, params: dict[str, str]) -> dict[str, Any] | None:
        wait = SPACING_S - (self.clock() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()
        self.requests += 1
        r = self.http.get(API, params={**params, "format": "json"}, timeout=25,
                          headers={"User-Agent": "trading-agent (private research)"})
        if r.status_code != 200:
            raise RuntimeError(f"GDELT antwortet {r.status_code}")
        text = r.text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            raise RuntimeError(f"GDELT: {text[:120]}")


def tone_series(data: dict[str, Any]) -> list[list[Any]]:
    """Tonfall je Tag [[YYYY-MM-DD, Wert], …] aus einer timelinetone-Antwort."""
    out: dict[str, list[float]] = {}
    for series in data.get("timeline") or []:
        for p in series.get("data") or []:
            d = str(p.get("date", ""))[:8]
            if len(d) == 8 and p.get("value") is not None:
                out.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}", []).append(float(p["value"]))
    return [[d, round(sum(v) / len(v), 3)] for d, v in sorted(out.items())]


def tone_state(series: list[list[Any]]) -> dict[str, Any] | None:
    """Letzte 7 Tage gegen die Wochen davor. „angespannt“ = deutlich negativer geworden."""
    if len(series) < 10:
        return None
    vals = [v for _, v in series]
    recent = sum(vals[-7:]) / 7
    before = sum(vals[:-7]) / len(vals[:-7])
    change = recent - before
    level = "angespannt" if change <= -0.5 and recent < -2.0 else "ruhig" if change > -0.25 else "leicht negativer"
    return {"recent": round(recent, 2), "before": round(before, 2), "change": round(change, 2), "level": level}


def articles(data: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    out = []
    for a in (data.get("articles") or [])[:limit]:
        out.append({"t": (a.get("title") or "").strip()[:180], "u": a.get("url", ""), "d": a.get("domain", ""),
                    "l": a.get("language", ""), "s": a.get("seendate", "")})
    return out


def update(storage, now: pd.Timestamp, targets: list[tuple[str, str]], *, budget_s: float = 40,
           max_requests: int = 6, client: Gdelt | None = None) -> dict[str, Any]:
    """Arbeitet fällige Abfragen ab und gibt den gesamten Nachrichtenstand zurück.

    ``targets`` = [(Wert-Kennung, Name)] in Reihenfolge der Wichtigkeit.
    """
    state = json.loads(storage.get_meta("news_state") or '{"world": {}, "values": {}}')
    world: dict[str, Any] = state.setdefault("world", {})
    values: dict[str, Any] = state.setdefault("values", {})
    client = client or Gdelt()
    t_start = client.clock()
    ts = int(now.timestamp())
    errors: list[str] = []

    def time_left() -> bool:
        return client.requests < max_requests and client.clock() - t_start + SPACING_S < budget_s

    due_world = [k for k in WORLD_TOPICS if ts - world.get(k, {}).get("t", 0) >= WORLD_REFRESH_H * 3600]
    due_world.sort(key=lambda k: world.get(k, {}).get("t", 0))
    for key in due_world[:2]:  # höchstens zwei Weltthemen pro Lauf
        if not time_left():
            break
        try:
            data = client.get({"query": WORLD_TOPICS[key][1], "mode": "timelinetone", "timespan": "30d"})
            series = tone_series(data or {})
            world[key] = {"t": ts, "series": series[-31:], "state": tone_state(series)}
        except Exception as exc:  # noqa: BLE001 - Nachrichten dürfen den Lauf nie stören
            errors.append(f"{key}: {exc}")
            world.setdefault(key, {})["t"] = ts - (WORLD_REFRESH_H - 1) * 3600  # in einer Stunde erneut
            break

    wanted = {i for i, _ in targets}
    for vid in list(values):
        if vid not in wanted and ts - values[vid].get("t", 0) > 7 * 86400:
            del values[vid]  # alte Einträge aufräumen
    for vid, name in targets:
        if not time_left():
            break
        if ts - values.get(vid, {}).get("t", 0) < VALUE_REFRESH_H * 3600:
            continue
        q = clean_name(name)
        if not q:
            values[vid] = {"t": ts, "n": 0, "a": [], "q": None}
            continue
        try:
            data = client.get({"query": f'"{q}" {EVENT_TERMS}', "mode": "artlist", "maxrecords": "10",
                               "timespan": "3d", "sort": "datedesc"})
            arts = articles(data or {}, limit=10)
            values[vid] = {"t": ts, "n": len(arts), "a": arts[:5], "q": q}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{vid}: {exc}")
            break

    # Tagesarchiv für einen späteren Rückblick-Test
    hist = json.loads(storage.get_meta("news_hist") or "{}")
    day = now.strftime("%Y-%m-%d")
    hist[day] = {"w": {k: (v.get("state") or {}).get("recent") for k, v in world.items()},
                 "v": {k: v.get("n", 0) for k, v in values.items()}}
    for old in sorted(hist)[:-400]:
        del hist[old]
    storage.set_meta("news_hist", json.dumps(hist, separators=(",", ":")))
    storage.set_meta("news_state", json.dumps(state, separators=(",", ":")))
    state["errors"] = errors
    state["requests"] = client.requests
    return state


def world_summary(state: dict[str, Any]) -> dict[str, Any]:
    topics = []
    for key, (label, _) in WORLD_TOPICS.items():
        w = state.get("world", {}).get(key) or {}
        topics.append({"key": key, "label": label, "state": w.get("state"), "series": w.get("series", []),
                       "t": w.get("t")})
    tense = [t for t in topics if (t["state"] or {}).get("level") == "angespannt"]
    known = [t for t in topics if t["state"]]
    level = None if not known else "angespannt" if len(tense) >= 2 else "teilweise angespannt" if tense else "ruhig"
    from . import news_archive

    archive = news_archive.current_state()
    if archive and archive.get("level"):
        # Langfristiger Verlauf (Archiv seit 2013) zählt mit; der schlechtere Befund gewinnt.
        order = ["ruhig", "etwas angespannt", "teilweise angespannt", "angespannt"]
        level = archive["level"] if level is None else max(level, archive["level"], key=order.index)
    return {"level": level, "topics": topics, "archive": archive}


def flagged(entry: dict[str, Any] | None) -> bool:
    return bool(entry) and entry.get("n", 0) >= FLAG_MIN_ARTICLES
