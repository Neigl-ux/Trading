"""Eingangsgrößen (Features) und Zielgröße für die Lernmodelle.

Zeitregel: Eine Zeile mit Datum t beschreibt die Lage am Schluss von Tag t. Alle Features nutzen nur
Kerzen bis einschließlich t; Nachrichten nur bis t−1 (das GDELT-Tagesarchiv erscheint erst am Folgetag).
Die Zielgröße schaut nach vorn: Kauf zur Eröffnung von t+1, Bewertung am Schluss von t+H.
Für Training und Prüfung wird deshalb das Datum des Zielendes (label_end) mitgeführt, um Überschneidungen
mit späteren Zeiträumen auszuschließen (Purging).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategies import macd, rsi

FEATURES = [
    "rsi14", "macd_line", "macd_hist", "ema20_dist", "ema50_dist", "ema200_dist", "bb_pctb", "bb_width",
    "atr_pct", "adx14", "vol_rel20", "vol_z60", "volat20", "mom5", "mom20", "mom60", "mom120", "ret1", "gap",
    "mkt_ret20", "mkt_above_ema200", "mkt_volat20", "ind_ret20", "news_tone_z", "news_conflict_z",
]
FEATURE_NAMES_DE = {
    "rsi14": "RSI (14)", "macd_line": "MACD-Linie", "macd_hist": "MACD-Histogramm", "ema20_dist": "Abstand EMA 20",
    "ema50_dist": "Abstand EMA 50", "ema200_dist": "Abstand EMA 200", "bb_pctb": "Lage im Bollinger-Band",
    "bb_width": "Breite der Bollinger-Bänder", "atr_pct": "ATR in %", "adx14": "ADX (14)", "vol_rel20": "Relatives Volumen",
    "vol_z60": "Volumen gegenüber 60 Tagen", "volat20": "Volatilität 20 Tage", "mom5": "Momentum 1 Woche",
    "mom20": "Momentum 1 Monat", "mom60": "Momentum 3 Monate", "mom120": "Momentum 6 Monate", "ret1": "Tagesrendite",
    "gap": "Eröffnungslücke", "mkt_ret20": "Markt 1 Monat (MSCI World)", "mkt_above_ema200": "Markt über EMA 200",
    "mkt_volat20": "Marktvolatilität", "ind_ret20": "Branche 1 Monat", "news_tone_z": "Nachrichtenton (Vortag)",
    "news_conflict_z": "Konfliktmeldungen (Vortag)",
}


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, lo, c = df["high"], df["low"], df["close"]
    up, down = h.diff(), -lo.diff()
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False, min_periods=n).mean()


def market_frame(bench: pd.DataFrame) -> pd.DataFrame:
    c = bench["close"]
    r = c.pct_change()
    return pd.DataFrame({
        "mkt_ret20": c / c.shift(20) - 1,
        "mkt_above_ema200": (c > c.ewm(span=200, adjust=False, min_periods=150).mean()).astype(float),
        "mkt_volat20": r.rolling(20).std(),
    })


def news_frame(hist: pd.DataFrame | None) -> pd.DataFrame | None:
    """Nachrichten-Signal aus dem GDELT-Archiv, um einen Tag verzögert (erscheint erst am Folgetag)."""
    if hist is None or hist.empty:
        return None
    from ..news_archive import signal_frame

    sf = signal_frame(hist)
    out = pd.DataFrame(index=sf.index)
    out["news_tone_z"] = sf.get("tone")
    out["news_conflict_z"] = sf.get("conflict")
    out = out.shift(1)  # Wert vom Vortag
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out


def instrument_features(df: pd.DataFrame, market: pd.DataFrame | None = None, industry_ret20: pd.Series | None = None,
                        news: pd.DataFrame | None = None) -> pd.DataFrame:
    """Features eines Werts; Index = Handelstage des Werts."""
    o, h, lo, c, v = (df[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    r = c.pct_change()
    m_line, m_sig = macd(c, 12, 26, 9)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    atr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    vol = v.where(v > 0)
    logv = np.log(vol)
    f = pd.DataFrame(index=df.index)
    f["rsi14"] = rsi(c, 14) / 100
    f["macd_line"] = m_line / c
    f["macd_hist"] = (m_line - m_sig) / c
    for n in (20, 50, 200):
        f[f"ema{n}_dist"] = c / c.ewm(span=n, adjust=False, min_periods=int(n * 0.75)).mean() - 1
    f["bb_pctb"] = (c - (mid - 2 * sd)) / (4 * sd).replace(0, np.nan)
    f["bb_width"] = 4 * sd / mid
    f["atr_pct"] = atr / c
    f["adx14"] = adx(df) / 100
    f["vol_rel20"] = vol / vol.rolling(20, min_periods=10).mean()
    f["vol_z60"] = (logv - logv.rolling(60, min_periods=30).mean()) / logv.rolling(60, min_periods=30).std()
    f["volat20"] = r.rolling(20).std()
    for n in (5, 20, 60, 120):
        f[f"mom{n}"] = c / c.shift(n) - 1
    f["ret1"] = r
    f["gap"] = o / c.shift(1) - 1
    idx = pd.DatetimeIndex(df.index)
    if market is not None:
        f = f.join(market.reindex(idx, method="ffill"))
    if industry_ret20 is not None:
        f["ind_ret20"] = industry_ret20.reindex(idx, method="ffill")
    if news is not None:
        f = f.join(news.reindex(idx, method="ffill"))
    for col in FEATURES:  # fehlende Quellen: neutraler Wert, die Datenqualität vermerkt das
        if col not in f:
            f[col] = 0.0
    f["vol_rel20"] = f["vol_rel20"].fillna(1.0)
    f["vol_z60"] = f["vol_z60"].fillna(0.0)
    for col in ("ind_ret20", "news_tone_z", "news_conflict_z", "mkt_ret20", "mkt_volat20"):
        f[col] = f[col].fillna(0.0)
    f["mkt_above_ema200"] = f["mkt_above_ema200"].fillna(0.5)
    return f[FEATURES].replace([np.inf, -np.inf], np.nan)


def target(df: pd.DataFrame, horizon: int, min_move: float) -> pd.DataFrame:
    """Zielgröße am Tag t: Kauf zur Eröffnung t+1, Schluss t+H; positiv ab min_move (z. B. 0,02)."""
    o, c = df["open"].astype(float), df["close"].astype(float)
    entry = o.shift(-1)
    exit_ = c.shift(-horizon)
    fwd = exit_ / entry - 1
    end = pd.Series(df.index, index=df.index).shift(-horizon)
    out = pd.DataFrame({"fwd": fwd, "label_end": end}, index=df.index)
    out["y"] = (fwd >= min_move).astype(float).where(fwd.notna())
    return out


def industry_returns(frames: dict[str, pd.DataFrame], industry: dict[str, str]) -> dict[str, pd.Series]:
    """Mittlere 1-Monats-Rendite der ANDEREN Werte derselben Branche (laut Datenquelle); ohne Branche: nichts."""
    groups: dict[str, list[str]] = {}
    for sym, ind in industry.items():
        if ind and sym in frames:
            groups.setdefault(ind, []).append(sym)
    r20 = {s: frames[s]["close"] / frames[s]["close"].shift(20) - 1 for g in groups.values() if len(g) > 1 for s in g}
    out = {}
    for g in groups.values():
        if len(g) < 2:
            continue
        panel = pd.DataFrame({s: r20[s] for s in g})
        for s in g:
            out[s] = panel.drop(columns=s).mean(axis=1, skipna=True)
    return out


def build_dataset(frames: dict[str, pd.DataFrame], bench_sym: str, *, horizon: int, min_move: float,
                  industry: dict[str, str] | None = None, news_hist: pd.DataFrame | None = None) -> pd.DataFrame:
    """Lange Tabelle (eine Zeile je Wert und Tag) mit Features, Zielgröße, fwd und label_end."""
    market = market_frame(frames[bench_sym]) if bench_sym in frames else None
    news = news_frame(news_hist)
    ind = industry_returns(frames, industry or {})
    parts = []
    for sym, df in frames.items():
        if len(df) < 260:
            continue
        f = instrument_features(df, market, ind.get(sym), news)
        t = target(df, horizon, min_move)
        part = f.join(t)
        part["sym"] = sym
        part.index.name = "date"
        parts.append(part.reset_index())
    data = pd.concat(parts, ignore_index=True)
    data = data.dropna(subset=FEATURES)
    return data.sort_values(["date", "sym"]).reset_index(drop=True)
