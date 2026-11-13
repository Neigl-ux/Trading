import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_assistant_answers():
    r = subprocess.run(["node", str(ROOT / "tests" / "assistant_check.mjs")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr + r.stdout
