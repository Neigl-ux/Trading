"""Neue App-Version eintragen: python scripts/bump_version.py "Kurze Beschreibung der Änderung"
Setzt die Nummer in app/version.json, app/app.js (APP_VERSION), app/sw.js (Cache-Name) und app/index.html (?v=)."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

app = Path(__file__).resolve().parent.parent / "app"
vf = app / "version.json"
old = json.loads(vf.read_text(encoding="utf-8"))["v"] if vf.exists() else 31
new = old + 1
note = " ".join(sys.argv[1:]).strip() or "Verbesserungen"
stamp = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")
vf.write_text(json.dumps({"v": new, "date": stamp, "note": note}, ensure_ascii=False) + "\n", encoding="utf-8")
for name, pat, rep in (("app.js", r"const APP_VERSION = \d+;", f"const APP_VERSION = {new};"),
                       ("sw.js", r"ta-shell-v\d+", f"ta-shell-v{new}"),
                       ("index.html", r"\?v=\d+", f"?v={new}")):
    p = app / name
    s, n = re.subn(pat, rep, p.read_text(encoding="utf-8"))
    assert n, f"{name}: Muster nicht gefunden"
    p.write_text(s, encoding="utf-8")
print(f"Version {new} ({stamp}): {note}")
