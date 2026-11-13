"""App-Positionsrechner (app/risk.js) rechnet wie der Server-Baustein und nutzt dieselben Werte."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tradingagent.risk import RiskConfig, RiskManager

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {"price": 240, "stop": 230, "kind": "aktie"},
    {"price": 100, "stop": 99, "kind": "etf"},
    {"price": 100, "stop": 80, "kind": "aktie"},
    {"price": 80000, "stop": 79200, "kind": "krypto"},
    {"price": 3.5, "stop": 3.2, "kind": "aktie"},
    {"price": 1500, "stop": 1420, "kind": "aktie"},
    {"price": 50, "stop": 55, "kind": "aktie"},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_app_rechnet_wie_server():
    cases = [{**c, "equity": 5000} for c in CASES]
    r = subprocess.run(["node", str(ROOT / "tests" / "risk_check.mjs"), json.dumps(cases)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    js = json.loads(r.stdout)
    cfg = RiskConfig.load()
    R = js["RISK"]
    assert (R["startCapital"], R["feeStock"], R["feeCryptoPct"], R["spreadPct"], R["spreadCryptoPct"], R["slipPct"],
            R["riskPct"], R["stopAtr"], R["maxPosPct"], R["maxPosCryptoPct"], R["maxFeePct"]) == (
        cfg.start_capital, cfg.fee_stock, cfg.fee_crypto_pct, cfg.spread_pct, cfg.spread_crypto_pct, cfg.slip_pct,
        cfg.risk_pct, cfg.stop_atr, cfg.max_pos_pct, cfg.max_pos_crypto_pct, cfg.max_fee_pct)
    rm = RiskManager(cfg)
    for c, j in zip(CASES, js["out"]):
        sz = rm.size(rm.new_account("t", "ueber_nacht"), c["price"], c["stop"], c["kind"], {})
        assert j["qty"] == pytest.approx(sz.qty, abs=1e-6), c
        assert j["fill"] == pytest.approx(sz.price), c
        assert j["reject"] == bool(sz.reject), c
        if not j["reject"]:
            assert j["fee"] == pytest.approx(sz.fee), c
