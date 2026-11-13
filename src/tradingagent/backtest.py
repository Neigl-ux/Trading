"""Rückblick-Test des Ratgebers.

Zwei Fragen, beide nur mit Daten, die zum jeweiligen Zeitpunkt vorlagen:

1. Nachkauf-Stufen: Lohnt es sich, 1.000 € Reserve gestaffelt bei Rückgängen
   (−10/−15/−20/−25/−30 % vom 12-Monats-Hoch → 10/15/20/25/30 %) anzulegen,
   statt alles sofort? Geprüft für jeden Monatsanfang als Startpunkt, Ergebnis
   nach 3 und 5 Jahren.
2. Punktzahl: Haben Werte mit hoher Punktzahl danach besser abgeschnitten als
   Werte mit niedriger? Punktzahl an jedem Monatsende, gemessen wird die
   Entwicklung der folgenden 3, 6 und 12 Monate.

Grenzen (stehen auch im Ergebnis): Kurse von Yahoo inkl. Dividenden
(auto_adjust), in Handelswährung. Getestet werden die Werte, die es heute gibt;
Firmen, die zwischenzeitlich verschwunden sind, fehlen (Überlebenden-Verzerrung,
macht Einzelaktien zu gut). Keine Gebühren, keine Steuern.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import scoring
from .config import ROOT, load_settings

STAGES = [(0.10, 0.10), (0.15, 0.15), (0.20, 0.20), (0.25, 0.25), (0.30, 0.30)]  # (Rückgang, Anteil der Reserve)

# Reihen für den Nachkauf-Test: (Name, Yahoo-Symbol, ab)
PYRAMID_SERIES = [
    ("MSCI World (iShares, in €)", "EUNL.DE", "2009-10-01"),
    ("S&P 500 inkl. Dividenden", "^SP500TR", "1990-01-01"),
    ("DAX", "^GDAXI", "1990-01-01"),
    ("Nasdaq 100", "^NDX", "1990-01-01"),
]
SCORE_FROM = "2012-01-01"
HORIZONS = {"3M": 63, "6M": 126, "12M": 252}


# ----------------------------------------------------------------------------
# 1. Nachkauf-Stufen
# ----------------------------------------------------------------------------

def rolling_high(close: np.ndarray) -> np.ndarray:
    return pd.Series(close).rolling(252, min_periods=1).max().to_numpy()


def simulate_pyramid(close: np.ndarray, start: int, end: int, *, cash_rate: float = 0.0,
                     allowed: np.ndarray | None = None, deploy_after: int | None = None,
                     high: np.ndarray | None = None) -> dict[str, float]:
    """Spielt die Staffel für 1 Einheit Reserve von Index ``start`` bis ``end`` durch.

    ``close`` braucht mindestens 252 Kurse vor ``start`` (für das 12-Monats-Hoch).
    ``allowed[i]`` False = Stufe an Tag i nicht erlaubt (z. B. Punktzahl unter 65).
    ``deploy_after`` = nach so vielen Handelstagen den Rest sofort anlegen.
    """
    cash, units = 1.0, 0.0
    hit: set[int] = set()
    daily_rate = (1 + cash_rate) ** (1 / 252) - 1
    bought_first: int | None = None
    if high is None:
        high = rolling_high(close)
    for i in range(start, end + 1):
        cash *= 1 + daily_rate
        dd = 1 - close[i] / high[i]
        if dd <= 1e-9:
            hit.clear()  # neues 12-Monats-Hoch: Stufen wieder frei
        for k, (thr, share) in enumerate(STAGES):
            if k in hit or dd < thr - 1e-9:
                continue
            if allowed is not None and not allowed[i]:
                continue
            amt = min(share, cash)
            if amt > 0:
                units += amt / close[i]
                cash -= amt
                bought_first = bought_first if bought_first is not None else i - start
            hit.add(k)
        if deploy_after is not None and i - start == deploy_after and cash > 0:
            units += cash / close[i]
            cash = 0.0
    value = units * close[end] + cash
    return {"value": value, "cash_left": cash, "first_buy_days": -1 if bought_first is None else bought_first}


def pyramid_study(close: pd.Series, horizon_days: int, *, cash_rate: float = 0.0,
                  allowed: pd.Series | None = None, deploy_after: int | None = None) -> dict[str, Any] | None:
    s = close.dropna()
    c = s.to_numpy()
    idx = s.index
    allow = None if allowed is None else allowed.reindex(idx).fillna(False).to_numpy(bool)
    # Startpunkte: erster Handelstag jedes Monats, mit 1 Jahr Vorlauf und vollem Zeitraum danach
    months = pd.Series(np.arange(len(idx)), index=idx).groupby([idx.year, idx.month]).first().to_numpy()
    starts = [int(i) for i in months if i >= 252 and i + horizon_days < len(c)]
    if not starts:
        return None
    rel, cash_left, never = [], [], 0
    high = rolling_high(c)
    for st in starts:
        end = st + horizon_days
        lump = c[end] / c[st]
        r = simulate_pyramid(c, st, end, cash_rate=cash_rate, allowed=allow, deploy_after=deploy_after, high=high)
        rel.append(r["value"] / lump - 1)
        cash_left.append(r["cash_left"])
        never += r["first_buy_days"] < 0
    rel_a = np.array(rel)
    return {
        "windows": len(starts),
        "from": str(idx[starts[0]].date()), "to": str(idx[starts[-1]].date()),
        "win_share": round(float(np.mean(rel_a >= 0)), 3),
        "median_diff": round(float(np.median(rel_a)), 4),
        "mean_diff": round(float(np.mean(rel_a)), 4),
        "worst_diff": round(float(np.min(rel_a)), 4),
        "best_diff": round(float(np.max(rel_a)), 4),
        "avg_cash_left": round(float(np.mean(cash_left)), 3),
        "never_bought_share": round(never / len(starts), 3),
    }


def score_allowed(close: pd.Series, kind: str = "etf", name: str = "", threshold: float = scoring.INTERESTING) -> pd.Series:
    """Für jeden Tag: Hätte die Punktzahl an diesem Tag die Stufe erlaubt? (Monatsweise berechnet.)"""
    s = close.dropna()
    c = s.to_numpy()
    out = pd.Series(False, index=s.index)
    month_end = pd.Series(np.arange(len(s)), index=s.index).groupby([s.index.year, s.index.month]).last().to_numpy()
    prev = 0
    ok = False
    for i in month_end:
        out.iloc[prev:i + 1] = ok  # gilt bis zum nächsten Monatsende
        res = scoring.score(c[:i + 1], kind=kind, name=name)
        ok = bool(res and res["score"] >= threshold)
        prev = i + 1
    out.iloc[prev:] = ok
    return out


def run_pyramid(prices: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for label, sym, start in PYRAMID_SERIES:
        df = prices.get(sym)
        if df is None or df.empty:
            rows.append({"series": label, "symbol": sym, "error": "keine Daten"})
            continue
        close = df["close"][df.index >= pd.Timestamp(start, tz=df.index.tz)]
        allowed = score_allowed(close, kind="etf", name=label)
        entry: dict[str, Any] = {"series": label, "symbol": sym, "years": round(len(close) / 252, 1)}
        for years in (3, 5):
            h = 252 * years
            entry[f"{years}J"] = {
                "staffel": pyramid_study(close, h),
                "staffel_zins2": pyramid_study(close, h, cash_rate=0.02),
                "staffel_rest_nach_12M": pyramid_study(close, h, deploy_after=252),
                "staffel_nur_ab_65": pyramid_study(close, h, allowed=allowed),
            }
        rows.append(entry)
    return rows


# ----------------------------------------------------------------------------
# 1b. Eigene Staffeln für ETFs und Aktien
# ----------------------------------------------------------------------------
# Jede Staffel: Stufen als Rückgang vom 12-Monats-Hoch, entweder fest ("fix") oder als
# Vielfaches der Schwankung des Werts ("sigma": Jahres-Volatilität der letzten 12 Monate,
# z. B. 1,0 × 15 % = −15 %). "upfront" = Anteil, der sofort angelegt wird.
LADDERS: dict[str, dict[str, Any]] = {
    "bisher": {"label": "Bisher: −10/−15/−20/−25/−30 %", "fix": [0.10, 0.15, 0.20, 0.25, 0.30],
               "shares": [0.10, 0.15, 0.20, 0.25, 0.30]},
    "etf_frueh": {"label": "ETF früh: −5/−10/−15/−20/−25 %, je 20 %", "fix": [0.05, 0.10, 0.15, 0.20, 0.25],
                  "shares": [0.20] * 5},
    "etf_10_sofort": {"label": "ETF: −10 % = 50 %, −15 % = 25 %, −20 % = 25 %", "fix": [0.10, 0.15, 0.20],
                      "shares": [0.50, 0.25, 0.25]},
    "etf_haelfte": {"label": "ETF: Hälfte sofort, Rest bei −5/−10/−15/−20 %", "upfront": 0.5,
                    "fix": [0.05, 0.10, 0.15, 0.20], "shares": [0.125] * 4},
    "aktie_spaet": {"label": "Aktie spät: −15/−25/−35/−45/−55 %", "fix": [0.15, 0.25, 0.35, 0.45, 0.55],
                    "shares": [0.10, 0.15, 0.20, 0.25, 0.30]},
    "sigma": {"label": "Schwankung: 0,5/0,75/1/1,25/1,5 × Volatilität", "sigma": [0.5, 0.75, 1.0, 1.25, 1.5],
              "shares": [0.10, 0.15, 0.20, 0.25, 0.30]},
    "sigma_tief": {"label": "Schwankung tief: 0,75/1/1,5/2/2,5 × Volatilität", "sigma": [0.75, 1.0, 1.5, 2.0, 2.5],
                   "shares": [0.10, 0.15, 0.20, 0.25, 0.30]},
}
LADDER_SETS = {"etf": ["bisher", "etf_frueh", "etf_10_sofort", "etf_haelfte", "sigma"],
               "aktie": ["bisher", "aktie_spaet", "sigma", "sigma_tief"]}
REST_MODES = {"tagesgeld": {"label": "Rest auf Tagesgeld (2 %)", "cash_rate": 0.02, "deploy_after": None},
              "nach_12M": {"label": "Rest nach 12 Monaten anlegen", "cash_rate": 0.02, "deploy_after": 252}}


def trailing_vol(close: np.ndarray) -> np.ndarray:
    r = pd.Series(np.log(close)).diff()
    return (r.rolling(252, min_periods=126).std() * math.sqrt(252)).to_numpy()


class LadderSeries:
    """Vorberechnung je Wert: Rückgang, Schwankung, Abschnitte zwischen zwei 12-Monats-Hochs."""

    def __init__(self, close: np.ndarray):
        self.c = close
        self.high = rolling_high(close)
        self.dd = 1 - close / self.high
        self.vol = trailing_vol(close)
        self.epoch = np.cumsum(self.dd <= 1e-9)  # neues Hoch = neuer Abschnitt, Stufen wieder frei
        self._cond: dict[tuple, np.ndarray] = {}

    def trigger_days(self, kind: str, x: float) -> np.ndarray:
        key = (kind, x)
        if key not in self._cond:
            thr = x if kind == "fix" else x * self.vol
            with np.errstate(invalid="ignore"):
                cond = self.dd >= np.asarray(thr) - 1e-9
            self._cond[key] = np.nonzero(cond)[0]
        return self._cond[key]


def simulate_ladder(ls: LadderSeries, ladder: dict[str, Any], start: int, end: int, *, cash_rate: float = 0.0,
                    deploy_after: int | None = None) -> dict[str, float]:
    """Wie simulate_pyramid, aber für beliebige Staffeln und schnell (Ereignisse statt Tagesschleife)."""
    c = ls.c
    up = ladder.get("upfront", 0.0)
    units, cash = up / c[start], 1.0 - up
    kind = "fix" if "fix" in ladder else "sigma"
    events: list[tuple[int, int, float]] = []
    for k, (x, share) in enumerate(zip(ladder[kind], ladder["shares"])):
        d = ls.trigger_days(kind, x)
        d = d[(d >= start) & (d <= end)]
        if len(d):
            e = ls.epoch[d]
            d = d[np.r_[True, e[1:] != e[:-1]]]  # pro Abschnitt nur einmal
        events += [(int(i), k, share) for i in d]
    if deploy_after is not None and start + deploy_after <= end:
        events.append((start + deploy_after, 99, 1.0))
    events.sort()
    daily = (1 + cash_rate) ** (1 / 252)
    t = start - 1  # Zins ab dem Starttag, wie in simulate_pyramid
    first = None
    for i, k, share in events:
        if cash <= 1e-12:
            break
        cash *= daily ** (i - t)
        t = i
        amt = cash if k == 99 else min(share, cash)
        units += amt / c[i]
        cash -= amt
        first = first if first is not None else i - start
    cash *= daily ** (end - t)
    return {"value": units * c[end] + cash, "cash_left": cash, "first_buy_days": -1 if first is None else first}


def ladder_study(series: dict[str, np.ndarray], ladder: dict[str, Any], horizon: int, *, cash_rate: float,
                 deploy_after: int | None) -> dict[str, Any] | None:
    """Alle Werte einer Art, alle Monatsanfänge: Staffel gegen Sofortanlage derselben 1.000 €."""
    rel_all, cash_all, per_inst = [], [], []
    for (c, idx, ls) in series.values():
        months = pd.Series(np.arange(len(idx)), index=idx).groupby([idx.year, idx.month]).first().to_numpy()
        starts = [int(i) for i in months if i >= 252 and i + horizon < len(c) and not np.isnan(ls.vol[i])]
        rel = []
        for st in starts:
            end = st + horizon
            r = simulate_ladder(ls, ladder, st, end, cash_rate=cash_rate, deploy_after=deploy_after)
            rel.append(r["value"] / (c[end] / c[st]) - 1)
            cash_all.append(r["cash_left"])
        if rel:
            rel_all += rel
            per_inst.append(float(np.median(rel)))
    if not rel_all:
        return None
    a = np.array(rel_all)
    return {"instruments": len(per_inst), "windows": len(a), "win_share": round(float(np.mean(a >= 0)), 3),
            "median_diff": round(float(np.median(a)), 4), "mean_diff": round(float(np.mean(a)), 4),
            "p10_diff": round(float(np.quantile(a, 0.10)), 4), "p90_diff": round(float(np.quantile(a, 0.90)), 4),
            "inst_median_diff": round(float(np.median(per_inst)), 4), "avg_cash_left": round(float(np.mean(cash_all)), 3)}


def drop_stats(series: dict[str, np.ndarray]) -> dict[str, Any]:
    """Wie oft fallen die Werte wie tief? Anteil der Tage mit Rückgang ab x % vom 12-Monats-Hoch."""
    dd = np.concatenate([ls.dd[252:] for (_, _, ls) in series.values()]) if series else np.array([])
    vol = np.concatenate([ls.vol[252:] for (_, _, ls) in series.values()]) if series else np.array([])
    vol = vol[~np.isnan(vol)]
    if not len(dd):
        return {}
    return {"days_below": {f"{int(x * 100)}": round(float(np.mean(dd >= x)), 3) for x in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)},
            "vol_median": round(float(np.median(vol)), 3) if len(vol) else None}


def run_ladders(prices: dict[str, pd.DataFrame], meta: dict[str, dict[str, str]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {"etf": {}, "aktie": {}}
    for sym, info in meta.items():
        df = prices.get(sym)
        if df is None or df.empty or info["kind"] not in groups:
            continue
        if info["kind"] == "etf" and (scoring.is_leveraged(info["name"]) or scoring.is_defensive(info["name"])):
            continue  # Hebel-, Anleihen-, Geldmarkt- und Rohstoff-ETFs gehören nicht in den Vergleich
        s = df["close"].dropna()
        s = s[s > 0]
        if len(s) < 252 + 252 * 3:
            continue
        c = s.to_numpy(float)
        groups[info["kind"]][sym] = (c, s.index, LadderSeries(c))
    out: dict[str, Any] = {}
    for kind, series in groups.items():
        res: dict[str, Any] = {"instruments": len(series), "drops": drop_stats(series), "ladders": {}}
        for name in LADDER_SETS[kind]:
            lad = LADDERS[name]
            res["ladders"][name] = {"label": lad["label"]}
            for years in (3, 5):
                for mode, m in REST_MODES.items():
                    res["ladders"][name][f"{years}J_{mode}"] = ladder_study(
                        series, lad, 252 * years, cash_rate=m["cash_rate"], deploy_after=m["deploy_after"])
        out[kind] = res
    return out


# ----------------------------------------------------------------------------
# 1c. Dein ganzes Vorgehen: Sparplan 120 €/Monat + einmalige Reserve 1.000 €
# ----------------------------------------------------------------------------
# Euro-ETFs an Xetra (Kurse inkl. Dividenden), damit kein Wechselkurs nachgerechnet werden muss.
PLAN_ASSETS = {"EUNL.DE": "MSCI World", "SXR8.DE": "S&P 500", "EXXT.DE": "Nasdaq 100"}
MIXES = {
    "welt": ("100 % Welt", {"EUNL.DE": 1.0}),
    "welt_sp": ("70 % Welt, 30 % S&P 500", {"EUNL.DE": 0.7, "SXR8.DE": 0.3}),
    "welt_ndx": ("70 % Welt, 30 % Nasdaq 100", {"EUNL.DE": 0.7, "EXXT.DE": 0.3}),
    "sp": ("100 % S&P 500", {"SXR8.DE": 1.0}),
    "ndx": ("100 % Nasdaq 100", {"EXXT.DE": 1.0}),
}
# Reserve 1.000 €, nur einmal vorhanden; was nicht angelegt ist, liegt auf Tagesgeld (2 %).
RESERVE_RULES = {
    "sofort": ("Reserve sofort anlegen", 1.0, [], []),
    "deine": ("Deine Pyramide: −10/−15/−20/−25/−30 % → 100/150/200/250/300 €", 0.0,
              [0.10, 0.15, 0.20, 0.25, 0.30], [100, 150, 200, 250, 300]),
    "zehn_50": ("−10 % → 500 €, −15 % → 250 €, −20 % → 250 €", 0.0, [0.10, 0.15, 0.20], [500, 250, 250]),
    "haelfte": ("500 € sofort, Rest bei −5/−10/−15/−20 % je 125 €", 0.5, [0.05, 0.10, 0.15, 0.20], [125] * 4),
    "nie": ("Reserve nie anlegen (bleibt auf Tagesgeld)", 0.0, [], []),
}
PLAN_EUR_M, RESERVE = 120.0, 1000.0


def plan_sim(px: pd.DataFrame, weights: dict[str, float], rule: tuple, start: int, end: int,
             high: np.ndarray, cash_rate: float = 0.02) -> dict[str, float]:
    """Ein Durchlauf von Tag ``start`` bis ``end``. Rückgänge gemessen am 12-Monats-Hoch des Hauptwerts
    (größter Anteil der Aufteilung); Käufe der Reserve werden wie der Sparplan aufgeteilt."""
    _, upfront, thr, eur = rule
    syms = list(weights)
    P = px[syms].to_numpy(float)
    main = max(weights, key=weights.get)
    m = px[main].to_numpy(float)
    w = np.array([weights[s] for s in syms])
    units = np.zeros(len(syms))
    cash = RESERVE
    daily = (1 + cash_rate) ** (1 / 252)
    idx = px.index
    month_first = np.r_[True, idx.month[1:] != idx.month[:-1]]

    def buy(i, amount):
        nonlocal units
        units = units + amount * w / P[i]

    invested = 0.0
    first_buy = None
    if upfront:
        buy(start, RESERVE * upfront); cash -= RESERVE * upfront
        first_buy = 0
    hit = [False] * len(thr)
    peak, max_dd = 0.0, 0.0
    for i in range(start, end + 1):
        cash *= daily
        if month_first[i] or i == start:
            buy(i, PLAN_EUR_M); invested += PLAN_EUR_M
        dd = 1 - m[i] / high[i]
        for k, t in enumerate(thr):
            if not hit[k] and dd >= t - 1e-9 and cash > 1e-6:
                amt = min(eur[k], cash)
                buy(i, amt); cash -= amt; hit[k] = True
                first_buy = first_buy if first_buy is not None else i - start
        val = float(units @ P[i])
        total = val + cash  # Vermögen (Depot + Reserve); Einzahlungen dämpfen den Rückgang
        if i == start:
            peak = total
        else:
            peak = max(peak, total)
            max_dd = max(max_dd, 1 - total / peak)
    final = float(units @ P[end]) + cash
    return {"final": final, "paid": invested + RESERVE, "cash_left": cash, "max_dd": max_dd,
            "first_buy": -1 if first_buy is None else first_buy}


def index_stats(px: pd.DataFrame, weights: dict[str, float]) -> dict[str, float]:
    """Kennzahlen der Aufteilung selbst (monatlich auf die Zielgewichte zurückgesetzt)."""
    r = px[list(weights)].pct_change().fillna(0)
    w = pd.Series(weights)
    mix = (1 + (r * w).sum(axis=1)).cumprod()
    years = len(mix) / 252
    dd = 1 - mix / mix.cummax()
    y12 = mix.pct_change(252).dropna()
    return {"cagr": round(float(mix.iloc[-1] ** (1 / years) - 1), 4), "max_dd": round(float(dd.max()), 4),
            "days_over30": round(float((dd > 0.30).mean()), 3), "worst_12m": round(float(y12.min()), 4),
            "best_12m": round(float(y12.max()), 4), "vol": round(float((r * w).sum(axis=1).std() * math.sqrt(252)), 4)}


def run_plans(prices: dict[str, pd.DataFrame]) -> dict[str, Any]:
    cols = {}
    for sym in PLAN_ASSETS:
        df = prices.get(sym)
        if df is None or df.empty:
            return {"error": f"keine Daten für {sym}"}
        s = df["close"].dropna()
        cols[sym] = s[~s.index.normalize().duplicated(keep="last")].set_axis(s.index.normalize()[~s.index.normalize().duplicated(keep="last")])
    px = pd.DataFrame(cols).dropna()
    px = px[px.index >= px.index[0]]
    out: dict[str, Any] = {"from": str(px.index[0].date()), "to": str(px.index[-1].date()),
                           "years": round(len(px) / 252, 1), "mixes": {}, "rules": {}}
    idx = px.index
    month_starts = np.nonzero(np.r_[True, idx.month[1:] != idx.month[:-1]])[0]
    for key, (label, weights) in MIXES.items():
        main = max(weights, key=weights.get)
        high = rolling_high(px[main].to_numpy(float))
        entry: dict[str, Any] = {"label": label, "index": index_stats(px, weights)}
        rules = RESERVE_RULES if key == "welt" else {k: RESERVE_RULES[k] for k in ("sofort", "deine")}
        for years in (5, 10):
            h = 252 * years
            starts = [int(i) for i in month_starts if i >= 252 and i + h < len(px)]
            res = {}
            for rk, rule in rules.items():
                runs = [plan_sim(px, weights, rule, st, st + h, high) for st in starts]
                if not runs:
                    continue
                fin = np.array([r["final"] for r in runs])
                paid = runs[0]["paid"]
                mdd = np.array([r["max_dd"] for r in runs])
                res[rk] = {"label": rule[0], "windows": len(runs), "paid": round(paid),
                           "median": round(float(np.median(fin))), "p10": round(float(np.quantile(fin, 0.10))),
                           "worst": round(float(fin.min())), "best": round(float(fin.max())),
                           "loss_share": round(float(np.mean(fin < paid)), 3),
                           "dd_median": round(float(np.median(mdd)), 3), "dd_over30": round(float(np.mean(mdd > 0.30)), 3),
                           "cash_left": round(float(np.mean([r["cash_left"] for r in runs]))),
                           "never_used": round(float(np.mean([r["first_buy"] < 0 for r in runs])), 3)}
            # gleiche Startpunkte: wie oft war die Regel besser als „sofort“?
            if "sofort" in res:
                base = np.array([plan_sim(px, weights, rules["sofort"], st, st + h, high)["final"] for st in starts])
                for rk, rule in rules.items():
                    if rk != "sofort" and rk in res:
                        f = np.array([plan_sim(px, weights, rule, st, st + h, high)["final"] for st in starts])
                        res[rk]["vs_sofort_win"] = round(float(np.mean(f >= base)), 3)
                        res[rk]["vs_sofort_median_eur"] = round(float(np.median(f - base)))
            entry[f"{years}J"] = res
        out["mixes"][key] = entry
    return out


# ----------------------------------------------------------------------------
# 2. Punktzahl
# ----------------------------------------------------------------------------

def score_study(prices: dict[str, pd.DataFrame], meta: dict[str, dict[str, str]],
                bench: pd.Series | None) -> dict[str, Any]:
    """Punktzahl an jedem Monatsende → Entwicklung danach, getrennt nach Einstufung und Art."""
    recs: list[dict[str, Any]] = []
    t0 = pd.Timestamp(SCORE_FROM)
    for sym, df in prices.items():
        info = meta.get(sym)
        if info is None or df.empty:
            continue
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        c = df["close"].to_numpy()
        v = df["volume"].to_numpy() if "volume" in df else None
        idx = df.index
        month_end = pd.Series(np.arange(len(idx)), index=idx).groupby([idx.year, idx.month]).last().to_numpy()
        for i in month_end:
            if idx[i].tz_localize(None) < t0 or i < scoring.MIN_BARS:
                continue
            res = scoring.score(c[:i + 1], None if v is None else v[:i + 1], kind=info["kind"], name=info["name"])
            if res is None:
                continue
            rec = {"sym": sym, "kind": info["kind"], "month": idx[i].strftime("%Y-%m"), "score": res["score"],
                   "grade": res["grade"]}
            for key, h in HORIZONS.items():
                if i + h < len(c):
                    rec[key] = c[i + h] / c[i] - 1
            if i + 252 < len(c):
                rec["dd12"] = scoring.max_drawdown(c[i:i + 253])
            recs.append(rec)
    if not recs:
        return {"error": "keine Daten"}
    df = pd.DataFrame(recs)

    def summarize(g: pd.DataFrame) -> dict[str, Any]:
        out: dict[str, Any] = {"n": int(len(g))}
        for key in HORIZONS:
            x = g[key].dropna() if key in g else pd.Series(dtype=float)
            if len(x):
                out[key] = {"mean": round(float(x.mean()), 4), "median": round(float(x.median()), 4),
                            "pos_share": round(float((x > 0).mean()), 3), "n": int(len(x))}
        if "dd12" in g and g["dd12"].notna().any():
            d = g["dd12"].dropna()
            out["dd12_mean"] = round(float(d.mean()), 4)
            out["dd12_over30"] = round(float((d > 0.30).mean()), 3)
        return out

    result: dict[str, Any] = {"months": [df["month"].min(), df["month"].max()], "instruments": int(df["sym"].nunique())}
    for kind in ("etf", "aktie"):
        sub = df[df["kind"] == kind]
        if sub.empty:
            continue
        result[kind] = {g: summarize(sub[sub["grade"] == g]) for g in ("interessant", "neutral", "vorsicht")}
        result[kind]["alle"] = summarize(sub)
        # Monatsweise: Durchschnitt „interessant“ minus Durchschnitt aller Werte desselben Monats
        diffs = []
        for _, m in sub.dropna(subset=["12M"]).groupby("month"):
            top = m[m["grade"] == "interessant"]["12M"]
            if len(top) >= 3 and len(m) >= 10:
                diffs.append(top.mean() - m["12M"].mean())
        if diffs:
            d = np.array(diffs)
            result[kind]["top_vs_all_12M"] = {"months": len(d), "mean": round(float(d.mean()), 4),
                                              "better_share": round(float((d > 0).mean()), 3)}
    if bench is not None and not bench.empty:
        b = bench.dropna()
        bi = b.index
        me = pd.Series(np.arange(len(bi)), index=bi).groupby([bi.year, bi.month]).last().to_numpy()
        fw = [b.iloc[i + 252] / b.iloc[i] - 1 for i in me if bi[i].tz_localize(None) >= t0 and i + 252 < len(b)]
        if fw:
            result["benchmark_12M"] = {"name": "MSCI World (EUNL)", "mean": round(float(np.mean(fw)), 4),
                                       "median": round(float(np.median(fw)), 4)}
    return result


# ----------------------------------------------------------------------------
# Daten laden und Bericht schreiben
# ----------------------------------------------------------------------------

def load_yahoo(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        raw = yf.download(chunk, period="max", interval="1d", auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
        for sym in chunk:
            try:
                d = raw[sym] if len(chunk) > 1 else raw
            except KeyError:
                continue
            d = d.rename(columns=str.lower)[["close", "volume"]].dropna(subset=["close"])
            if d.index.tz is None:
                d.index = d.index.tz_localize("UTC")
            if len(d):
                out[sym] = d
    return out


def universe_meta() -> dict[str, dict[str, str]]:
    settings = load_settings()
    meta = {}
    for inst in settings.instruments:
        sym = inst.symbol_for("yahoo")
        if sym and inst.tier == "core" and inst.kind in ("aktie", "etf"):
            meta[sym] = {"kind": inst.kind, "name": inst.name, "id": inst.id}
    return meta


def run(loader: Callable[[list[str]], dict[str, pd.DataFrame]] = load_yahoo) -> dict[str, Any]:
    meta = universe_meta()
    symbols = sorted(set(meta) | {s for _, s, _ in PYRAMID_SERIES} | set(PLAN_ASSETS))
    prices = loader(symbols)
    result = {
        "created": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "pyramid": run_pyramid(prices),
        "ladders": run_ladders(prices, meta),
        "plans": run_plans(prices),
        "score": score_study({k: v for k, v in prices.items() if k in meta}, meta,
                             prices.get("EUNL.DE", pd.DataFrame()).get("close")),
        "limits": [
            "Kurse von Yahoo inklusive Dividenden, in Handelswährung, ohne Gebühren und Steuern.",
            "Getestet werden Werte, die es heute gibt. Verschwundene Firmen fehlen; das lässt Einzelaktien besser aussehen und Nachkäufe bei Aktien zu gut (eine Aktie, die nach −50 % pleiteging, fehlt im Test).",
            "Qualität/Bewertung und Nachrichten fehlen im Test (keine freie Historie).",
            "Startpunkte im Monatsabstand überlappen sich stark; sie sind keine unabhängigen Versuche.",
            "Vergangene Ergebnisse sagen künftige nicht sicher voraus.",
        ],
    }
    return result


def _p(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.1f} %".replace(".", ",").replace("-", "−")


def report_markdown(r: dict[str, Any]) -> str:
    lines = ["# Rückblick-Test des Ratgebers", "", f"Erstellt: {r['created']} (UTC)", "",
             "## 1. Nachkauf-Stufen gegen sofortiges Anlegen", "",
             "Für jeden Monatsanfang als Startpunkt: 1.000 € entweder sofort angelegt oder nach der Staffel "
             "(−10/−15/−20/−25/−30 % vom 12-Monats-Hoch). „Vorteil“ = Ergebnis der Staffel geteilt durch Sofortanlage, minus 1.", ""]
    names = {"staffel": "Staffel, Rest bleibt Bargeld (0 %)", "staffel_zins2": "Staffel, Rest auf Tagesgeld (2 %)",
             "staffel_rest_nach_12M": "Staffel, Rest nach 12 Monaten anlegen", "staffel_nur_ab_65": "Staffel nur bei Punktzahl ab 65"}
    for row in r["pyramid"]:
        if "error" in row:
            lines.append(f"- {row['series']}: {row['error']}")
            continue
        lines += [f"### {row['series']} ({row['years']} Jahre Daten)", "",
                  "| Variante | Zeitraum | Startpunkte | Staffel besser | Vorteil Median | schlechtester | bester | Rest unangelegt (Ø) |",
                  "|---|---|---|---|---|---|---|---|"]
        for years in ("3J", "5J"):
            for key, label in names.items():
                s = row[years].get(key)
                if not s:
                    continue
                lines.append(f"| {label} | {years} | {s['windows']} | {s['win_share'] * 100:.0f} % | {_p(s['median_diff'])} | "
                             f"{_p(s['worst_diff'])} | {_p(s['best_diff'])} | {s['avg_cash_left'] * 100:.0f} % |")
        lines.append("")
    lad = r.get("ladders") or {}
    if lad:
        lines += ["## 1b. Eigene Staffeln für ETFs und Aktien", "",
                  "Alle ETFs (ohne Hebel, Anleihen, Geldmarkt, Rohstoffe) bzw. alle Aktien der App, jeder Monatsanfang als Start. "
                  "„Schwankung“ = Stufe als Vielfaches der Jahres-Volatilität des Werts in den 12 Monaten davor. "
                  "„Vorteil“ = Staffel geteilt durch Sofortanlage, minus 1. Schlechte 10 % = jeder zehnte Fall lag mindestens so weit hinten.", ""]
        for kind, title in (("etf", "ETFs"), ("aktie", "Aktien")):
            g = lad.get(kind)
            if not g:
                continue
            dr = g.get("drops") or {}
            lines.append(f"### {title} ({g['instruments']} Werte)")
            lines.append("")
            if dr:
                db = dr["days_below"]
                lines.append(f"Typische Schwankung (Median): {dr['vol_median'] * 100:.0f} % pro Jahr. Anteil der Tage mindestens x % unter dem "
                             f"12-Monats-Hoch: 10 %: {db['10'] * 100:.0f} %, 20 %: {db['20'] * 100:.0f} %, 30 %: {db['30'] * 100:.0f} %, 50 %: {db['50'] * 100:.0f} %.")
                lines.append("")
            lines += ["| Staffel | Rest | Zeitraum | Fälle | Staffel besser | Vorteil Median | schlechte 10 % | gute 10 % | Rest unangelegt (Ø) |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for name, row in g["ladders"].items():
                for years in (3, 5):
                    for mode, m in REST_MODES.items():
                        x = row.get(f"{years}J_{mode}")
                        if not x:
                            continue
                        lines.append(f"| {row['label']} | {m['label']} | {years}J | {x['windows']} | {x['win_share'] * 100:.0f} % | "
                                     f"{_p(x['median_diff'])} | {_p(x['p10_diff'])} | {_p(x['p90_diff'])} | {x['avg_cash_left'] * 100:.0f} % |")
            lines.append("")
    pl = r.get("plans") or {}
    if pl and "error" not in pl:
        e0 = lambda x: f"{x:,.0f} €".replace(",", ".")
        lines += ["## 1c. Sparplan 120 €/Monat plus einmalige Reserve 1.000 €", "",
                  f"Euro-ETFs an Xetra inkl. Dividenden, {pl['from']} bis {pl['to']} ({pl['years']} Jahre). Jeder Monatsanfang ab dem zweiten Jahr ist ein Startpunkt. "
                  "Nicht angelegte Reserve liegt auf Tagesgeld (2 %). Rückgänge gemessen am 12-Monats-Hoch des Hauptwerts.", "",
                  "### Die Aufteilungen selbst", "",
                  "| Aufteilung | Rendite pro Jahr | Schwankung | größter Rückgang | Tage über −30 % | schlechteste 12 Monate | beste 12 Monate |",
                  "|---|---|---|---|---|---|---|"]
        for key, m in pl["mixes"].items():
            x = m["index"]
            lines.append(f"| {m['label']} | {_p(x['cagr'])} | {x['vol'] * 100:.0f} % | −{x['max_dd'] * 100:.0f} % | {x['days_over30'] * 100:.0f} % | {_p(x['worst_12m'])} | {_p(x['best_12m'])} |")
        lines.append("")
        for years in ("5J", "10J"):
            lines += [f"### Nach {years[:-1]} Jahren", "",
                      "| Aufteilung | Reserve | Starts | eingezahlt | Endwert Median | schlechte 10 % | schlechtester | bester | im Minus | Depot-Rückgang über 30 % | besser als „sofort“ | Median ggü. „sofort“ | Reserve nie gebraucht |",
                      "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            for key, m in pl["mixes"].items():
                for rk, x in (m.get(years) or {}).items():
                    vs = f"{x['vs_sofort_win'] * 100:.0f} %" if "vs_sofort_win" in x else "–"
                    vm = (("+" if x["vs_sofort_median_eur"] >= 0 else "−") + e0(abs(x["vs_sofort_median_eur"]))) if "vs_sofort_median_eur" in x else "–"
                    lines.append(f"| {m['label']} | {x['label']} | {x['windows']} | {e0(x['paid'])} | {e0(x['median'])} | {e0(x['p10'])} | {e0(x['worst'])} | {e0(x['best'])} | "
                                 f"{x['loss_share'] * 100:.0f} % | {x['dd_over30'] * 100:.0f} % | {vs} | {vm} | {x['never_used'] * 100:.0f} % |")
            lines.append("")
    sc = r["score"]
    lines += ["## 2. Hat die Punktzahl etwas vorhergesagt?", ""]
    if "error" in sc:
        lines.append(sc["error"])
    else:
        lines.append(f"{sc['instruments']} Werte, Monatsenden {sc['months'][0]} bis {sc['months'][1]}.")
        lines.append("")
        for kind, title in (("etf", "ETFs"), ("aktie", "Aktien")):
            k = sc.get(kind)
            if not k:
                continue
            lines += [f"### {title}", "", "| Einstufung | Fälle | Ø 3 Monate | Ø 6 Monate | Ø 12 Monate | 12 Monate im Plus | Ø größter Rückgang 12 M | Rückgang über 30 % |",
                      "|---|---|---|---|---|---|---|---|"]
            for g in ("interessant", "neutral", "vorsicht", "alle"):
                s = k.get(g) or {}
                if not s.get("n"):
                    continue
                lines.append(f"| {g} | {s['n']} | {_p(s.get('3M', {}).get('mean'))} | {_p(s.get('6M', {}).get('mean'))} | "
                             f"{_p(s.get('12M', {}).get('mean'))} | {s.get('12M', {}).get('pos_share', 0) * 100:.0f} % | "
                             f"−{s.get('dd12_mean', 0) * 100:.0f} % | {s.get('dd12_over30', 0) * 100:.0f} % |")
            t = k.get("top_vs_all_12M")
            if t:
                lines.append("")
                lines.append(f"„Interessant“ gegen alle Werte desselben Monats: im Schnitt {_p(t['mean'])} über 12 Monate, "
                             f"besser in {t['better_share'] * 100:.0f} % von {t['months']} Monaten.")
            lines.append("")
        b = sc.get("benchmark_12M")
        if b:
            lines.append(f"Maßstab {b['name']}: Ø {_p(b['mean'])} über 12 Monate.")
            lines.append("")
    lines += ["## Grenzen", ""] + [f"- {x}" for x in r["limits"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    r = run()
    out_json = ROOT / "status" / "backtest.json"
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report_markdown(r)
    (ROOT / "docs" / "RUECKBLICK.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
