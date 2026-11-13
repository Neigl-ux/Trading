"""Liveradar für den Server: beobachtet Kurse rund um die Uhr und meldet starke Bewegungen per Push.

Läuft dauerhaft (systemd-Dienst „ta-radar“). Jede Minute:
  * Tradegate-Kurse für ETFs und Aktien (während der Handelszeit 7:30–22 Uhr),
  * Binance für Kryptowährungen (immer),
  * Yahoo für Indizes (alle 5 Minuten).

Meldungen (je Wert und Tag pro Stufe nur einmal):
  * Tagesbewegung ab einer Schwelle je Art (ETF/Index ±2/3/5 %, Aktie ±5/8/12 %, Krypto 24 h ±7/10/15 %),
  * schnelle Bewegung innerhalb von 15 Minuten (ETF/Index ±1,5 %, Aktie ±3 %, Krypto ±4 %),
  * Nachkauf-Stufen für ETFs (−10/−15/−20/−25/−30 % vom 12-Monats-Hoch), einmal je Rückgang.

Welche Werte: alle Kernwerte (instruments.yaml) plus eine eigene Liste in RADAR_EXTRA
(Kennungen, durch Komma getrennt), z. B. deine Watchlist. Nachrichten nennen nur Wert und
Bewegung, keine Beträge.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from urllib.parse import quote

from . import notify
from .config import Instrument, load_settings
from .quotes import URL as TG_URL, parse_number

BERLIN = ZoneInfo("Europe/Berlin")
STATE = Path(os.environ.get("TA_RADAR_STATE", "/var/lib/trading-agent/radar_state.json"))
DAY_LEVELS = {"etf": (0.02, 0.03, 0.05, 0.08), "index": (0.02, 0.03, 0.05, 0.08), "aktie": (0.05, 0.08, 0.12, 0.20),
              "krypto": (0.07, 0.10, 0.15, 0.25)}
FAST = {"etf": 0.015, "index": 0.015, "aktie": 0.03, "krypto": 0.04}
STAGES = (0.10, 0.15, 0.20, 0.25, 0.30)
FAST_WINDOW = 15 * 60
# Volatilitätsindizes schwanken von Natur aus stark und sind keine Anlage: keine Meldungen.
NO_RADAR = {"VIX", "VDAX", "VSTOXX", "V2X", "VXN", "MOVE"}


@dataclass
class Watch:
    inst: Instrument
    prev_close: float | None = None
    high12: float | None = None
    hist: deque = field(default_factory=lambda: deque(maxlen=40))  # (Zeit, Kurs)


def day_level_crossed(move: float, levels: tuple[float, ...], done: list[float]) -> float | None:
    """Höchste neu überschrittene Stufe (gleiche Richtung zählt einmal pro Tag)."""
    hit = None
    for lv in levels:
        if abs(move) >= lv and (lv * (1 if move > 0 else -1)) not in done:
            hit = lv
    return hit


def stage_of(dd: float) -> int:
    return sum(1 for s in STAGES if dd >= s - 1e-9)


def fast_move(hist: deque, now: float, price: float) -> float | None:
    old = [p for t, p in hist if now - t >= FAST_WINDOW * 0.8 and now - t <= FAST_WINDOW * 1.6]
    if not old:
        return None
    return price / old[0] - 1


def tradegate_open(now: datetime) -> bool:
    loc = now.astimezone(BERLIN)
    return loc.weekday() < 5 and (7 * 60 + 30) <= loc.hour * 60 + loc.minute < 22 * 60


class Radar:
    def __init__(self, session: requests.Session | None = None, send=notify.send):
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = "Mozilla/5.0 (trading-agent radar)"
        self.send = send
        s = load_settings()
        extra = {x.strip() for x in os.environ.get("RADAR_EXTRA", "").split(",") if x.strip()}
        self.watch: dict[str, Watch] = {}
        for inst in s.instruments:
            if inst.kind not in DAY_LEVELS or inst.id in NO_RADAR:
                continue
            if inst.tier == "core" or inst.id in extra:
                self.watch[inst.id] = Watch(inst)
        self.state: dict[str, Any] = {"day": "", "done": {}, "stage": {}}
        self.last_daily = 0.0
        self.last_index = 0.0
        self.load_state()

    # ---------- Zustand ----------
    def load_state(self) -> None:
        try:
            self.state.update(json.loads(STATE.read_text()))
        except (OSError, ValueError):
            pass

    def save_state(self) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(self.state))
        except OSError as err:
            print(f"Zustand nicht gespeichert: {err}", flush=True)

    # ---------- Daten ----------
    def refresh_daily(self) -> None:
        """Vortagesschluss und 12-Monats-Hoch aus Yahoo-Tageskursen (einmal am Tag)."""
        import yfinance as yf

        syms = {w.inst.symbol_for("yahoo"): w for w in self.watch.values() if w.inst.symbol_for("yahoo")}
        if not syms:
            return
        raw = yf.download(list(syms), period="1y", interval="1d", auto_adjust=False, group_by="ticker",
                          threads=True, progress=False)
        today = datetime.now(BERLIN).date()
        for sym, w in syms.items():
            try:
                d = (raw[sym] if len(syms) > 1 else raw)["Close"].dropna()
            except KeyError:
                continue
            if d.empty:
                continue
            past = d[[ts.date() < today for ts in d.index]]
            w.prev_close = float(past.iloc[-1]) if len(past) else None
            w.high12 = float(d.max())

    def poll_tradegate(self) -> dict[str, tuple[float, float | None]]:
        items = [w for w in self.watch.values() if w.inst.isin and w.inst.kind in ("etf", "aktie")]
        out: dict[str, tuple[float, float | None]] = {}

        def one(w: Watch):
            try:
                r = self.http.get(TG_URL, params={"isin": w.inst.isin}, timeout=10)
                data = r.json() if r.status_code == 200 else None
            except (requests.RequestException, ValueError):
                return
            if not isinstance(data, dict):
                return
            last = parse_number(data.get("last"))
            delta = parse_number(str(data.get("delta", "")).replace("%", "").replace("+", ""))
            if last is not None:
                out[w.inst.id] = (last, delta / 100 if delta is not None else None)
            time.sleep(0.15)

        with ThreadPoolExecutor(4) as pool:
            list(pool.map(one, items))
        return out

    def poll_binance(self) -> dict[str, tuple[float, float | None]]:
        items = {w.inst.symbol_for("binance"): w for w in self.watch.values() if w.inst.symbol_for("binance")}
        if not items:
            return {}
        try:
            r = self.http.get("https://api.binance.com/api/v3/ticker/24hr",
                              params={"symbols": json.dumps(sorted(items), separators=(",", ":"))}, timeout=15)
            rows = r.json() if r.status_code == 200 else []
        except (requests.RequestException, ValueError):
            return {}
        out = {}
        for row in rows if isinstance(rows, list) else []:
            w = items.get(row.get("symbol"))
            if w:
                out[w.inst.id] = (float(row["lastPrice"]), float(row["priceChangePercent"]) / 100)
        return out

    def poll_indices(self) -> dict[str, tuple[float, float | None]]:
        import yfinance as yf

        syms = {w.inst.symbol_for("yahoo"): w for w in self.watch.values() if w.inst.kind == "index" and w.inst.symbol_for("yahoo")}
        if not syms:
            return {}
        raw = yf.download(list(syms), period="1d", interval="5m", group_by="ticker", threads=True, progress=False)
        out = {}
        for sym, w in syms.items():
            try:
                d = (raw[sym] if len(syms) > 1 else raw)["Close"].dropna()
            except KeyError:
                continue
            if len(d):
                last = float(d.iloc[-1])
                out[w.inst.id] = (last, last / w.prev_close - 1 if w.prev_close else None)
        return out

    # ---------- Auswertung ----------
    def check(self, now: datetime, quotes: dict[str, tuple[float, float | None]]) -> list[tuple[str, str, str, int, list[str]]]:
        day = now.astimezone(BERLIN).strftime("%Y-%m-%d")
        if self.state.get("day") != day:
            self.state["day"], self.state["done"] = day, {}
        done: dict[str, list] = self.state["done"]
        msgs = []
        ts = now.timestamp()
        for vid, (price, move) in quotes.items():
            w = self.watch[vid]
            kind = w.inst.kind
            if move is None and w.prev_close:
                move = price / w.prev_close - 1
            name = w.inst.name
            # 1. Tagesbewegung
            if move is not None:
                lv = day_level_crossed(move, DAY_LEVELS[kind], done.setdefault(vid, []))
                if lv is not None:
                    done[vid] += [x * (1 if move > 0 else -1) for x in DAY_LEVELS[kind] if x <= lv]
                    span = "in 24 Stunden" if kind == "krypto" else "heute"
                    msgs.append((vid, f"{name} {'+' if move > 0 else '−'}{abs(move) * 100:.1f} % {span}",
                                 f"{name} steht {span} {'+' if move > 0 else '−'}{abs(move) * 100:.1f} % "
                                 f"({'Anstieg' if move > 0 else 'Rückgang'} über {lv * 100:.0f} %).",
                                 4 if lv >= DAY_LEVELS[kind][2] else 3, ["chart_with_upwards_trend" if move > 0 else "chart_with_downwards_trend"]))
            # 2. Schnelle Bewegung
            fm = fast_move(w.hist, ts, price)
            w.hist.append((ts, price))
            key = f"fast{'+' if (fm or 0) > 0 else '-'}"
            if fm is not None and abs(fm) >= FAST[kind] and key not in done.setdefault(vid, []):
                done[vid].append(key)
                msgs.append((vid, f"{name}: {'+' if fm > 0 else '−'}{abs(fm) * 100:.1f} % in 15 Minuten",
                             f"Schnelle Bewegung bei {name}: {'+' if fm > 0 else '−'}{abs(fm) * 100:.1f} % in etwa 15 Minuten.",
                             4, ["zap"]))
            # 3. Nachkauf-Stufen (nur ETFs)
            if kind == "etf" and w.high12:
                high = max(w.high12, price)
                dd = 1 - price / high
                st = stage_of(dd)
                prev = int(self.state["stage"].get(vid, 0))
                if dd <= 0.001:
                    self.state["stage"][vid] = 0
                elif st > prev:
                    self.state["stage"][vid] = st
                    msgs.append((vid, f"Nachkauf-Stufe {st}: {name}",
                                 f"{name} liegt {dd * 100:.1f} % unter dem 12-Monats-Hoch. Stufe {st} deiner Pyramide ist erreicht. "
                                 "Details und Punktzahl im Ratgeber.", 4, ["moneybag"]))
        return msgs

    def tick(self, now: datetime | None = None, tradegate: dict[str, tuple[float, float | None]] | None = None) -> int:
        now = now or datetime.now(BERLIN)
        t = time.time()
        if t - self.last_daily > 6 * 3600 or (now.astimezone(BERLIN).hour == 7 and t - self.last_daily > 3600):
            try:
                self.refresh_daily()
                self.last_daily = t
            except Exception as err:  # noqa: BLE001 – Yahoo zeitweise nicht erreichbar
                print(f"Tageskurse: {err}", flush=True)
        quotes: dict[str, tuple[float, float | None]] = {}
        quotes.update(self.poll_binance())
        if tradegate is not None:  # Kurse kommen schon vom Live-Dienst
            quotes.update({k: v for k, v in tradegate.items() if k in self.watch})
        elif tradegate_open(now):
            quotes.update(self.poll_tradegate())
        if t - self.last_index > 300:
            try:
                quotes.update(self.poll_indices())
            except Exception as err:  # noqa: BLE001
                print(f"Indizes: {err}", flush=True)
            self.last_index = t
        msgs = self.check(now, quotes)
        for vid, title, text, prio, tags in msgs:
            ok = self.send(title, text, tags=tags, priority=prio, click=f"{notify.APP_URL}#chart/{quote(vid, safe='')}")
            print(f"{now:%H:%M} Push {'gesendet' if ok else 'NICHT gesendet'}: {title}", flush=True)
        self.save_state()
        return len(msgs)


def main() -> int:
    r = Radar()
    print(f"Liveradar: {len(r.watch)} Werte beobachtet", flush=True)
    while True:
        start = time.time()
        try:
            r.tick()
        except Exception as err:  # noqa: BLE001 – Dienst läuft weiter
            print(f"Fehler: {err}", flush=True)
        time.sleep(max(5.0, 60 - (time.time() - start)))


if __name__ == "__main__":
    raise SystemExit(main())
