"""Prüft, dass die App (JavaScript) entschlüsseln kann, was Python verschlüsselt."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tradingagent.crypto_box import Box

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_browser_crypto_opens_python_export(tmp_path):
    box = Box("korrektes-pferd-batterie", iterations=10_000)
    sealed = box.seal({"hallo": "Welt", "zahlen": [1, 2.5]})
    sealed["kdf"] = box.kdf_header()
    (tmp_path / "sealed.json").write_text(json.dumps(sealed))
    script = tmp_path / "check.mjs"
    script.write_text(
        f"""
import {{ readFileSync }} from "node:fs";
import {{ deriveKey, exportKey, importKey, openSealed }} from "{(APP / 'crypto.js').as_uri()}";
const s = JSON.parse(readFileSync("{tmp_path / 'sealed.json'}", "utf8"));
const key = await deriveKey("korrektes-pferd-batterie", s.kdf);
const again = await importKey(await exportKey(key));
console.log(JSON.stringify(await openSealed(again, s)));
let wrong = "ok";
try {{ await openSealed(await deriveKey("falsches-passwort-123", s.kdf), s); wrong = "geoeffnet"; }} catch {{ wrong = "abgelehnt"; }}
console.log(wrong);
"""
    )
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert json.loads(lines[0]) == {"hallo": "Welt", "zahlen": [1, 2.5]}
    assert lines[1] == "abgelehnt"
