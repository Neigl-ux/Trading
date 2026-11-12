"""Tests der Market Data Engine der App (app/engine.js)."""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_engine():
    out = subprocess.run(["node", str(HERE / "engine_check.mjs")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
