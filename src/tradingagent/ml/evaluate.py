"""Walk-Forward-Prüfung der Lernmodelle: Prognosequalität, Kalibrierung, Handel mit den Phase-4-Regeln, Zulassung.

Je Prüfjahr Y (streng chronologisch, nie gemischt):
  Lernzeitraum     Jahre Y−4 … Y−2   nur Zeilen, deren Zielende vor dem Kalibrierjahr liegt (Purging)
  Kalibrierjahr    Jahr Y−1          Auswahl der Einstellung (AUC) und Platt-Kalibrierung;
                                     nur Zeilen, deren Zielende vor dem Prüfjahr liegt
  Prüfjahr         Jahr Y            unberührt; daraus stammen alle berichteten Zahlen
Skalierung, Kern und Baum werden nur im Lernzeitraum angepasst (alles steckt im Modell-Objekt).
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

from ..risk import RiskConfig, RiskManager
from ..strategy_test import Panel, _chain, _score, simulate, stats, year_bounds
from .features import FEATURES
from .models import MODEL_NAMES, Calibrated, fit_calibrated, svm_grid, tree_grid

KINDS = ("svm", "tree", "base")


# ---------------------------------------------------------------- Prognosequalität
def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[list[dict[str, Any]], float]:
    edges = np.linspace(0, 1, bins + 1)
    out, ece = [], 0.0
    for k in range(bins):
        m = (p >= edges[k]) & (p < edges[k + 1] if k < bins - 1 else p <= 1)
        if m.sum() == 0:
            continue
        mp, my = float(p[m].mean()), float(y[m].mean())
        out.append({"von": float(edges[k]), "bis": float(edges[k + 1]), "prognose": mp, "eingetreten": my, "anzahl": int(m.sum())})
        ece += m.sum() / len(p) * abs(mp - my)
    return out, float(ece)


def forecast_metrics(p: np.ndarray, y: np.ndarray, base_rate: float) -> dict[str, Any]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    pred = p >= 0.5
    tp, fp, fn = int((pred & (y == 1)).sum()), int((pred & (y == 0)).sum()), int((~pred & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    brier = float(np.mean((p - y) ** 2))
    base_brier = float(np.mean((base_rate - y) ** 2))
    rel, ece = reliability(p, y)
    return {
        "n": int(len(y)), "positiv_anteil": float(y.mean()), "trefferquote": float((pred == (y == 1)).mean()),
        "precision": prec, "recall": rec, "f1": (2 * prec * rec / (prec + rec)) if prec and rec else None,
        "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
        "brier": brier, "brier_grundrate": base_brier, "brier_skill": 1 - brier / base_brier if base_brier > 0 else None,
        "log_loss": float(log_loss(y, p, labels=[0, 1])), "ece": ece, "kalibrierung": rel,
    }


# ---------------------------------------------------------------- Zeitfenster
def windows(data: pd.DataFrame, year: int, learn_years: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    d = data["date"]
    cal_start, test_start = pd.Timestamp(year - 1, 1, 1), pd.Timestamp(year, 1, 1)
    learn_start = pd.Timestamp(year - 1 - learn_years, 1, 1)
    has_y = data["y"].notna()
    fit = (d >= learn_start) & (d < cal_start) & (data["label_end"] < cal_start) & has_y
    cal = (d >= cal_start) & (d < test_start) & (data["label_end"] < test_start) & has_y
    test = (d >= test_start) & (d < pd.Timestamp(year + 1, 1, 1)) & has_y
    return fit, cal, test


def train_one(kind: str, data: pd.DataFrame, fit: pd.Series, cal: pd.Series, cfg: dict, level: int) -> Calibrated:
    seed = int(cfg["training"]["zufallssaat"])
    rng = np.random.default_rng(seed)
    idx = np.flatnonzero(fit.to_numpy())
    cap = int(cfg["training"]["max_trainingszeilen"]) * (2 if level >= 3 else 1)
    if len(idx) > cap:
        idx = np.sort(rng.choice(idx, cap, replace=False))
    X_fit, y_fit = data.loc[idx, FEATURES].to_numpy(float), data.loc[idx, "y"].to_numpy(float)
    X_cal, y_cal = data.loc[cal, FEATURES].to_numpy(float), data.loc[cal, "y"].to_numpy(float)
    if kind == "base":
        return fit_calibrated("base", {}, X_fit, y_fit, X_cal, y_cal, seed)
    grid = svm_grid(level) if kind == "svm" else tree_grid(level)
    best, best_auc = None, -1.0
    for params in grid:  # Auswahl NUR im Kalibrierjahr (Prüfjahr bleibt unberührt)
        m = fit_calibrated(kind, params, X_fit, y_fit, X_cal, y_cal, seed)
        auc = roc_auc_score(y_cal, m.predict(X_cal)) if 0 < y_cal.sum() < len(y_cal) else 0.5
        if auc > best_auc:
            best, best_auc = m, auc
    best.meta = {"auc_kalibrierjahr": float(best_auc), "zeilen_training": int(len(idx)), "zeilen_kalibrierung": int(cal.sum())}
    return best


def walk_forward(data: pd.DataFrame, cfg: dict, *, level: int = 1, kinds: tuple[str, ...] = KINDS,
                 progress: Callable[[str], None] = print) -> dict[str, Any]:
    learn = int(cfg["training"]["lernjahre"])
    first = int(data["date"].min().year) + learn + 1 + 1   # erstes vollständiges Lernjahr + Kalibrierjahr
    last = int(data.loc[data["y"].notna(), "date"].max().year)
    years = list(range(first, last + 1))
    preds, per_year, chosen = [], {k: [] for k in kinds}, {k: [] for k in kinds}
    for y in years:
        fit, cal, test = windows(data, y, learn)
        if fit.sum() < 2000 or cal.sum() < 500 or test.sum() < 200:
            continue
        X_test = data.loc[test, FEATURES].to_numpy(float)
        y_test = data.loc[test, "y"].to_numpy(float)
        for k in kinds:
            t0 = time.time()
            m = train_one(k, data, fit, cal, cfg, level)
            p = m.predict(X_test)
            part = data.loc[test, ["date", "sym", "y", "fwd"]].copy()
            part["kind"], part["p"] = k, p
            preds.append(part)
            met = forecast_metrics(p, y_test, m.base_rate)
            met.update({"jahr": y, "sekunden": round(time.time() - t0, 1)})
            per_year[k].append(met)
            chosen[k].append({"jahr": y, "einstellung": m.params, **m.meta})
        progress(f"  Prüfjahr {y}: " + ", ".join(f"{MODEL_NAMES[k]} AUC {per_year[k][-1]['auc']:.3f}" for k in kinds if per_year[k] and per_year[k][-1]["auc"]))
    allp = pd.concat(preds, ignore_index=True)
    overall = {}
    for k in kinds:
        sel = allp[allp["kind"] == k]
        # Grundrate für den Gesamt-Skill: je Jahr die Rate des jeweiligen Lernzeitraums
        base_p = allp[allp["kind"] == "base"].set_index(["date", "sym"])["p"] if "base" in kinds else None
        br = base_p.reindex(sel.set_index(["date", "sym"]).index).to_numpy() if base_p is not None else np.full(len(sel), sel["y"].mean())
        met = forecast_metrics(sel["p"].to_numpy(), sel["y"].to_numpy(), 0.5)
        brier_base = float(np.mean((br - sel["y"].to_numpy()) ** 2))
        met["brier_grundrate"], met["brier_skill"] = brier_base, 1 - met["brier"] / brier_base
        met["anteil_jahre_brier_skill_positiv"] = float(np.mean([(x["brier_skill"] or 0) > 0 for x in per_year[k]]))
        # Kalibrierung genau dort, wo gekauft würde (hohe Prognosen) – die Gesamt-ECE kann das verdecken
        lo = min(cfg["handel"]["schwellen"])
        hi = sel[sel["p"] >= lo]
        met["kaufbereich"] = {"ab": lo, "n": int(len(hi)), "prognose": float(hi["p"].mean()) if len(hi) else None,
                              "eingetreten": float(hi["y"].mean()) if len(hi) else None}
        overall[k] = met
    return {"jahre": [y for y in years if any(x["jahr"] == y for x in per_year[kinds[0]])],
            "je_jahr": per_year, "gesamt": overall, "einstellungen": chosen, "prognosen": allp}


# ---------------------------------------------------------------- Handel mit den Phase-4-Regeln
def prob_matrix(panel: Panel, preds: pd.DataFrame) -> np.ndarray:
    P = np.full((len(panel.dates), len(panel.ids)), np.nan)
    col = {s: j for j, s in enumerate(panel.ids)}
    row = pd.Series(np.arange(len(panel.dates)), index=panel.dates)
    sel = preds[preds["sym"].isin(col)]
    r = row.reindex(pd.DatetimeIndex(sel["date"])).to_numpy()
    ok = ~np.isnan(r)
    P[r[ok].astype(int), sel["sym"].map(col).to_numpy()[ok]] = sel["p"].to_numpy()[ok]
    return P


def trade_details(log: list[dict[str, Any]]) -> dict[str, Any]:
    opened, holds = {}, []
    for e in log:
        if not e["ok"]:
            continue
        if e["side"] == "buy":
            opened[e["id"]] = e["t"]
        elif e["side"] == "sell" and e["id"] in opened:
            holds.append((e["t"] - opened.pop(e["id"])) / 86400)
    return {"haltedauer_tage": float(np.mean(holds)) if holds else None}


def trade(panel: Panel, preds: pd.DataFrame, years: list[int], cfg: dict, rm: RiskManager) -> dict[str, Any]:
    h = cfg["handel"]
    P = prob_matrix(panel, preds)
    horizon = int(cfg["ziel"]["horizont_tage"])
    exit_ = np.nan_to_num(P, nan=1.0) < float(h["verkauf_unter"])
    cap = rm.cfg.start_capital
    curve, trades, fees, kills, yrs, holds = [], [], 0.0, [], [], []
    thr = float(h["start_schwelle"])
    prev: tuple[int, int] | None = None
    for y in years:
        if prev is not None:  # Schwelle am Vorjahr wählen – dessen Prognosen waren selbst schon Prüfprognosen
            best = max(h["schwellen"], key=lambda t: _score(stats(
                simulate(panel, np.nan_to_num(P, nan=0) >= t, exit_, *prev, rm=rm, min_hold=horizon, rank=P)["equity"], cap)))
            thr = float(best)
        s, e = year_bounds(panel, y)
        r = simulate(panel, np.nan_to_num(P, nan=0) >= thr, exit_, s, e, rm=rm, min_hold=horizon, rank=P)
        st = stats(r["equity"], cap, r["trades"])
        yrs.append({"jahr": y, "rendite": st["rendite"], "max_rueckgang": st["max_rueckgang"], "trades": st["trades"],
                    "schwelle": thr, "gestoppt": r["kills"]})
        curve.append(r["equity"] / cap)
        trades += r["trades"]
        fees += r["fees"]
        kills += [f"{y}: {k}" for k in r["kills"]]
        d = trade_details(r["log"])
        if d["haltedauer_tage"]:
            holds.append(d["haltedauer_tage"])
        prev = (s, e)
    chained = _chain(curve) * cap
    out = stats(chained, cap, trades)
    out.update({"gebuehren": round(fees, 2), "jahre": yrs, "kill_switch": kills,
                "haltedauer_tage": float(np.mean(holds)) if holds else None,
                "anteil_positive_jahre": float(np.mean([x["rendite"] > 0 for x in yrs])) if yrs else 0.0,
                "chained": chained})
    return out


# ---------------------------------------------------------------- Zulassung
def admission(kind: str, forecast: dict[str, Any], trading: dict[str, Any], msci_cagr: float | None,
              random_p90: float | None, cfg: dict) -> dict[str, Any]:
    z = cfg["zulassung"]
    checks = [
        ("Genug Testentscheidungen", trading.get("trades", 0), f"≥ {z['min_trades']} Trades", trading.get("trades", 0) >= z["min_trades"]),
        ("Trennschärfe (AUC)", forecast.get("auc"), f"≥ {z['min_auc']}", (forecast.get("auc") or 0) >= z["min_auc"]),
        ("Besser als die Grundrate (Brier Skill)", forecast.get("brier_skill"), f"> {z['min_brier_skill']}", (forecast.get("brier_skill") or -1) > z["min_brier_skill"]),
        ("Stabil über die Jahre", forecast.get("anteil_jahre_brier_skill_positiv"), f"in ≥ {z['min_anteil_jahre_brier_skill']:.0%} der Jahre besser als Grundrate",
         (forecast.get("anteil_jahre_brier_skill_positiv") or 0) >= z["min_anteil_jahre_brier_skill"]),
        ("Kalibrierung (ECE)", forecast.get("ece"), f"≤ {z['max_ece']}", (forecast.get("ece") if forecast.get("ece") is not None else 1) <= z["max_ece"]),
        _buy_zone_check(forecast.get("kaufbereich") or {}, z),
        ("Rendite nach Kosten über MSCI World", trading.get("cagr"), f"> {msci_cagr:.3f}" if msci_cagr is not None else "MSCI fehlt",
         msci_cagr is not None and trading.get("cagr", -1) > msci_cagr if z["besser_als_msci"] else True),
        ("Besser als Zufall (beste 10 %)", trading.get("cagr"), f"> {random_p90:.3f}" if random_p90 is not None else "Zufall fehlt",
         random_p90 is not None and trading.get("cagr", -1) > random_p90 if z["besser_als_zufall_p90"] else True),
        ("Größter Rückgang", trading.get("max_rueckgang"), f"höchstens −{z['max_rueckgang']:.0%}", trading.get("max_rueckgang", -1) >= -z["max_rueckgang"]),
        ("Positive Jahre", trading.get("anteil_positive_jahre"), f"≥ {z['min_anteil_positive_jahre']:.0%}", trading.get("anteil_positive_jahre", 0) >= z["min_anteil_positive_jahre"]),
    ]
    rows = [{"pruefung": n, "wert": (None if v is None or (isinstance(v, float) and math.isnan(v)) else v), "grenze": g.replace(".", ","), "ok": bool(ok)}
            for n, v, g, ok in checks]
    passed = all(r["ok"] for r in rows)
    return {"modell": kind, "status": "Für Phase 7 zugelassen" if passed else "Nicht freigegeben", "zugelassen": passed, "pruefungen": rows}


def _buy_zone_check(kb: dict[str, Any], z: dict) -> tuple:
    """Prognosen ab der Kaufschwelle: tritt ein, was vorhergesagt wird? (mind. N Fälle, Abweichung begrenzt)"""
    n, p, y = kb.get("n", 0), kb.get("prognose"), kb.get("eingetreten")
    gap = abs(p - y) if p is not None and y is not None else None
    ok = n >= z["min_faelle_kaufbereich"] and gap is not None and gap <= z["max_abweichung_kaufbereich"]
    return ("Kalibrierung im Kaufbereich", gap, f"Abweichung ≤ {z['max_abweichung_kaufbereich']} bei ≥ {z['min_faelle_kaufbereich']} Fällen (hier {n})", ok)


def leak_check(data: pd.DataFrame, cfg: dict, years: list[int]) -> dict[str, Any]:
    """Gegenprobe: Zielgröße im Lernzeitraum zufällig vertauscht. Lernt das Modell trotzdem 'etwas',
    stimmt etwas mit den Daten nicht (Leck). Erwartet: AUC um 0,50."""
    rng = np.random.default_rng(7)
    learn = int(cfg["training"]["lernjahre"])
    out = []
    for y in years[-2:]:
        fit, cal, test = windows(data, y, learn)
        d = data.copy()
        d.loc[fit, "y"] = rng.permutation(d.loc[fit, "y"].to_numpy())
        d.loc[cal, "y"] = rng.permutation(d.loc[cal, "y"].to_numpy())
        m = train_one("tree", d, fit, cal, cfg, 1)
        p = m.predict(data.loc[test, FEATURES].to_numpy(float))
        out.append({"jahr": y, "auc": float(roc_auc_score(data.loc[test, "y"], p))})
    return {"beschreibung": "Baum mit zufällig vertauschter Zielgröße trainiert; ohne Leck liegt die AUC bei etwa 0,50.",
            "ergebnisse": out, "ok": all(abs(x["auc"] - 0.5) < 0.03 for x in out)}


def default_rm() -> RiskManager:
    return RiskManager(RiskConfig.load())
