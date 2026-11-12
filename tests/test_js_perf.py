"""Automatische Tests der Prozentberechnung je Zeitintervall (app/perf.js)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_perf_calculation():
    out = subprocess.run(["node", str(HERE / "perf_check.mjs")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
