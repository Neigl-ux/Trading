"""Ratgeber (Phase 3): Bewertung aller Werte, Sparplan-Vorschlag, Nachkauf-Stufen, Weltlage.

Läuft im Sammler nach dem Export. Alles hier ist allgemein (für jeden gleich);
was dein Portfolio betrifft, rechnet die App nur auf deinem Gerät.

Nur Information, keine Anlageberatung, keine Aufträge.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from . import news as news_mod
from . import scoring
from .config import ROOT

PLAN_EUR = 120
CORE_EUR = 90
RESERVE_EUR = 1000
STAGES = [(0.10, 100), (0.15, 150), (0.20, 200), (0.25, 250), (0.30, 300)]
# Breite Welt-ETFs, aus denen der Kern gewählt wird (Reihenfolge = Vorrang bei Gleichstand)
CORE_IDS = ["VWCE", "EUNL", "IUSQ", "XDWD", "VGWL"]
# Gesamtmarkt-Maßstäbe für die gesonderte Markt-Meldung
MARKET_IDS = [("EUNL", "MSCI World"), ("SXR8", "S&P 500")]
# Stufen für den Kern-ETF gelten auch ohne Punktzahl 65: Fällt der ganze Weltmarkt,
# sinkt die Trend-Punktzahl jedes Welt-ETFs zwangsläufig. Der Rückblick-Test
# (docs/RUECKBLICK.md, Variante „nur bei Punktzahl ab 65“) prüft diese Entscheidung.
CORE_NEEDS_SCORE = False
WEAK_DAYS = 90          # „drei Monate in Folge unter 40“
WEAK_MIN_SNAPSHOTS = 60
SCORABLE = {"aktie", "etf", "krypto"}


def _load_json(storage, key: str, default: Any) -> Any:
    raw = storage.get_meta(key)
    return json.loads(raw) if raw else default


def stage_of(dd: float) -> int:
    return sum(1 for thr, _ in STAGES if dd >= thr - 1e-9)


def is_weak(hist: dict[str, dict[str, float]], vid: str, today: str) -> bool:
    """Lag der Wert an allen Tagen der letzten 90 Tage unter 40 (bei genug Tagen Verlauf)?"""
    start = (pd.Timestamp(today) - pd.Timedelta(days=WEAK_DAYS)).strftime("%Y-%m-%d")
    vals = [day[vid] for d, day in hist.items() if d >= start and vid in day]
    return len(vals) >= WEAK_MIN_SNAPSHOTS and all(v < scoring.NEUTRAL for v in vals)


def returns_1y(closes: pd.Series) -> pd.Series:
    s = closes.dropna()
    s = s[~s.index.normalize().duplicated(keep="last")]
    s.index = s.index.normalize()
    return np.log(s).diff().iloc[-253:]


def corr(a: pd.Series, b: pd.Series) -> float | None:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < 120:
        return None
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def sat_candidate(inst, sc: dict[str, Any]) -> bool:
    return (inst.kind in ("etf", "aktie") and inst.id not in CORE_IDS and sc["score"] >= scoring.INTERESTING
            and not scoring.is_leveraged(inst.name) and not scoring.is_defensive(inst.name))


def choose_plan(scores: dict[str, dict], insts: dict, closes: dict[str, pd.Series], prev: dict[str, Any],
                hist: dict, today: str) -> tuple[dict[str, Any], list[str]]:
    """Kern + bis zu 2 Satelliten. Bestehende Bausteine bleiben, solange sie nicht 3 Monate unter 40 liegen."""
    changes: list[str] = []
    core = prev.get("core")
    if core and core in insts and not is_weak(hist, core, today):
        pass
    else:
        avail = [c for c in CORE_IDS if c in scores]
        new_core = max(avail, key=lambda c: (scores[c]["score"], -CORE_IDS.index(c))) if avail else None
        if core and new_core != core:
            changes.append(f"Kern: {insts[new_core].name} ersetzt {insts[core].name if core in insts else core} "
                           "(drei Monate unter 40 Punkten).")
        core = new_core
    if core is None:
        return {"core": None, "sats": []}, changes
    core_ret = returns_1y(closes[core]) if core in closes else None
    sats: list[str] = []
    for s in prev.get("sats", []):
        if s in insts and not is_weak(hist, s, today):
            sats.append(s)
        else:
            changes.append(f"Satellit {insts[s].name if s in insts else s} entfällt (drei Monate unter 40 Punkten).")
    ranked = sorted((vid for vid, sc in scores.items() if vid in insts and sat_candidate(insts[vid], sc)),
                    key=lambda v: (insts[v].kind != "etf", -scores[v]["score"]))
    for vid in ranked:
        if len(sats) >= 2:
            break
        if vid in sats or vid not in closes:
            continue
        r = returns_1y(closes[vid])
        cc = corr(r, core_ret) if core_ret is not None else None
        if cc is None or cc >= 0.85:
            continue  # zu ähnlich zum Kern (oder zu wenig gemeinsamer Verlauf)
        if any((corr(r, returns_1y(closes[s])) or 1) >= 0.80 for s in sats if s in closes):
            continue
        sats.append(vid)
        if prev.get("core"):
            changes.append(f"Neuer Satellit: {insts[vid].name} ({scores[vid]['score']:.0f} Punkte).")
    return {"core": core, "sats": sats}, changes


def amounts(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not plan.get("core"):
        return []
    sats = plan["sats"]
    if not sats:
        return [{"id": plan["core"], "role": "Kern", "eur": PLAN_EUR}]
    each = (PLAN_EUR - CORE_EUR) // len(sats)
    return [{"id": plan["core"], "role": "Kern", "eur": CORE_EUR}] + [{"id": s, "role": "Satellit", "eur": each} for s in sats]


def verdict(bt: dict[str, Any] | None) -> dict[str, Any] | None:
    """Was der Rückblick-Test über Punktzahl und Staffel sagt (siehe docs/RUECKBLICK.md)."""
    if not bt or "score" not in bt or "error" in bt["score"]:
        return None
    sc = bt["score"]
    etf = sc.get("etf", {})
    bench = (sc.get("benchmark_12M") or {}).get("mean")
    top = (etf.get("interessant", {}).get("12M") or {}).get("mean")
    beats = bench is not None and top is not None and top > bench
    stocks = sc.get("aktie", {})
    risk_ok = all(k.get("interessant", {}).get("dd12_over30", 1) < k.get("vorsicht", {}).get("dd12_over30", 0)
                  for k in (etf, stocks) if k)
    pyr = None
    for row in bt.get("pyramid", []):
        if row.get("symbol") == "EUNL.DE" and row.get("5J"):
            p = row["5J"]
            pyr = {k: {"win": v["win_share"], "median": v["median_diff"]} for k, v in p.items() if v}
    return {"created": bt.get("created"), "score_beats_benchmark": beats, "top_12m": top, "bench_12m": bench,
            "risk_filter_works": risk_ok, "pyramid_world_5y": pyr}


def build(storage, now: pd.Timestamp, items: list[tuple[Any, Any, dict[str, Any]]], *,
          fetch_news: bool = True, news_client=None) -> dict[str, Any]:
    """``items`` = [(Instrument, Loader, Export-Eintrag)] aller exportierten Werte."""
    today = now.tz_convert("Europe/Berlin").strftime("%Y-%m-%d")
    insts = {inst.id: inst for inst, _, _ in items}
    scores: dict[str, dict[str, Any]] = {}
    closes: dict[str, pd.Series] = {}
    for inst, loader, entry in items:
        if inst.kind not in SCORABLE or inst.market == "futures":
            continue
        df = loader.load("1d", now - pd.Timedelta(days=1150))
        if df.empty:
            continue
        c = df["close"]
        last = entry.get("last")
        if last and entry.get("last_ts") and entry["last_ts"] > c.index[-1].timestamp():
            c = c.copy()
            c.loc[pd.Timestamp(entry["last_ts"], unit="s", tz="UTC")] = float(last)  # aktueller Kurs zählt mit
        res = scoring.score(c.to_numpy(), df["volume"].reindex(c.index).fillna(0).to_numpy(), kind=inst.kind, name=inst.name)
        if res is None:
            continue
        scores[inst.id] = res
        closes[inst.id] = c

    # Verlauf der Punktzahlen (für die Drei-Monats-Regel)
    hist = _load_json(storage, "adv_hist", {})
    hist[today] = {vid: round(sc["score"]) for vid, sc in scores.items()}
    for old in sorted(hist)[:-120]:
        del hist[old]
    storage.set_meta("adv_hist", json.dumps(hist, separators=(",", ":")))

    prev = _load_json(storage, "adv_plan", {})
    plan, changes = choose_plan(scores, insts, closes, prev, hist, today)
    log = prev.get("log", [])
    if not prev.get("core") and plan.get("core"):
        changes.append("Erster Vorschlag erstellt.")
    for text in changes:
        log.append({"date": today, "text": text})
    plan_state = {**plan, "since": prev.get("since") if not changes and prev.get("core") else today, "log": log[-12:]}
    storage.set_meta("adv_plan", json.dumps(plan_state, separators=(",", ":")))
    plan_ids = [x for x in [plan.get("core"), *plan.get("sats", [])] if x]

    # Nachrichten: Aktien im Plan, Aktien mit Rückgang ab 10 %, dann gut bewertete Aktien
    def is_stock(v): return insts[v].kind == "aktie"
    order = [v for v in plan_ids if is_stock(v)]
    order += sorted((v for v, sc in scores.items() if is_stock(v) and sc["facts"]["dd12"] >= 0.10 and sc["score"] >= scoring.NEUTRAL),
                    key=lambda v: -scores[v]["facts"]["dd12"])
    order += sorted((v for v, sc in scores.items() if is_stock(v) and sc["score"] >= scoring.INTERESTING),
                    key=lambda v: -scores[v]["score"])[:80]
    targets = list(dict.fromkeys(order))
    news_state = {"world": {}, "values": {}}
    if fetch_news:
        try:
            news_state = news_mod.update(storage, now, [(v, insts[v].name) for v in targets], client=news_client)
        except Exception as exc:  # noqa: BLE001
            news_state = {**_load_json(storage, "news_state", {"world": {}, "values": {}}), "errors": [str(exc)]}
    else:
        news_state = _load_json(storage, "news_state", {"world": {}, "values": {}})
    vnews = news_state.get("values", {})

    # Nachkauf-Stufen für die Bausteine des Sparplans (Zustand je Rückgang)
    st_state = _load_json(storage, "adv_stages", {})
    events = _load_json(storage, "adv_events", [])
    for vid in plan_ids:
        sc = scores.get(vid)
        if sc is None:
            continue
        dd = sc["facts"]["dd12"]
        s = st_state.get(vid, {"hit": 0, "since": today})
        if dd <= 0.005:
            s = {"hit": 0, "since": today}  # neues 12-Monats-Hoch: Stufen wieder frei
        stage = stage_of(dd)
        allowed = (vid == plan.get("core") and not CORE_NEEDS_SCORE) or sc["score"] >= scoring.INTERESTING
        if stage > s["hit"]:
            check = news_mod.flagged(vnews.get(vid))
            events.append({"key": f"{vid}:{s['since']}:{stage}", "id": vid, "name": insts[vid].name, "stage": stage,
                           "dd": round(dd, 4), "date": today, "ts": int(now.timestamp()), "allowed": allowed,
                           "check_news": check, "score": round(sc["score"])})
            s["hit"] = stage
        st_state[vid] = s
    st_state = {k: v for k, v in st_state.items() if k in plan_ids}
    storage.set_meta("adv_stages", json.dumps(st_state, separators=(",", ":")))
    storage.set_meta("adv_events", json.dumps(events[-50:], separators=(",", ":")))

    # Gesamtmarkt
    market = []
    for mid, label in MARKET_IDS:
        sc = scores.get(mid)
        if sc:
            market.append({"id": mid, "label": label, "dd": round(sc["facts"]["dd12"], 4), "stage": stage_of(sc["facts"]["dd12"])})

    # Vergleich zum Vortag (für den Lagebericht)
    days = sorted(hist)
    prev_day = next((d for d in reversed(days) if d <= (pd.Timestamp(today) - pd.Timedelta(days=2)).strftime("%Y-%m-%d")), None)
    moves = []
    if prev_day:
        old = hist[prev_day]
        for vid, sc in scores.items():
            if vid in old and insts[vid].kind != "krypto":
                moves.append((round(sc["score"]) - old[vid], vid))
        moves.sort()

    world = news_mod.world_summary(news_state)
    backtest = None
    bt_path = ROOT / "status" / "backtest.json"
    if bt_path.exists():
        try:
            backtest = json.loads(bt_path.read_text(encoding="utf-8"))
        except ValueError:
            backtest = None
    vd = verdict(backtest)
    # Regel aus der Konzeption: Schlägt die Punktzahl im Rückblick den schlichten Welt-ETF nicht,
    # lautet die Empfehlung „alles in den Welt-ETF“; Kern + Satelliten bleibt als Alternative sichtbar.
    simple = vd is not None and not vd["score_beats_benchmark"]
    plan_items = [{"id": plan["core"], "role": "Kern", "eur": PLAN_EUR}] if simple and plan.get("core") else amounts(plan)

    values = {}
    for vid, sc in scores.items():
        f = sc["facts"]
        n = vnews.get(vid)
        values[vid] = {
            "s": sc["score"], "g": sc["grade"], "r": sc["risk"],
            "f": [sc["factors"][k] for k in scoring.WEIGHTS],
            "w": sc["why"], "dd": round(f["dd12"], 4), "st": stage_of(f["dd12"]),
            "vol": round(f["vol"], 4), "mdd": round(f["mdd3"], 4), "r12": round(f["r12_1"], 4),
            "n": None if n is None else {"n": n.get("n", 0), "a": n.get("a", []), "t": n.get("t")},
        }
    report = {
        "date": today,
        "world": world.get("level"),
        "plan": plan_items,
        "changes": [x for x in plan_state["log"] if x["date"] >= (prev_day or today)],
        "up": [[vid, d] for d, vid in moves[-3:][::-1] if d >= 5],
        "down": [[vid, d] for d, vid in moves[:3] if d <= -5],
        "stages": [e for e in events if e["ts"] >= now.timestamp() - 2 * 86400],
        "market": market,
    }
    storage.set_meta("adv_report", json.dumps(report, separators=(",", ":")))
    storage.set_meta("adv_updated", str(int(now.timestamp())))
    return {
        "updated": int(now.timestamp()),
        "weights": scoring.WEIGHTS,
        "missing": ["Qualität und Bewertung: keine kostenlose Quelle im Sammler"],
        "values": values,
        "verdict": vd,
        "plan": {"items": plan_items, "mode": "einfach" if simple else "kern_satellit",
                 "alternative": amounts(plan) if simple else None, "since": plan_state.get("since"), "log": plan_state["log"],
                 "reserve": RESERVE_EUR, "stages": [{"dd": t, "eur": e} for t, e in STAGES],
                 "core_needs_score": CORE_NEEDS_SCORE},
        "stage_state": st_state,
        "events": events[-20:],
        "market": market,
        "world": world,
        "news_errors": news_state.get("errors", []),
        "report": report,
        "backtest": backtest,
    }
