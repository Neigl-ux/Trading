"""Prüft die Umwandlung der Tradegate-Antworten in der App (JavaScript)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_tradegate_number_formats(tmp_path):
    script = tmp_path / "t.mjs"
    script.write_text(f"""
import {{ parseNumber, parseQuote, tradegateOpen }} from "{(APP / 'tradegate.js').as_uri()}";
const cases = [[212.75,212.75],["110,10",110.1],["1 371,80",1371.8],["1.371,80",1371.8],["-",null],["",null],[null,null],["45.444",45.444]];
for (const [i,e] of cases) if (parseNumber(i) !== e) throw new Error(`${{i}} -> ${{parseNumber(i)}}`);
if (parseQuote({{bid:"-", ask:"-", last:"-"}}) !== null) throw new Error("leer");
const q = parseQuote({{bid:1, ask:3, last:"-", close:"2,00"}});
if (q.last !== 2 || q.prevClose !== 2) throw new Error("mitte");
if (!tradegateOpen(new Date("2026-11-12T11:00:00Z")) || tradegateOpen(new Date("2026-11-14T11:00:00Z"))) throw new Error("zeit");
console.log("ok");
""")
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
