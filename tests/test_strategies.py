"""Phase 5: Signale ohne Blick in die Zukunft, Test-Motor mit den Phase-4-Regeln."""
import numpy as np
import pandas as pd
import pytest

from tradingagent import strategy_test as st
from tradingagent.risk import RiskConfig, RiskManager
from tradingagent.strategies import STRATEGIES, signals


def _frame(n=700, seed=1, drift=0.0004):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.006, "low": np.minimum(o, c) * 0.994, "close": c,
                         "volume": rng.uniform(1e5, 1e6, n)}, index=idx)


@pytest.mark.parametrize("strat", STRATEGIES, ids=lambda s: s.key)
def test_kein_blick_in_die_zukunft(strat):
    df = _frame()
    for p in strat.params:
        full_e, full_x = signals(df, strat.key, p)
        for cut in (300, 450, 699):
            e, x = signals(df.iloc[:cut], strat.key, p)
            assert (e == full_e[:cut]).all() and (x == full_x[:cut]).all(), (strat.key, p, cut)


def test_strategien_liefern_signale():
    df = _frame(seed=4)
    for strat in STRATEGIES:
        e, x = signals(df, strat.key, strat.params[0])
        assert e.sum() > 0 and x.sum() > 0, strat.key


def _panel(frames):
    meta = {k: {"kind": "aktie", "name": k} for k in frames}
    return st.build_panel(frames, meta)


def test_kauf_erst_am_folgetag_zur_eroeffnung_mit_kosten():
    df = _frame(400, seed=2, drift=0.002)
    panel = _panel({"A.DE": df})
    en, ex = np.zeros((400, 1), bool), np.zeros((400, 1), bool)
    en[100, 0] = True
    rm = RiskManager(RiskConfig.load())
    r = st.simulate(panel, en, ex, 0, 400, rm=rm)
    buys = [e for e in r["log"] if e["side"] == "buy" and e["ok"]]
    assert len(buys) == 1 and buys[0]["t"] == int(st._when(panel.dates[101]).timestamp())
    assert buys[0]["price"] > df["open"].iloc[101]  # Spread und Schlupf
    assert np.isnan(r["equity"]).sum() == 0 and r["equity"][99] == 5000  # vorher nichts passiert


def test_stop_beendet_verlust_und_regeln_greifen():
    df = _frame(300, seed=5, drift=-0.004)  # fallender Markt
    panel = _panel({"A.DE": df})
    en = np.zeros((300, 1), bool)
    en[50, 0] = True
    rm = RiskManager(RiskConfig.load())
    r = st.simulate(panel, en, np.zeros((300, 1), bool), 0, 300, rm=rm)
    sells = [e for e in r["log"] if e["side"] == "sell" and e["ok"]]
    assert sells and "Stop" in sells[0]["reason"]
    assert r["trades"][0] < 0 and r["trades"][0] > -5000 * 0.01 - 40  # Verlust ≈ 1 % plus Kosten


def test_gesamtlauf_mit_vergleichen(capsys):
    frames = {("EUNL.DE" if k == 0 else f"S{k}.DE"): _frame(1200, seed=10 + k) for k in range(6)}
    meta = {k: {"kind": "etf" if k == "EUNL.DE" else "aktie", "name": k} for k in frames}
    old = st.FIRST_YEAR
    st.FIRST_YEAR = 2020
    try:
        r = st._jsonable(st.run(frames, meta, progress=lambda m: None))
    finally:
        st.FIRST_YEAR = old
    assert r["pruefjahre"] == [2023, 2024] and len(r["strategien"]) == len(STRATEGIES)
    s = r["strategien"][0]
    assert {"cagr", "max_rueckgang", "jahre", "verlauf", "besser_als_zufall_prozent"} <= set(s)
    assert r["msci_world"]["jahre"][0]["jahr"] == 2023
    md = st.report_markdown(r)
    assert "MSCI World" in md and "Grenzen des Tests" in md


def test_mindesthaltedauer_ignoriert_fruehe_verkaufssignale():
    df = _frame(300, seed=2, drift=0.003)  # steigt: kein Stop
    panel = _panel({"A.DE": df})
    en, ex = np.zeros((300, 1), bool), np.zeros((300, 1), bool)
    en[100, 0] = True
    ex[105, 0] = ex[125, 0] = True
    rm = RiskManager(RiskConfig.load())
    sells = lambda r: [e for e in r["log"] if e["side"] == "sell" and e["ok"]]  # noqa: E731
    early = sells(st.simulate(panel, en, ex, 0, 300, rm=rm))
    late = sells(st.simulate(panel, en, ex, 0, 300, rm=rm, min_hold=20))
    assert early[0]["t"] == int(st._when(panel.dates[106]).timestamp())
    assert late[0]["t"] == int(st._when(panel.dates[126]).timestamp())
