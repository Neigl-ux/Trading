"""Prüft, ob Tradegate für jede ISIN einen aktuellen Kurs liefert.

Die App fragt Tradegate direkt vom Handy aus ab. Dieser Befehl kontrolliert
vorab, ob die Adresse erreichbar ist, ob Browser-Abrufe erlaubt sind (CORS)
und ob jede ISIN bekannt ist. Inoffizielle Schnittstelle: kann sich ändern.
"""

from __future__ import annotations

import json
import time

import requests

from .config import load_settings

URL = "https://www.tradegatebsx.com/refresh.php"


def main() -> int:
    settings = load_settings()
    items = [i for i in settings.instruments if i.isin]
    ok = 0
    for n, inst in enumerate(items):
        try:
            r = requests.get(URL, params={"isin": inst.isin}, timeout=10,
                             headers={"Origin": "https://example.github.io", "User-Agent": "Mozilla/5.0"})
            cors = r.headers.get("Access-Control-Allow-Origin")
            body = r.text.strip()
            if n < 2:
                print(f"--- Beispielantwort {inst.id}: HTTP {r.status_code}, CORS={cors!r}, Typ={r.headers.get('Content-Type')}")
                print(body[:600])
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            good = isinstance(data, dict) and any(str(data.get(k, "")).strip() not in ("", "-", "0") for k in ("last", "bid", "ask"))
            ok += good
            print(f"{'OK  ' if good else 'FEHLT'} {inst.id:<6} {inst.isin}  last={data.get('last') if isinstance(data, dict) else None!r}")
        except requests.RequestException as exc:
            print(f"FEHLER {inst.id:<6} {inst.isin}  {exc}")
        time.sleep(0.5)  # höflich bleiben
    print(f"\n{ok} von {len(items)} Werten liefern Kurse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
