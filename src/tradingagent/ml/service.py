"""Befehle für Phase 6 und der Dauerdienst auf dem Server.

  python -m tradingagent ml train [--stufe N]   Walk-Forward-Prüfung, Vergleich, Zulassung, neue Modellversion
  python -m tradingagent ml predict             Prognosen für den letzten Handelstag (alle Kernwerte)
  python -m tradingagent ml earnings            Quartalszahlen-Kalender und Branchenhinweise
  python -m tradingagent ml worker              Dauerdienst: plant die Aufgaben selbst und rechnet rund um die Uhr

Ausgaben (nur Modell- und Marktdaten, nichts Persönliches): ki.json (Bericht und Modelle),
ki_prognosen.json, ki_quartale.json, ki_status.json (laufende Aufgabe). Auf dem Server neben den
Kursdaten (für die App), auf GitHub in app/ und status/.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import ROOT
from . import load_config
from .features import FEATURE_NAMES_DE, FEATURES, build_dataset
from .models import MODEL_NAMES

BERLIN = ZoneInfo("Europe/Berlin")
LEVELS = (1, 2, 3, 4)


# ---------------------------------------------------------------- Ausgabe
def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(float(o)) or math.isinf(float(o))) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def write_output(name: str, obj: dict[str, Any]) -> None:
    text = json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":"))
    targets = []
    if os.environ.get("TA_QUOTES_FILE") and os.environ.get("TA_EXPORT_DIR"):
        targets.append(Path(os.environ["TA_EXPORT_DIR"]).parent / name)   # Server: neben die Kursdaten
    if not os.environ.get("TA_QUOTES_FILE"):
        targets += [ROOT / "app" / name, ROOT / "status" / name]          # GitHub: in die App und ins Projekt
    for p in targets:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(p)
        except OSError as err:
            print(f"{p} nicht geschrieben: {err}")


def status(job: str, progress: float | None = None, message: str = "", **extra: Any) -> None:
    write_output("ki_status.json", {"aufgabe": job, "fortschritt": progress, "meldung": message,
                                    "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra})


# ---------------------------------------------------------------- Daten
def load_inputs() -> tuple[dict, dict, pd.DataFrame | None, dict]:
    from .. import strategy_test as st
    from .earnings import industries

    meta = st.universe()
    frames = st.load_yahoo_ohlc(meta)
    try:
        ind = industries([s for s, m in meta.items() if m["kind"] == "aktie" and s in frames])
    except Exception as err:  # noqa: BLE001
        print(f"Branchen nicht verfügbar: {err}")
        ind = {}
    hist = None
    p = ROOT / "status" / "news_history.csv"
    if p.exists():
        hist = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    return meta, frames, hist, ind


# ---------------------------------------------------------------- Training und Prüfung
def train(level: int = 1, inputs: tuple | None = None) -> dict[str, Any]:
    from .. import strategy_test as st
    from ..strategies import STRATEGIES
    from . import evaluate as ev
    from .registry import Registry

    cfg = load_config()
    t0 = time.time()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status(f"Training Stufe {level}", 0.02, "Lade Kursdaten …")
    meta, frames, hist, ind = inputs or load_inputs()
    horizon, move = int(cfg["ziel"]["horizont_tage"]), float(cfg["ziel"]["mindestbewegung_prozent"]) / 100
    data = build_dataset(frames, st.BENCH, horizon=horizon, min_move=move, industry=ind, news_hist=hist)
    status(f"Training Stufe {level}", 0.08, f"{len(data):,} Zeilen aus {data['sym'].nunique()} Werten".replace(",", "."))
    n_years_guess = data["date"].dt.year.nunique()
    done = {"n": 0}

    def prog(msg: str) -> None:
        print(msg, flush=True)
        done["n"] += 1
        status(f"Training Stufe {level}", min(0.6, 0.08 + 0.52 * done["n"] / max(1, n_years_guess - 5)), msg.strip())

    wf = ev.walk_forward(data, cfg, level=level, progress=prog)
    years = wf["jahre"]
    status(f"Training Stufe {level}", 0.62, "Handelstest mit den Regeln aus Phase 4 …")
    panel = st.build_panel(frames, meta)
    rm = ev.default_rm()
    cap = rm.cfg.start_capital
    trading = {k: ev.trade(panel, wf["prognosen"][wf["prognosen"]["kind"] == k], years, cfg, rm) for k in ("svm", "tree")}
    status(f"Training Stufe {level}", 0.75, "Vergleich mit MSCI World, Zufall und Phase 5 …")
    bench_years = {}
    msci = None
    if st.BENCH in panel.ids:
        curve = []
        for y in years:
            s, e = st.year_bounds(panel, y)
            eq = st.buy_and_hold(panel, st.BENCH, s, e, rm)
            bench_years[y] = st.stats(eq, cap)["rendite"]
            curve.append(eq / cap)
        msci = st.stats(st._chain(curve) * cap, cap)
        msci["jahre"] = [{"jahr": y, "rendite": bench_years[y]} for y in years]
    rng = np.random.default_rng(42)
    rand = []
    for _ in range(10):
        en, ex = st.random_signals(panel, rng, p_entry=0.01, hold=20)
        curve = [st.simulate(panel, en, ex, *st.year_bounds(panel, y), rm=rm)["equity"] / cap for y in years]
        rand.append(st.stats(st._chain(curve) * cap, cap)["cagr"])
    random_res = {"cagr_median": float(np.median(rand)), "cagr_p90": float(np.percentile(rand, 90)), "laeufe": len(rand)}
    tech = [st.walk_forward(panel, s, years, rm, bench_years) for s in STRATEGIES]
    tech_best = max(tech, key=lambda r: r["cagr"])
    status(f"Training Stufe {level}", 0.85, "Zulassungsprüfung und Gegenprobe auf Datenlecks …")
    leak = ev.leak_check(data, cfg, years)
    admissions = {k: ev.admission(k, wf["gesamt"][k], trading[k], msci["cagr"] if msci else None, random_res["cagr_p90"], cfg)
                  for k in ("svm", "tree")}
    if not leak["ok"]:
        for a in admissions.values():
            a["zugelassen"], a["status"] = False, "Nicht freigegeben"
            a["pruefungen"].append({"pruefung": "Gegenprobe Datenleck", "wert": None, "grenze": "AUC ≈ 0,50", "ok": False})

    # Endgültige Modelle auf den neuesten Daten (gleiches Verfahren wie im Walk-Forward)
    status(f"Training Stufe {level}", 0.92, "Trainiere die aktuellen Modelle …")
    last_year = int(data["date"].max().year)
    fit, cal, _ = ev.windows(data, last_year + 1, int(cfg["training"]["lernjahre"]))
    reg = Registry()
    versions = {}
    for k in ("svm", "tree", "base"):
        m = ev.train_one(k, data, fit, cal, cfg, level)
        m.meta.update({"trainingsperiode": [str(data.loc[fit, "date"].min().date()), str(data.loc[cal, "date"].max().date())],
                       "features": FEATURES, "stufe": level})
        adm = admissions.get(k, {"zugelassen": False, "status": "Messlatte"})
        score = wf["gesamt"][k]["brier_skill"] or -1
        metrics = {"prognose": {x: wf["gesamt"][k].get(x) for x in ("auc", "brier", "brier_skill", "log_loss", "ece", "trefferquote")},
                   "handel": {x: trading[k].get(x) for x in ("cagr", "max_rueckgang", "trades")} if k in trading else None,
                   "bewertung": score}
        v = reg.save(k, m, level=level, data_from=m.meta["trainingsperiode"][0], data_to=m.meta["trainingsperiode"][1],
                     n_rows=int(fit.sum() + cal.sum()), params=m.params, metrics=metrics, admitted=adm["zugelassen"],
                     reason="; ".join(p["pruefung"] for p in adm.get("pruefungen", []) if not p["ok"]) or adm["status"])
        promoted, why = reg.promote_if_better(k, v, score, adm["zugelassen"]) if k != "base" else (False, "Messlatte")
        versions[k] = {"version": v, "champion_neu": promoted, "entscheidung": why}
    reg.log_run(f"train stufe {level}", started, time.time() - t0, True, json.dumps({k: v["entscheidung"] for k, v in versions.items()}))

    report = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="minutes"), "stufe": level,
        "dauer_sekunden": round(time.time() - t0), "zeilen": int(len(data)), "werte": int(data["sym"].nunique()),
        "pruefjahre": [years[0], years[-1]], "ziel": cfg["ziel"], "features": [FEATURE_NAMES_DE[f] for f in FEATURES],
        "branchen_bekannt": len(ind), "nachrichten": hist is not None,
        "prognose": {k: {**wf["gesamt"][k], "je_jahr": wf["je_jahr"][k]} for k in ("svm", "tree", "base")},
        "einstellungen": wf["einstellungen"],
        "handel": {k: {kk: vv for kk, vv in trading[k].items() if kk != "chained"} for k in trading},
        "verlauf": {k: st._weekly(panel, years, trading[k]["chained"] / cap) for k in trading},
        "vergleich": {"msci_world": msci, "zufall": random_res,
                      "technisch_beste": {x: tech_best[x] for x in ("name", "cagr", "max_rueckgang", "trades", "trefferquote", "sharpe", "sortino", "profit_factor", "gebuehren")},
                      "technisch_alle": [{x: r[x] for x in ("name", "cagr", "max_rueckgang", "trades")} for r in tech]},
        "zulassung": admissions, "leckpruefung": leak, "versionen": versions,
        "grenzen": LIMITS,
    }
    finish_report(report)
    status("bereit", 1.0, f"Training Stufe {level} fertig nach {report['dauer_sekunden']} s")
    return report


LIMITS = [
    "Nur Werte, die es heute gibt (Survivorship Bias): verschwundene Firmen fehlen, das schönt die Ergebnisse eher.",
    "Branchen-Zuordnung von heute wird auch für die Vergangenheit verwendet (Firmen können früher anders eingeordnet gewesen sein).",
    "Nachrichten-Signal: weltweite Tageslage aus dem GDELT-Archiv ab April 2013, um einen Tag verzögert; keine Meldungen zu einzelnen Firmen.",
    "Quartalszahlen fließen (noch) nicht als Eingangsgröße ein: die kostenlose Quelle reicht nicht verlässlich weit zurück.",
    "Tageskerzen von Yahoo (Dividenden eingerechnet), Ausführung zur Eröffnung des Folgetags mit geschätztem Spread und Schlupf.",
    "Das Kalibrierjahr dient zugleich der Auswahl der Einstellung; berichtet wird nur das danach folgende, unberührte Prüfjahr.",
]


def finish_report(report: dict[str, Any]) -> None:
    from .registry import Registry

    reg = Registry()
    report["modelle"] = {
        "versionen": reg.versions(30),
        "champion": {k: reg.pointer(f"champion:{k}") for k in ("svm", "tree")},
        "aktuell": {k: reg.pointer(f"aktuell:{k}") for k in ("svm", "tree", "base")},
        "laeufe": reg.runs(20),
    }
    write_output("ki.json", report)
    if not os.environ.get("TA_QUOTES_FILE"):
        try:
            (ROOT / "docs" / "KI.md").write_text(markdown(report), encoding="utf-8")
        except OSError:
            pass


def _p(x: Any, sign: bool = True) -> str:
    if x is None:
        return "–"
    return (f"{x * 100:+.1f} %" if sign else f"{x * 100:.1f} %").replace(".", ",")


def _eur(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def _f(x: Any, nd: int = 3) -> str:
    return "–" if x is None else f"{x:.{nd}f}".replace(".", ",")


def markdown(r: dict[str, Any]) -> str:
    y0, y1 = r["pruefjahre"]
    L = ["# Phase 6 – KI-Analyse: Prüfbericht", "",
         f"Stand {r['erstellt'][:10]}, Ausbaustufe {r['stufe']}, {r['werte']} Werte, {r['zeilen']:,} Datenzeilen, Prüfjahre {y0}–{y1}.".replace(",", "."),
         f"Ziel: Wahrscheinlichkeit, dass ein Kauf zur Eröffnung des nächsten Handelstags nach {r['ziel']['horizont_tage']} Handelstagen "
         f"mindestens {r['ziel']['mindestbewegung_prozent']:g} % im Plus liegt.", "",
         "## Prognosequalität (nur Prüfjahre)", "",
         "| Modell | AUC | Brier | Brier Skill | Log Loss | ECE | Trefferquote | Precision | Recall | F1 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k in ("svm", "tree", "base"):
        m = r["prognose"][k]
        L.append(f"| {MODEL_NAMES[k]} | {_f(m['auc'])} | {_f(m['brier'])} | {_f(m['brier_skill'])} | {_f(m['log_loss'])} | {_f(m['ece'])} | "
                 f"{_p(m['trefferquote'], False)} | {_p(m['precision'], False)} | {_p(m['recall'], False)} | {_p(m['f1'], False)} |")
    L += ["", "Brier Skill > 0 heißt: besser als die Grundrate (immer dieselbe Wahrscheinlichkeit). ECE = mittlere Abweichung zwischen Prognose und Eintritt.", "",
          "## Handel mit den Regeln und Kosten aus Phase 4", "",
          "| Strategie | pro Jahr | größter Rückgang | Sharpe | Sortino | Profit Factor | Trades | Treffer | Ø Haltedauer | Gebühren |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, t in r["handel"].items():
        L.append(f"| {MODEL_NAMES[k]} | {_p(t['cagr'])} | {_p(t['max_rueckgang'])} | {_f(t['sharpe'], 2)} | {_f(t['sortino'], 2)} | {_f(t.get('profit_factor'), 2)} | "
                 f"{t['trades']} | {_p(t['trefferquote'], False)} | {_f(t['haltedauer_tage'], 1)} Tage | {_eur(t['gebuehren'])} |")
    v = r["vergleich"]
    tb = v["technisch_beste"]
    L.append(f"| Beste Technik (Phase 5): {tb['name']} | {_p(tb['cagr'])} | {_p(tb['max_rueckgang'])} | {_f(tb['sharpe'], 2)} | {_f(tb['sortino'], 2)} | {_f(tb.get('profit_factor'), 2)} | {tb['trades']} | {_p(tb['trefferquote'], False)} | – | {_eur(tb['gebuehren'])} |")
    if v["msci_world"]:
        mw = v["msci_world"]
        L.append(f"| MSCI World halten | {_p(mw['cagr'])} | {_p(mw['max_rueckgang'])} | {_f(mw['sharpe'], 2)} | {_f(mw['sortino'], 2)} | – | 1 je Jahr | – | – | – |")
    L.append(f"| Zufällige Käufe (Median, beste 10 % ab) | {_p(v['zufall']['cagr_median'])} ({_p(v['zufall']['cagr_p90'])}) | – | – | – | – | – | – | – | – |")
    L += ["", "## Zulassung für Phase 7", ""]
    for k, a in r["zulassung"].items():
        L += [f"**{MODEL_NAMES[k]}: {a['status']}**", ""]
        for p in a["pruefungen"]:
            val = p["wert"]
            L.append(f"- {'✓' if p['ok'] else '✗'} {p['pruefung']}: {_f(val) if isinstance(val, float) else val} (Grenze {p['grenze']})")
        L.append("")
    lk = r["leckpruefung"]
    L += ["## Gegenprobe auf Datenlecks", "", lk["beschreibung"], ""] + [f"- {x['jahr']}: AUC {_f(x['auc'])}" for x in lk["ergebnisse"]]
    L += ["", f"Ergebnis: {'unauffällig' if lk['ok'] else 'AUFFÄLLIG – Modelle gesperrt'}", "", "## Grenzen", ""] + [f"- {x}" for x in r["grenzen"]]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- Prognosen
def predict(inputs: tuple | None = None) -> dict[str, Any]:
    from .. import strategy_test as st
    from .registry import Registry

    cfg = load_config()
    status("Prognosen", 0.1, "Lade Kursdaten …")
    meta, frames, hist, ind = inputs or load_inputs()
    data = build_dataset(frames, st.BENCH, horizon=int(cfg["ziel"]["horizont_tage"]),
                         min_move=float(cfg["ziel"]["mindestbewegung_prozent"]) / 100, industry=ind, news_hist=hist)
    reg = Registry()
    models, info = {}, {}
    for k in ("svm", "tree", "base"):
        v = reg.pointer(f"champion:{k}") or reg.pointer(f"aktuell:{k}")
        if not v:
            continue
        models[k] = reg.load(v)
        row = reg.get(v)
        info[k] = {"version": v, "zugelassen": row["status"] == "zugelassen", "trainiert": row["created"],
                   "trainingsperiode": [row["data_from"], row["data_to"]]}
    if not models:
        out = {"erstellt": datetime.now(timezone.utc).isoformat(timespec="minutes"), "fehler": "Noch kein Modell trainiert.", "prognosen": []}
        write_output("ki_prognosen.json", out)
        return out
    last = data.sort_values("date").groupby("sym").tail(1)
    newest = data["date"].max()
    X = last[FEATURES].to_numpy(float)
    P = {k: m.predict(X) for k, m in models.items()}
    thr = float(cfg["handel"]["start_schwelle"])
    rows = []
    for i, (_, r) in enumerate(last.iterrows()):
        sym = r["sym"]
        age = (newest - r["date"]).days
        quality = "ausreichend" if age <= 4 else "veraltet"
        ps, pt = (P.get("svm", [None] * len(X))[i], P.get("tree", [None] * len(X))[i])
        base = P["base"][i] if "base" in P else 0.5
        # Übereinstimmung gemessen an der Grundrate (typische Wahrscheinlichkeit): beide darüber, beide darunter oder uneins
        agree = None if ps is None or pt is None else ((ps >= base) == (pt >= base))
        side = "überdurchschnittlich" if ps is not None and ps >= base else "unterdurchschnittlich"
        sig = []
        for k, p in (("svm", ps), ("tree", pt)):
            if p is None:
                continue
            lift = p - base
            sig.append({"modell": MODEL_NAMES[k], "version": info[k]["version"], "p_positiv": float(p), "p_negativ": float(1 - p),
                        "signalstaerke": "stark" if lift >= 0.10 else "mittel" if lift >= 0.05 else "schwach" if lift > 0 else "keine",
                        "zugelassen": info[k]["zugelassen"]})
        allowed = all(info[k]["zugelassen"] for k in ("svm", "tree") if k in info)
        buy = any(s["p_positiv"] >= thr for s in sig)
        rows.append({
            "symbol": sym, "id": meta.get(sym, {}).get("id", sym), "name": meta.get(sym, {}).get("name", sym),
            "kurs": float(frames[sym]["close"].iloc[-1]), "datum": str(r["date"].date()),
            "zeitraum": f"{cfg['ziel']['horizont_tage']} Handelstage ab Eröffnung des nächsten Handelstags",
            "grundrate": float(base), "signale": sig, "uebereinstimmung": f"einig: {side}" if agree else "widersprüchlich" if agree is False else "–",
            "status": ("Kaufsignal" if buy and allowed else "Kaufsignal (Modell nicht freigegeben – nur Beobachtung)" if buy else "kein Signal"),
            "datenqualitaet": quality,
        })
    rows.sort(key=lambda x: -max([s["p_positiv"] for s in x["signale"]] or [0]))
    out = {"erstellt": datetime.now(timezone.utc).isoformat(timespec="minutes"), "stand_kurse": str(newest.date()),
           "schwelle": thr, "modelle": info, "daten": [FEATURE_NAMES_DE[f] for f in FEATURES], "prognosen": rows,
           "hinweis": "Wahrscheinlichkeiten sind kalibrierte Schätzungen, keine sicheren Prognosen. SVM und Entscheidungsbaum "
                      "werden getrennt gezeigt und nicht gemittelt."}
    write_output("ki_prognosen.json", out)
    status("bereit", 1.0, f"Prognosen für {len(rows)} Werte (Kurse vom {newest.date()})")
    return out


def earnings_job(inputs: tuple | None = None) -> dict[str, Any]:
    from .earnings import calendar

    status("Quartalszahlen", 0.2, "Frage Termine ab …")
    meta, frames, _, _ = inputs or load_inputs()
    out = calendar(meta, frames)
    write_output("ki_quartale.json", out)
    status("bereit", 1.0, f"Quartalszahlen: {len(out.get('termine', []))} Termine, {len(out.get('warnungen', []))} Branchenhinweise")
    return out


# ---------------------------------------------------------------- Dauerdienst
def worker() -> None:
    """Plant die Arbeit selbst. Reihenfolge der Dringlichkeit:
    1. Prognosen nach US-Börsenschluss (werktags ab 22:45) und Quartalszahlen (morgens und abends)
    2. Neu trainieren, wenn das letzte Training ≥ 7 Tage alt ist und ≥ 5 neue Handelstage vorliegen
    3. Sonst rechnen: höhere Ausbaustufen (genauere SVM, mehr Daten, größere Baum-Suche) als Herausforderer –
       sie werden genauso geprüft und ersetzen den Champion nur, wenn sie zugelassen UND besser sind."""
    from .registry import Registry

    cfg = load_config()
    state_file = Path(os.environ.get("TA_MODEL_DIR", str(ROOT / "models"))) / "worker.json"
    state: dict[str, Any] = json.loads(state_file.read_text()) if state_file.exists() else {}

    def save() -> None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    while True:
        now = datetime.now(BERLIN)
        today = now.date().isoformat()
        job = None
        try:
            if now.weekday() < 5 and now.hour * 60 + now.minute >= 22 * 60 + 45 and state.get("prognose_tag") != today:
                job = "prognose"
                inputs = load_inputs()
                earnings_job(inputs)
                predict(inputs)
                state["prognose_tag"] = today
            elif now.hour >= 7 and state.get("quartale_tag") != today:
                job = "quartale"
                earnings_job()
                state["quartale_tag"] = today
            elif _train_due(state, cfg, now):
                job = "training"
                inputs = load_inputs()
                train(1, inputs)
                predict(inputs)
                state.update({"training": now.isoformat(), "stufen_erledigt": [1]})
            elif len(state.get("stufen_erledigt", [])) < len(LEVELS):
                level = next(lv for lv in LEVELS if lv not in state.get("stufen_erledigt", []))
                job = f"stufe {level}"
                train(level)
                state.setdefault("stufen_erledigt", []).append(level)
            else:
                status("bereit", None, "Alle Aufgaben erledigt – nächstes Training, sobald genug neue Kursdaten vorliegen", naechste_pruefung=(now + timedelta(minutes=15)).isoformat())
                save()
                time.sleep(900)
                continue
            save()
        except Exception as err:  # noqa: BLE001 – Fehler protokollieren, Dienst läuft weiter
            msg = f"{job}: {type(err).__name__}: {err}"
            print(traceback.format_exc(), flush=True)
            status("Fehler", None, msg)
            try:
                Registry().log_run(job or "?", datetime.now(timezone.utc).isoformat(timespec="seconds"), 0, False, msg)
            except Exception:  # noqa: BLE001
                pass
            state[f"fehler_{job}"] = now.isoformat()
            save()
            time.sleep(600)


def _train_due(state: dict, cfg: dict, now: datetime) -> bool:
    last = state.get("training")
    if not last:
        return True
    days = (now - datetime.fromisoformat(last)).days
    new_trading_days = int(np.busday_count(datetime.fromisoformat(last).date(), now.date()))
    return days >= int(cfg["training"]["neu_trainieren_nach_tagen"]) and new_trading_days >= int(cfg["training"]["mindestens_neue_handelstage"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tradingagent ml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--stufe", type=int, default=1, choices=LEVELS)
    sub.add_parser("predict")
    sub.add_parser("earnings")
    sub.add_parser("worker")
    sub.add_parser("all", help="train + predict + earnings (für GitHub)")
    a = ap.parse_args(argv)
    if a.cmd == "train":
        r = train(a.stufe)
        print(markdown(r))
    elif a.cmd == "predict":
        predict()
    elif a.cmd == "earnings":
        earnings_job()
    elif a.cmd == "worker":
        worker()
    else:
        inputs = load_inputs()
        r = train(1, inputs)
        predict(inputs)
        earnings_job(inputs)
        print(markdown(r))
    return 0
