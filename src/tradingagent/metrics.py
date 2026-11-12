"""Kennzahlen je Instrument für Top/Flop, Screener und Kategorien.

Alle Werte stammen aus den tatsächlich gespeicherten Kerzen. Fehlt eine
Grundlage (zu wenig Historie, Reihe reicht nicht bis zum Vergleichszeitpunkt,
kein Volumen), ist der Wert ``None`` – niemals 0.

Zeitbezug: Alle Zeitfenster enden beim Zeitpunkt des aktuellen Kurses
(``last_ts``). Bei geschlossenen Börsen sind das also die letzten 24 Stunden
bis zum letzten Handel; die App kennzeichnet das als „letzter Schlusskurs“.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

STEP = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

# Zeitfenster: Name -> (Sekunden, erlaubte Kerzenreihen, feinste zuerst)
WINDOWS: dict[str, tuple[int, tuple[str, ...]]] = {
    "5m": (300, ("5m",)),
    "15m": (900, ("5m", "15m")),
    "1h": (3600, ("5m", "15m")),
    "4h": (4 * 3600, ("5m", "15m", "1h")),
    "24h": (86400, ("5m", "15m", "1h")),
    "7d": (7 * 86400, ("1h", "1d")),
    "30d": (30 * 86400, ("1h", "1d")),
}

# Wie weit jede Reihe für die Kennzahlen geladen wird
LOAD_DAYS = {"5m": 3, "15m": 4, "1h": 40, "1d": 460}

# Mindestzahl Tageskerzen, ab der ein Indikator berechnet wird
MIN_BARS = {"rsi": 30, "ema20": 40, "ema50": 100, "ema200": 400, "sma200": 210, "macd": 60, "boll": 20, "vol": 21}


class Frames:
    """Lädt jede Kerzenreihe einmal (über den _Loader des Exports, also schon in Euro)."""

    def __init__(self, loader, now: pd.Timestamp):
        self.loader = loader
        self.now = now
        self._cache: dict[str, pd.DataFrame] = {}

    def get(self, interval: str) -> pd.DataFrame:
        if interval not in self._cache:
            start = self.now - pd.Timedelta(days=LOAD_DAYS[interval])
            self._cache[interval] = self.loader.load(interval, start)
        return self._cache[interval]


def price_at(frames: Frames, T: pd.Timestamp, intervals: tuple[str, ...]) -> float | None:
    """Schluss der letzten abgeschlossenen Kerze vor T aus der feinsten Reihe, die T abdeckt.

    „Abdecken“ heißt: Die Reihe hat eine Kerze, die nach T endet, und die letzte
    abgeschlossene Kerze vor T liegt höchstens vier Kerzenlängen (bzw. bei
    Handelspausen die Pause) zurück – sonst wäre der Vergleich verfälscht.
    """
    for interval in intervals:
        df = frames.get(interval)
        if df.empty:
            continue
        step = pd.Timedelta(seconds=STEP[interval])
        if df.index[-1] + step < T:
            continue  # Reihe reicht nicht bis T (z. B. verzögerte Daten)
        ends = df.index + step
        done = df[ends <= T]
        if done.empty:
            continue  # Reihe beginnt erst nach T
        return float(done["close"].iloc[-1])
    return None


def _closes_daily(frames: Frames, last: float | None, last_ts: pd.Timestamp | None) -> pd.Series:
    d = frames.get("1d")
    if d.empty:
        return pd.Series(dtype=float)
    closes = d["close"].astype(float).copy()
    if last is not None and last_ts is not None:
        if last_ts >= closes.index[-1] + pd.Timedelta(days=1):
            closes.loc[last_ts.normalize()] = last  # heutige Kerze fehlt noch: aktueller Kurs als Schluss
        elif last_ts >= closes.index[-1]:
            closes.iloc[-1] = last
    return closes


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> float | None:
    if len(s) < MIN_BARS["rsi"]:
        return None
    delta = s.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    g, l = float(gain.iloc[-1]), float(loss.iloc[-1])
    if l == 0:
        return 100.0 if g > 0 else 50.0
    return 100 - 100 / (1 + g / l)


def _cross(fast: pd.Series, slow: pd.Series, lookback: int = 10) -> str | None:
    """„golden“ bzw. „death“, wenn sich die Linien in den letzten `lookback` Kerzen gekreuzt haben."""
    diff = (fast - slow).dropna().iloc[-(lookback + 1):]
    if len(diff) < 2:
        return None
    sign = np.sign(diff.values)
    for i in range(len(sign) - 1, 0, -1):
        if sign[i] > 0 and sign[i - 1] <= 0:
            return "golden" if sign[-1] > 0 else None
        if sign[i] < 0 and sign[i - 1] >= 0:
            return "death" if sign[-1] < 0 else None
    return None


def _window_bars(frames: Frames, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
    """Kerzen im Fenster (start, end] aus der feinsten Reihe, die das Fenster ganz abdeckt."""
    for interval in ("5m", "15m", "1h"):
        df = frames.get(interval)
        if df.empty or df.index[0] > start:
            continue
        step = pd.Timedelta(seconds=STEP[interval])
        if df.index[-1] + step < end - pd.Timedelta(hours=1):
            continue
        win = df[(df.index >= start) & (df.index < end)]
        if not win.empty:
            return win
    return None


def compute(loader, now: pd.Timestamp, last: float | None, last_ts: pd.Timestamp | None,
            *, crypto: bool = False) -> dict[str, Any]:
    frames = Frames(loader, now)
    out: dict[str, Any] = {"perf": {}, "abs": {}}
    if last is None or last_ts is None:
        return out

    for name, (secs, intervals) in WINDOWS.items():
        ref = price_at(frames, last_ts - pd.Timedelta(seconds=secs), intervals)
        if ref:
            out["perf"][name] = round(last / ref - 1, 6)
            out["abs"][name] = _round(last - ref)

    # 24 Stunden: Umsatz, Hoch/Tief, Verlauf
    win = _window_bars(frames, last_ts - pd.Timedelta(hours=24), last_ts + pd.Timedelta(seconds=1))
    if win is not None:
        vol = float(win["volume"].sum())
        out["volume_24h"] = _round(vol) if vol > 0 else None
        turnover = float((win["close"] * win["volume"]).sum())
        out["turnover_24h"] = round(turnover) if turnover > 0 else None
        hi, lo = float(win["high"].max()), float(win["low"].min())
        out["high_24h"], out["low_24h"] = _round(hi), _round(lo)
        out["new_high_24h"] = last >= hi
        out["new_low_24h"] = last <= lo
        out["spark"] = _spark(win["close"], last)

    closes = _closes_daily(frames, last, last_ts)
    daily = frames.get("1d")
    n = len(closes)
    ind: dict[str, Any] = {}
    if n:
        ind["bars"] = n
    r = rsi(closes)
    if r is not None:
        ind["rsi14"] = round(r, 2)
    for k, span in (("ema20", 20), ("ema50", 50), ("ema200", 200)):
        if n >= MIN_BARS[k]:
            ind[k] = _round(float(ema(closes, span).iloc[-1]))
    if n >= MIN_BARS["sma200"]:
        ind["cross"] = _cross(closes.rolling(50).mean(), closes.rolling(200).mean())
    if n >= MIN_BARS["macd"]:
        macd = ema(closes, 12) - ema(closes, 26)
        sig = ema(macd, 9)
        ind["macd"] = _round(float(macd.iloc[-1]))
        ind["macd_signal"] = _round(float(sig.iloc[-1]))
    if n >= MIN_BARS["boll"]:
        mid = closes.rolling(20).mean()
        sd = closes.rolling(20).std(ddof=0)
        up, lo = float(mid.iloc[-1] + 2 * sd.iloc[-1]), float(mid.iloc[-1] - 2 * sd.iloc[-1])
        ind["boll_up"], ind["boll_low"] = _round(up), _round(lo)
    if n >= MIN_BARS["vol"]:
        rets = np.log(closes / closes.shift(1)).dropna().iloc[-30:]
        if len(rets) >= 20:
            ind["volatility"] = round(float(rets.std(ddof=1)) * math.sqrt(365 if crypto else 252), 4)
    out["ind"] = ind

    # Liquidität: durchschnittlicher Tagesumsatz der letzten 20 abgeschlossenen Tage
    if not daily.empty:
        d = daily[daily.index + pd.Timedelta(days=1) <= last_ts].iloc[-20:]
        turn = (d["close"] * d["volume"])
        turn = turn[turn > 0]
        if len(turn) >= 10:
            avg = float(turn.mean())
            out["avg_turnover_20d"] = round(avg)
            if out.get("turnover_24h"):
                out["rel_volume"] = round(out["turnover_24h"] / avg, 3)
    return out


def _spark(closes: pd.Series, last: float, points: int = 32) -> list[float]:
    vals = closes.astype(float).tolist()
    if len(vals) > points:
        idx = np.linspace(0, len(vals) - 1, points).round().astype(int)
        vals = [vals[i] for i in idx]
    vals.append(last)
    return [_round(v) for v in vals]


def _round(x: float) -> float:
    x = float(x)
    if abs(x) >= 1000:
        return round(x, 2)
    if abs(x) >= 1:
        return round(x, 4)
    return float(f"{x:.6g}")
