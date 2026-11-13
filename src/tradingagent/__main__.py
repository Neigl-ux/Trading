"""Kommandozeile.

  python -m tradingagent collect     Kurse sammeln und für die App exportieren
  python -m tradingagent export      nur exportieren (ohne neue Kurse)
  python -m tradingagent status      Übersicht über gespeicherte Daten
  python -m tradingagent heartbeat   Monats-Lebenszeichen schreiben (GitHub-Abschaltschutz)
"""

from __future__ import annotations

import argparse
import os
import time
import json
import sys
from pathlib import Path

import pandas as pd

from .collector import collect
from .config import ROOT, load_settings
from .data.providers import build_providers
from .export import export_all
from .quotes import fetch_quotes
from .storage import open_storage


def _passphrase() -> str | None:
    value = os.environ.get("APP_PASSPHRASE")
    return value.strip() if value else None


def _collect_state(settings, **changes) -> None:
    """Server: Zeitpunkte des Sammel-Laufs für die App (site/collect.json, nur Uhrzeiten, keine Kurse)."""
    if not os.environ.get("TA_QUOTES_FILE"):
        return
    path = settings.export_dir.parent / "collect.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        state = {}
    state.update(changes)
    state["pause"] = int(os.environ.get("TA_COLLECT_PAUSE", "300"))
    if state.get("finished") and not state.get("running"):
        state["next"] = int(state["finished"]) + state["pause"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def cmd_collect(args) -> int:
    settings = load_settings()
    t0 = time.time()
    _collect_state(settings, running=True, started=int(t0))
    try:
        return _collect(settings, args)
    finally:
        t1 = time.time()
        _collect_state(settings, running=False, finished=int(t1), duration=int(t1 - t0))


def _collect(settings, args) -> int:
    storage = open_storage(settings.storage_path)
    names = sorted({p for m in settings.markets.values() if m.enabled for p in (*m.providers, *m.daily_providers)})
    t_collect = time.time()
    summary = collect(settings, storage, build_providers(names))
    t_collect = time.time() - t_collect
    s = summary.as_dict()
    print(f"Lauf {s['started']}: {s['fetches']} Abrufe, {s['failed']} fehlgeschlagen, {s['new_bars']} neue Kerzen")
    for n in s.get("notes", []):
        print(f"  {n}")
    for w in s["warnings"]:
        print(f"  Hinweis {w['instrument']} {w['interval']}: {w['detail']}")
    for e in s["errors"]:
        print(f"  FEHLER {e['instrument']} {e['interval']}: {'; '.join(e['errors'])}")
    if not args.no_export:
        quotes = _server_quotes()
        if quotes is not None:
            q_errors = []
            print(f"Tradegate: {len(quotes)} Kurse vom Live-Dienst des Servers")
        else:
            quotes, q_errors = fetch_quotes(settings)
            print(f"Tradegate: {len(quotes)} aktuelle Kurse" + (f", {len(q_errors)} Fehler" if q_errors else ""))
        t_export = time.time()
        export_all(settings, storage, summary.started, status=s, passphrase=_passphrase(), quotes=quotes)
        print(f"Export nach {settings.export_dir}")
        print(f"Dauer: Sammeln {t_collect:.0f} s, Export {time.time() - t_export:.0f} s")
    storage.close()
    return 1 if summary.all_failed else 0


def _server_quotes():
    """Auf dem Server fragt der Live-Dienst Tradegate ohnehin laufend ab (TA_QUOTES_FILE).
    Ist die Datei frisch (unter 10 Minuten), nimmt der Sammler diese Kurse statt 800 eigener Abfragen."""
    import json as _json
    import os as _os
    import time as _time

    from .quotes import Quote

    path = _os.environ.get("TA_QUOTES_FILE")
    if not path:
        return None
    try:
        if _time.time() - _os.path.getmtime(path) > 600:
            return None
        raw = _json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return None
    return {k: Quote(float(v["last"]), pd.Timestamp(v["time"], unit="s", tz="UTC")) for k, v in raw.items()}


def cmd_export(args) -> int:
    settings = load_settings()
    from . import republish

    if republish.only_app_changed() and _passphrase():
        n = republish.fetch_published(settings.export_dir, _passphrase())
        if n:
            print(f"Nur die App wurde geändert: {n} veröffentlichte Daten-Dateien übernommen, kein neuer Export nötig.")
            return 0
        print("Veröffentlichte Daten nicht vollständig abrufbar – normaler Export.")
    storage = open_storage(settings.storage_path)
    runs = storage.recent_runs(1)
    export_all(settings, storage, pd.Timestamp.now(tz="UTC"), status=runs[0] if runs else {}, passphrase=_passphrase())
    print(f"Export nach {settings.export_dir}")
    return 0


def cmd_status(args) -> int:
    settings = load_settings()
    storage = open_storage(settings.storage_path)
    rows = storage.counts()
    if not rows:
        print("Noch keine Daten gespeichert.")
        return 0
    print(f"{'Instrument':<10} {'Int.':<5} {'Kerzen':>7}  von               bis")
    for r in rows:
        print(f"{r['instrument']:<10} {r['interval']:<5} {r['bars']:>7}  {r['first']:%Y-%m-%d %H:%M}  {r['last']:%Y-%m-%d %H:%M}")
    return 0


def cmd_heartbeat(args) -> int:
    """GitHub schaltet Zeitpläne öffentlicher Projekte nach 60 Tagen ohne
    Aktivität ab. Diese Datei ändert sich einmal im Monat und wird committet."""
    path = ROOT / "status" / "heartbeat.txt"
    path.parent.mkdir(exist_ok=True)
    month = pd.Timestamp.now(tz="UTC").strftime("%Y-%m")
    old = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if old != month:
        path.write_text(month + "\n", encoding="utf-8")
        print(f"Heartbeat aktualisiert: {month}")
    else:
        print("Heartbeat aktuell")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingagent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect", help="Kurse sammeln und exportieren")
    p.add_argument("--no-export", action="store_true")
    p.set_defaults(func=cmd_collect)
    sub.add_parser("export").set_defaults(func=cmd_export)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("heartbeat").set_defaults(func=cmd_heartbeat)
    sub.add_parser("notify-test", help="Testnachricht aufs Handy (ntfy)").set_defaults(
        func=lambda args: __import__("tradingagent.notify", fromlist=["main"]).main())
    sub.add_parser("universe", help="Verzeichnis config/universe.yaml aus öffentlichen Listen erzeugen").set_defaults(
        func=lambda args: __import__("tradingagent.universe", fromlist=["main"]).main())
    sub.add_parser("advisor-push", help="Ratgeber-Nachrichten aufs Handy").set_defaults(
        func=lambda args: __import__("tradingagent.push", fromlist=["main"]).main())
    sub.add_parser("backtest", help="Rückblick-Test des Ratgebers").set_defaults(
        func=lambda args: __import__("tradingagent.backtest", fromlist=["main"]).main())
    sub.add_parser("strategien", help="Phase 5: Strategien im Rückblick testen").set_defaults(
        func=lambda args: __import__("tradingagent.strategy_test", fromlist=["main"]).main())
    sub.add_parser("verify", help="Asset-Datenbank gegen die Quellen prüfen").set_defaults(
        func=lambda args: __import__("tradingagent.verify", fromlist=["main"]).main())
    sub.add_parser("tradegate-check", help="Tradegate-Echtzeitkurse prüfen").set_defaults(
        func=lambda args: __import__("tradingagent.tradegate_check", fromlist=["main"]).main())
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
