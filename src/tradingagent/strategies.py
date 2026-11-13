"""Phase 5 – Kaufsignale aus Tageskerzen.

Jede Strategie liefert für einen Wert zwei Reihen gleicher Länge:
  entry[t] = True  → am Schluss von Tag t ist ein Kaufsignal entstanden (gekauft wird zur Eröffnung von t+1)
  exit[t]  = True  → am Schluss von Tag t ist ein Verkaufssignal entstanden
Berechnet wird nur mit Daten bis einschließlich Tag t – kein Blick in die Zukunft (ein Test prüft das).

Die sechs Indikatoren stammen aus Sukma & Namahoot (2025): MA-Kreuz, RSI, MACD, Bollinger, OBV, Ichimoku.
Dazu ein Mehrheitsentscheid aus allen sechs. Nur Käufe (keine Leerverkäufe, siehe Phase 4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    idea: str
    params: tuple[dict, ...]                      # Kandidaten für den Walk-Forward
    fn: Callable[[pd.DataFrame, dict], tuple[pd.Series, pd.Series]]


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a > b) & (a.shift(1) <= b_prev)


def _cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a < b) & (a.shift(1) >= b_prev)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(down > 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast: int, slow: int, sig: int) -> tuple[pd.Series, pd.Series]:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return line, line.ewm(span=sig, adjust=False).mean()


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff()).fillna(0) * volume.fillna(0)).cumsum()


def ichimoku(df: pd.DataFrame, a: int = 9, b: int = 26, c: int = 52) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Tenkan, Kijun und die Wolke, wie sie am Tag t im Chart steht (Span A/B um b Tage nach vorn verschoben,
    also aus Daten von t−b berechnet – das ist kein Blick in die Zukunft)."""
    hi, lo = df["high"], df["low"]
    tenkan = (hi.rolling(a).max() + lo.rolling(a).min()) / 2
    kijun = (hi.rolling(b).max() + lo.rolling(b).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(b)
    span_b = ((hi.rolling(c).max() + lo.rolling(c).min()) / 2).shift(b)
    return tenkan, kijun, span_a, span_b


# ---------------------------------------------------------------- Zustände (bullish ja/nein) für den Mehrheitsentscheid
def _states(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    m, s = macd(c, 12, 26, 9)
    r = rsi(c)
    mid = c.rolling(20).mean()
    o = obv(c, df["volume"])
    t, k, sa, sb = ichimoku(df)
    return pd.DataFrame({
        "ma": ma20 > ma50,
        "rsi": (r > 50) & (r < 70),
        "macd": m > s,
        "boll": c > mid,
        "obv": o > o.rolling(20).mean(),
        "ichi": (c > np.fmax(sa, sb)) & (t > k),
    }).fillna(False)


# ---------------------------------------------------------------- Strategien
def s_ma(df, p):
    c = df["close"]
    f, s = c.rolling(p["fast"]).mean(), c.rolling(p["slow"]).mean()
    return _cross_up(f, s), _cross_down(f, s)


def s_rsi(df, p):
    r = rsi(df["close"], 14)
    return _cross_up(r, float(p["low"])), r > float(p["high"])


def s_macd(df, p):
    m, s = macd(df["close"], p["fast"], p["slow"], p["sig"])
    return _cross_up(m, s), _cross_down(m, s)


def s_boll(df, p):
    c = df["close"]
    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    lower = mid - p["k"] * sd
    return _cross_down(c, lower), c > mid  # unter das untere Band gefallen: Rückkehr zur Mitte erwartet


def s_obv(df, p):
    c = df["close"]
    o = obv(c, df["volume"])
    om = o.rolling(p["n"]).mean()
    trend = c > c.rolling(p["n"]).mean()
    return _cross_up(o, om) & trend, _cross_down(o, om)


def s_ichi(df, p):
    t, k, sa, sb = ichimoku(df)
    top, bottom = np.fmax(sa, sb), np.fmin(sa, sb)
    c = df["close"]
    above = (c > top) & (t > k)
    return above & ~above.shift(1, fill_value=False), c < bottom


def s_vote(df, p):
    st = _states(df)
    votes = st.sum(axis=1)
    bull = votes >= p["min"]
    return bull & ~bull.shift(1, fill_value=False), votes <= 6 - p["min"] - 1


STRATEGIES: tuple[Strategy, ...] = (
    Strategy("ma", "Gleitende Durchschnitte", "Kauf, wenn der schnelle Durchschnitt den langsamen von unten kreuzt; Verkauf beim Kreuzen nach unten.",
             ({"fast": 20, "slow": 50}, {"fast": 10, "slow": 30}, {"fast": 50, "slow": 200}), s_ma),
    Strategy("rsi", "RSI", "Kauf, wenn der RSI(14) aus dem überverkauften Bereich nach oben dreht; Verkauf, wenn er überkauft ist.",
             ({"low": 30, "high": 70}, {"low": 25, "high": 70}, {"low": 35, "high": 65}), s_rsi),
    Strategy("macd", "MACD", "Kauf, wenn die MACD-Linie ihre Signallinie nach oben kreuzt; Verkauf beim Kreuzen nach unten.",
             ({"fast": 12, "slow": 26, "sig": 9}, {"fast": 8, "slow": 17, "sig": 9}), s_macd),
    Strategy("boll", "Bollinger-Bänder", "Kauf, wenn der Kurs unter das untere Band fällt (Übertreibung); Verkauf zurück an der Mittellinie.",
             ({"k": 2.0}, {"k": 2.5}), s_boll),
    Strategy("obv", "On-Balance-Volumen", "Kauf, wenn das Volumen-Saldo über seinen Durchschnitt steigt und der Kurs im Aufwärtstrend ist.",
             ({"n": 20}, {"n": 50}), s_obv),
    Strategy("ichi", "Ichimoku", "Kauf, wenn der Kurs über die Wolke steigt und Tenkan über Kijun liegt; Verkauf unter der Wolke.",
             ({},), s_ichi),
    Strategy("vote", "Mehrheit aus allen sechs", "Kauf, wenn mindestens 4 (bzw. 5) der sechs Indikatoren positiv sind.",
             ({"min": 4}, {"min": 5}), s_vote),
)
BY_KEY = {s.key: s for s in STRATEGIES}


def signals(df: pd.DataFrame, key: str, params: dict) -> tuple[np.ndarray, np.ndarray]:
    entry, exit_ = BY_KEY[key].fn(df, params)
    return entry.fillna(False).to_numpy(bool), exit_.fillna(False).to_numpy(bool)
