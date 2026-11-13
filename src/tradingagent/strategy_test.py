"""Phase 5 – Rückblick-Tests der Strategien mit den Regeln und Kosten aus Phase 4.

Ablauf je Strategie:
  * Tageskerzen aller Kernwerte (Aktien und ETFs aus Deutschland, Europa, USA; US-Werte in Euro umgerechnet).
  * Signal am Schluss von Tag t, Kauf/Verkauf zur Eröffnung von t+1. Stops werden mit Tief und Eröffnung geprüft.
  * Jede Order läuft durch den RiskManager (Phase 4): Kosten, 1 % Risiko, Stop 2 × ATR, Obergrenzen,
    höchstens 5 Positionen, Tages-/Wochenverlust, Verlustserie, Kill Switch.
  * Walk-Forward: 3 Jahre lernen (beste Einstellung wählen), das folgende Jahr unberührt prüfen.
    Bewertet wird nur, was in den Prüfjahren herauskam.
  * Vergleich: MSCI World kaufen und halten (gleiche Kosten) und zufällige Käufe mit denselben Regeln.

Aufruf:  python -m tradingagent strategien   (schreibt status/strategien.json und docs/STRATEGIEN.md)
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import ROOT, load_settings
from .risk import BERLIN, Quote, RiskConfig, RiskManager
from .strategies import STRATEGIES, signals

TRAIN_YEARS = 3
FIRST_YEAR = 2012          # erstes Lernjahr; erstes Prüfjahr damit 2015
BENCH = "EUNL.DE"          # iShares Core MSCI World
RANDOM_SEEDS = 20
MAX_CANDIDATES = 10        # je Tag höchstens so viele Kaufsignale prüfen (nach Momentum sortiert)


# ---------------------------------------------------------------- Daten
@dataclass
class Panel:
    dates: pd.DatetimeIndex
    ids: list[str]
    kinds: list[str]
    names: list[str]
    frames: dict[str, pd.DataFrame]          # je Wert die eigenen Tageskerzen (in €)
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    mom: np.ndarray                          # 6-Monats-Rendite bis zum Schluss, zum Sortieren der Kaufsignale
    pos: dict[int, np.ndarray]               # Zeilen des Kalenders, an denen der Wert gehandelt wurde


def _wilder_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def build_panel(frames: dict[str, pd.DataFrame], meta: dict[str, dict[str, str]]) -> Panel:
    ids = sorted(k for k, f in frames.items() if len(f) >= 300)
    dates = pd.DatetimeIndex(sorted(set().union(*[set(frames[i].index) for i in ids])))
    shape = (len(dates), len(ids))
    o, h, lo, c, a, m = (np.full(shape, np.nan) for _ in range(6))
    pos = {}
    for j, i in enumerate(ids):
        f = frames[i]
        p = dates.get_indexer(f.index)
        pos[j] = p
        o[p, j], h[p, j], lo[p, j], c[p, j] = f["open"].to_numpy(), f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy()
        a[p, j] = _wilder_atr(f).to_numpy()
        m[p, j] = (f["close"] / f["close"].shift(126) - 1).to_numpy()
    return Panel(dates, ids, [meta[i]["kind"] for i in ids], [meta[i]["name"] for i in ids], {i: frames[i] for i in ids},
                 o, h, lo, c, a, m, pos)


def signal_arrays(panel: Panel, key: str, params: dict) -> tuple[np.ndarray, np.ndarray]:
    shape = (len(panel.dates), len(panel.ids))
    en, ex = np.zeros(shape, bool), np.zeros(shape, bool)
    for j, i in enumerate(panel.ids):
        e, x = signals(panel.frames[i], key, params)
        en[panel.pos[j], j], ex[panel.pos[j], j] = e, x
    return en, ex


# ---------------------------------------------------------------- Simulation
def _when(day: pd.Timestamp) -> datetime:
    """Handelszeitpunkt: 15:35 Uhr deutscher Zeit, dann haben Xetra und US-Börsen geöffnet."""
    return datetime(day.year, day.month, day.day, 15, 35, tzinfo=BERLIN)


def simulate(panel: Panel, entry: np.ndarray, exit_: np.ndarray, start: int, end: int, *,
             rm: RiskManager, capital: float | None = None) -> dict[str, Any]:
    """Ein Konto (Halten über Nacht) vom Tag start bis end (ausschließlich)."""
    acc = rm.new_account("test", "ueber_nacht")
    if capital:
        acc.cash = acc.peak = acc.day_start = acc.week_start = capital
    idx = {i: j for j, i in enumerate(panel.ids)}
    last: dict[str, float] = {}
    equity = np.full(end - start, np.nan)
    invested = 0
    for d in range(start, end):
        now = _when(panel.dates[d])
        t = now.timestamp()
        held = list(acc.positions)
        # 1) Stops zur Eröffnung, 2) Verkaufssignale vom Vortag
        for vid in held:
            j = idx[vid]
            op = panel.o[d, j]
            if np.isnan(op):
                continue
            pos = acc.positions[vid]
            if op <= pos.stop:
                rm.sell(acc, vid, Quote(op, t), now, last, reason="Stop (Eröffnung)", stop=True)
            elif d > 0 and exit_[d - 1, j]:
                rm.sell(acc, vid, Quote(op, t), now, last, reason="Verkaufssignal")
        # 3) Kaufsignale vom Vortag, nach Momentum sortiert
        if d > 0 and not acc.killed:
            cand = np.flatnonzero(entry[d - 1] & ~np.isnan(panel.o[d]))
            if len(cand):
                order = cand[np.argsort(-np.nan_to_num(panel.mom[d - 1, cand], nan=-9))][:MAX_CANDIDATES]
                for j in order:
                    vid = panel.ids[j]
                    if vid in acc.positions or len(acc.positions) >= rm.cfg.max_positions:
                        continue
                    q = Quote(panel.o[d, j], t, prev=panel.c[d - 1, j], jump_confirmed=True)
                    rm.buy(acc, vid, panel.kinds[j], q, panel.atr[d - 1, j], now, last)
        # 4) Stops im Tagesverlauf (Tief), 5) Schluss: Stop nachziehen, Konto fortschreiben
        for vid in list(acc.positions):
            j = idx[vid]
            if np.isnan(panel.c[d, j]):
                continue
            pos = acc.positions[vid]
            if panel.lo[d, j] <= pos.stop:
                rm.sell(acc, vid, Quote(pos.stop, t), now, last, reason="Stop", stop=True)
                continue
            last[vid] = panel.c[d, j]
            rm.on_price(acc, vid, Quote(panel.c[d, j], t), panel.atr[d, j], now, last)
        for vid in acc.positions:
            j = idx[vid]
            if not np.isnan(panel.c[d, j]):
                last[vid] = panel.c[d, j]
        rm.roll(acc, now, last)
        invested += bool(acc.positions)
        equity[d - start] = acc.equity(last)
    fees = sum(e["fee"] for e in acc.log if e["ok"] and e["side"] in ("buy", "sell"))
    kills = [e["reason"] for e in acc.log if e["side"] == "kill"]
    return {"equity": equity, "trades": list(acc.realized), "fees": fees, "kills": kills,
            "exposure": invested / max(1, end - start), "log": acc.log}


def stats(equity: np.ndarray, capital: float, trades: list[float] | None = None) -> dict[str, Any]:
    eq = equity[~np.isnan(equity)]
    if len(eq) < 2:
        return {}
    ret = eq[-1] / capital - 1
    years = len(eq) / 252
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))
    dd = (np.concatenate([[capital], eq]) / peak - 1).min()
    daily = np.diff(np.concatenate([[capital], eq])) / np.concatenate([[capital], eq])[:-1]
    sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0
    out = {"rendite": ret, "cagr": (1 + ret) ** (1 / years) - 1 if years > 0 and ret > -1 else -1.0,
           "max_rueckgang": float(dd), "sharpe": sharpe}
    if trades is not None:
        out.update({"trades": len(trades), "trefferquote": (sum(1 for x in trades if x > 0) / len(trades)) if trades else None})
    return out


def _score(s: dict[str, Any]) -> float:
    """Auswahl im Lernzeitraum: Rendite je Rückgang (vorsichtig, bestraft große Verluste)."""
    if not s:
        return -9.0
    return s["cagr"] / max(0.05, -s["max_rueckgang"])


def cost_free(cfg: RiskConfig) -> RiskConfig:
    """Zum Vergleich: dieselben Regeln ohne Gebühr, Spread und Schlupf – zeigt, wie viel die Kosten ausmachen."""
    from dataclasses import replace

    return replace(cfg, fee_stock=0.0, fee_crypto_pct=0.0, spread_pct=0.0, spread_crypto_pct=0.0,
                   slip_pct=0.0, slip_stop_pct=0.0, max_fee_pct=100.0)


def walk_forward(panel: Panel, strat, test_years: list[int], rm: RiskManager, bench_years: dict[int, float]) -> dict[str, Any]:
    cap = rm.cfg.start_capital
    sig = {i: signal_arrays(panel, strat.key, p) for i, p in enumerate(strat.params)}
    years, curve, all_trades, fees, kills, exposure = [], [], [], 0.0, [], []
    for y in test_years:
        ts, _ = year_bounds(panel, y - TRAIN_YEARS)
        te, _ = year_bounds(panel, y)
        best, best_score = 0, -1e9
        if len(strat.params) > 1:
            for i in sig:
                sc = _score(stats(simulate(panel, *sig[i], ts, te, rm=rm)["equity"], cap))
                if sc > best_score:
                    best, best_score = i, sc
        s, e = year_bounds(panel, y)
        r = simulate(panel, *sig[best], s, e, rm=rm)
        st = stats(r["equity"], cap, r["trades"])
        years.append({"jahr": y, "rendite": st["rendite"], "max_rueckgang": st["max_rueckgang"], "trades": st["trades"],
                      "einstellung": strat.params[best], "msci_world": bench_years.get(y), "gestoppt": r["kills"]})
        curve.append(r["equity"] / cap)
        all_trades += r["trades"]
        fees += r["fees"]
        kills += [f"{y}: {k}" for k in r["kills"]]
        exposure.append(r["exposure"])
    chained = _chain(curve) * cap
    st = stats(chained, cap, all_trades)
    wins = [x for x in all_trades if x > 0]
    losses = [x for x in all_trades if x <= 0]
    return {"key": strat.key, "name": strat.name, "idee": strat.idea, **st,
            "gebuehren": round(fees, 2), "investiert": float(np.mean(exposure)),
            "gewinn_je_trade": float(np.mean(all_trades)) if all_trades else None,
            "mittel_gewinner": float(np.mean(wins)) if wins else None, "mittel_verlierer": float(np.mean(losses)) if losses else None,
            "jahre_besser_als_msci": sum(1 for x in years if x["msci_world"] is not None and x["rendite"] > x["msci_world"]),
            "jahre": years, "kill_switch": kills, "verlauf": _weekly(panel, test_years, chained / cap)}


def year_bounds(panel: Panel, year: int) -> tuple[int, int]:
    y = panel.dates.year
    sel = np.flatnonzero(y == year)
    return (int(sel[0]), int(sel[-1]) + 1) if len(sel) else (0, 0)


def buy_and_hold(panel: Panel, sym: str, start: int, end: int, rm: RiskManager) -> np.ndarray:
    j = panel.ids.index(sym)
    cfg = rm.cfg
    o, c = panel.o[start:end, j], panel.c[start:end, j]
    first = int(np.flatnonzero(~np.isnan(o))[0])
    from .risk import fill_price

    px = fill_price(cfg, "buy", o[first], "etf")
    qty = math.floor((cfg.start_capital - cfg.fee_stock) / px)
    cash = cfg.start_capital - qty * px - cfg.fee_stock
    close = pd.Series(c).ffill().to_numpy()
    eq = cash + qty * close
    eq[:first] = cfg.start_capital
    return eq


def random_signals(panel: Panel, rng: np.random.Generator, p_entry: float, hold: int) -> tuple[np.ndarray, np.ndarray]:
    shape = (len(panel.dates), len(panel.ids))
    has = ~np.isnan(panel.c)
    return (rng.random(shape) < p_entry) & has, (rng.random(shape) < 1 / hold) & has


# ---------------------------------------------------------------- Gesamtlauf
def run(frames: dict[str, pd.DataFrame], meta: dict[str, dict[str, str]], *, rm: RiskManager | None = None,
        progress: Callable[[str], None] = print, last_year: int | None = None) -> dict[str, Any]:
    rm = rm or RiskManager(RiskConfig.load())
    cap = rm.cfg.start_capital
    panel = build_panel(frames, meta)
    last_year = last_year or int(panel.dates.year.max())
    test_years = [y for y in range(FIRST_YEAR + TRAIN_YEARS, last_year + 1) if year_bounds(panel, y)[1] > 0]
    progress(f"{len(panel.ids)} Werte, {len(panel.dates)} Handelstage, Prüfjahre {test_years[0]}–{test_years[-1]}")

    bench_years = {}
    if BENCH in panel.ids:
        for y in test_years:
            s, e = year_bounds(panel, y)
            bench_years[y] = stats(buy_and_hold(panel, BENCH, s, e, rm), cap)["rendite"]

    results = []
    rm_free = RiskManager(cost_free(rm.cfg))
    for strat in STRATEGIES:
        res = walk_forward(panel, strat, test_years, rm, bench_years)
        free = walk_forward(panel, strat, test_years, rm_free, bench_years)
        res["cagr_ohne_kosten"], res["max_rueckgang_ohne_kosten"] = free["cagr"], free["max_rueckgang"]
        res["trefferquote_ohne_kosten"] = free["trefferquote"]
        results.append(res)
        progress(f"  {strat.name}: {res['cagr']:+.1%} pro Jahr (ohne Kosten {free['cagr']:+.1%}), "
                 f"Rückgang {res['max_rueckgang']:.1%}, {res['trades']} Trades")

    # Vergleich 1: MSCI World halten (jedes Prüfjahr neu gekauft, wie die Strategie-Konten)
    bench = None
    if BENCH in panel.ids:
        curve = []
        for y in test_years:
            s, e = year_bounds(panel, y)
            curve.append(buy_and_hold(panel, BENCH, s, e, rm) / cap)
        chained = _chain(curve) * cap
        bench = {"key": "msci", "name": "MSCI World kaufen und halten", **stats(chained, cap),
                 "jahre": [{"jahr": y, "rendite": bench_years[y]} for y in test_years],
                 "verlauf": _weekly(panel, test_years, chained / cap)}
    # Vergleich 2: Zufallskäufe mit denselben Regeln (Glück oder Können?)
    rng = np.random.default_rng(42)
    rand_cagr, rand_dd, rand_free = [], [], []
    for k in range(RANDOM_SEEDS):
        en, ex = random_signals(panel, rng, p_entry=0.01, hold=20)
        for target, manager in ((rand_cagr, rm), (rand_free, rm_free)):
            curve = []
            for y in test_years:
                s, e = year_bounds(panel, y)
                curve.append(simulate(panel, en, ex, s, e, rm=manager)["equity"] / cap)
            st = stats(_chain(curve) * cap, cap)
            target.append(st["cagr"])
            if manager is rm:
                rand_dd.append(st["max_rueckgang"])
    random = {"name": "Zufällige Käufe, gleiche Regeln", "laeufe": RANDOM_SEEDS,
              "cagr_median": float(np.median(rand_cagr)), "cagr_p90": float(np.percentile(rand_cagr, 90)),
              "max_rueckgang_median": float(np.median(rand_dd)), "cagr_median_ohne_kosten": float(np.median(rand_free))}
    for r in results:
        r["besser_als_zufall_prozent"] = float(np.mean([r["cagr"] > x for x in rand_cagr]) * 100)
    progress(f"  Zufall: Median {random['cagr_median']:+.1%} pro Jahr")

    return {"erstellt": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "werte": len(panel.ids), "pruefjahre": [test_years[0], test_years[-1]], "lernjahre": TRAIN_YEARS,
            "startkapital": cap, "strategien": results, "msci_world": bench, "zufall": random,
            "grenzen": LIMITS}


LIMITS = [
    "Getestet werden Werte, die es heute gibt. Firmen, die pleitegingen oder verschwanden, fehlen. Das lässt Aktienstrategien eher zu gut aussehen.",
    "Tageskerzen von Yahoo, Dividenden eingerechnet, US-Werte zum Tageskurs in Euro umgerechnet. Geld- und Briefkurse der Vergangenheit fehlen; der Spread ist mit 0,1 % geschätzt.",
    "Stops werden zum Stop-Kurs mit 0,2 % Schlupf ausgeführt; öffnet der Kurs schon darunter, zur Eröffnung.",
    "Jedes Prüfjahr beginnt mit frischen 5.000 €; die Jahre werden zu einem Verlauf aneinandergehängt.",
    "Branchen-Grenze und Nachrichten-Sperre fehlen im Test (keine Daten dazu).",
    "Daytrading ist nicht enthalten: dafür gibt es frei nur rund 60 Tage 5-Minuten-Kerzen.",
    "Vergangene Ergebnisse sagen künftige nicht sicher voraus.",
]


def _chain(curves: list[np.ndarray]) -> np.ndarray:
    out, base = [], 1.0
    for cv in curves:
        cv = pd.Series(cv).ffill().fillna(1.0).to_numpy()
        out.append(base * cv)
        base *= cv[-1]
    return np.concatenate(out) if out else np.array([])


def _weekly(panel: Panel, years: list[int], rel: np.ndarray) -> list[list[float]]:
    s, _ = year_bounds(panel, years[0])
    _, e = year_bounds(panel, years[-1])
    dates = panel.dates[s:e]
    ser = pd.Series(rel, index=dates).resample("W-FRI").last().dropna()
    return [[int(t.timestamp()), round(float(v), 4)] for t, v in ser.items()]


# ---------------------------------------------------------------- Daten laden
def universe() -> dict[str, dict[str, str]]:
    meta = {}
    for inst in load_settings().instruments:
        sym = inst.symbol_for("yahoo")
        if sym and inst.tier == "core" and inst.kind in ("aktie", "etf") and inst.market in ("xetra", "europe", "us"):
            meta[sym] = {"kind": inst.kind, "name": inst.name, "id": inst.id, "usd": inst.market == "us"}
    return meta


def load_yahoo_ohlc(meta: dict[str, dict[str, Any]]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    syms = sorted(meta)
    fx = yf.download("EURUSD=X", period="max", interval="1d", auto_adjust=True, progress=False)
    fx = fx["Close"].squeeze() if isinstance(fx.columns, pd.MultiIndex) else fx["Close"]
    fx.index = pd.DatetimeIndex(fx.index).tz_localize(None)
    out: dict[str, pd.DataFrame] = {}
    for k in range(0, len(syms), 40):
        chunk = syms[k:k + 40]
        raw = yf.download(chunk, period="max", interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)
        for sym in chunk:
            try:
                d = raw[sym] if len(chunk) > 1 else raw
            except KeyError:
                continue
            d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "close"])
            d = d[d.index >= f"{FIRST_YEAR - 1}-06-01"]
            d.index = pd.DatetimeIndex(d.index).tz_localize(None)
            if meta[sym].get("usd"):
                rate = fx.reindex(d.index, method="ffill")
                d[["open", "high", "low", "close"]] = d[["open", "high", "low", "close"]].div(rate, axis=0)
                d = d.dropna(subset=["close"])
            d = d[(d["low"] > 0) & (d["high"] >= d["low"])]
            if len(d):
                out[sym] = d
    return out


# ---------------------------------------------------------------- Bericht
def _p(x: float | None, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.1f} %".replace(".", ",") if sign else f"{x * 100:.1f} %".replace(".", ",")


def _eur(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def _n(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def report_markdown(r: dict[str, Any]) -> str:
    y0, y1 = r["pruefjahre"]
    lines = [
        "# Phase 5 – Strategien im Rückblick",
        "",
        f"Stand: {r['erstellt'][:10]}. {r['werte']} Werte (Aktien und ETFs aus Deutschland, Europa und den USA), "
        f"Prüfjahre {y0}–{y1}. Jede Strategie lernt {r['lernjahre']} Jahre (beste Einstellung) und wird im Folgejahr unberührt geprüft. "
        f"Startkapital {_eur(r['startkapital'])} je Prüfjahr, alle Kosten und Risikoregeln aus Phase 4. "
        "„Ohne Kosten“ zeigt dieselben Regeln ohne Gebühr, Spread und Schlupf.",
        "",
        "| Strategie | pro Jahr | ohne Kosten | größter Rückgang | Trades | Treffer | Gebühren gesamt | Jahre besser als MSCI World | besser als Zufall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    n_years = y1 - y0 + 1
    for s in sorted(r["strategien"], key=lambda s: -s["cagr"]):
        lines.append(f"| {s['name']} | {_p(s['cagr'])} | {_p(s['cagr_ohne_kosten'])} | {_p(s['max_rueckgang'])} | {s['trades']} | "
                     f"{_p(s['trefferquote'], False)} | {_eur(s['gebuehren'])} | {s['jahre_besser_als_msci']} von {n_years} | {s['besser_als_zufall_prozent']:.0f} % |")
    b = r.get("msci_world")
    if b:
        lines.append(f"| **{b['name']}** | {_p(b['cagr'])} | – | {_p(b['max_rueckgang'])} | 1 je Jahr | – | – | – | – |")
    z = r["zufall"]
    lines += ["", f"Zufällige Käufe mit denselben Regeln ({z['laeufe']} Läufe): Median {_p(z['cagr_median'])} pro Jahr, "
              f"die besten 10 % ab {_p(z['cagr_p90'])}; größter Rückgang im Median {_p(z['max_rueckgang_median'])}; "
              f"ohne Kosten im Median {_p(z['cagr_median_ohne_kosten'])}.",
              "„Besser als Zufall“ = Anteil der Zufallsläufe, die die Strategie übertrifft.", ""]
    lines += ["## Je Prüfjahr", "", "| Jahr | " + " | ".join(s["name"] for s in r["strategien"]) + " | MSCI World |",
              "|---|" + "---|" * (len(r["strategien"]) + 1)]
    for k, y in enumerate(range(y0, y1 + 1)):
        row = [_p(s["jahre"][k]["rendite"]) for s in r["strategien"]]
        bw = b["jahre"][k]["rendite"] if b else None
        lines.append(f"| {y} | " + " | ".join(row) + f" | {_p(bw)} |")
    kills = [(s["name"], k) for s in r["strategien"] for k in s["kill_switch"]]
    lines += ["", "## Kill Switch", ""]
    lines += [f"- {n}: {k}" for n, k in kills] or ["- in keinem Prüfjahr ausgelöst"]
    lines += ["", "## Die Strategien", ""] + [f"- **{s['name']}:** {s['idee']}" for s in r["strategien"]]
    lines += ["", "## Grenzen des Tests", ""] + [f"- {x}" for x in r["grenzen"]]
    return "\n".join(lines) + "\n"


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(float(o)) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    return o


def main() -> int:
    meta = universe()
    print(f"Lade Tageskerzen für {len(meta)} Werte …", flush=True)
    frames = load_yahoo_ohlc(meta)
    r = _jsonable(run(frames, meta, progress=lambda m: print(m, flush=True)))
    try:  # im Projekt (GitHub); auf dem Server darf der Dienst dort nicht schreiben
        status = ROOT / "status" / "strategien.json"
        status.parent.mkdir(exist_ok=True)
        status.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (ROOT / "app" / "strategien.json").write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (ROOT / "docs" / "STRATEGIEN.md").write_text(report_markdown(r), encoding="utf-8")
    except OSError as err:
        print(f"Bericht im Projektordner nicht geschrieben: {err}")
    # Für die App: auf dem Server neben die Kursdaten (nur Testergebnisse, keine persönlichen Daten)
    site = os.environ.get("TA_EXPORT_DIR")
    if site and os.environ.get("TA_QUOTES_FILE"):
        p = Path(site).parent / "strategien.json"
        p.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(report_markdown(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
