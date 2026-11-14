"""Phase 6: Features ohne Blick in die Zukunft, Purging, Kalibrierung, Lern-Nachweis, Versionen, Zulassung, Warnungen."""
import json

import numpy as np
import pandas as pd
import pytest

from tradingagent.ml import evaluate as ev
from tradingagent.ml import load_config
from tradingagent.ml.earnings import calendar, industry_warnings
from tradingagent.ml.features import FEATURES, build_dataset, instrument_features, target
from tradingagent.ml.models import fit_calibrated
from tradingagent.ml.registry import Registry


def _frame(n, seed, drift=0.0003, start="2012-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.006, "low": np.minimum(o, c) * 0.994, "close": c,
                         "volume": rng.uniform(1e5, 1e6, n)}, index=idx)


def test_features_nutzen_nur_vergangenheit():
    df = _frame(900, 1)
    full = instrument_features(df)
    for cut in (400, 650, 899):
        part = instrument_features(df.iloc[:cut])
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_exact=False, rtol=1e-9)


def test_zielgroesse_kauf_naechste_eroeffnung_bewertung_nach_h_tagen():
    df = _frame(30, 2)
    t = target(df, 5, 0.02)
    expect = df["close"].iloc[8] / df["open"].iloc[4] - 1
    assert t["fwd"].iloc[3] == pytest.approx(expect)
    assert t["label_end"].iloc[3] == df.index[8]
    assert t["y"].iloc[3] == float(expect >= 0.02)
    assert t["y"].iloc[-1] != t["y"].iloc[-1]  # am Ende unbekannt (NaN)


def test_purging_keine_ueberschneidung():
    frames = {f"S{k}": _frame(2200, k) for k in range(3)}
    data = build_dataset(frames, "S0", horizon=5, min_move=0.02)
    fit, cal, test = ev.windows(data, 2018, 3)
    assert data.loc[fit, "label_end"].max() < pd.Timestamp("2017-01-01")
    assert data.loc[cal, "label_end"].max() < pd.Timestamp("2018-01-01")
    assert data.loc[test, "date"].min() >= pd.Timestamp("2018-01-01")
    assert data.loc[fit, "date"].min() >= pd.Timestamp("2014-01-01")


def _learnable(n_days=2400, n_sym=6, seed=3):
    """Synthetischer Markt mit echtem, schwachem Zusammenhang: nach starkem Tagesverlust (ret1) steigt der Kurs öfter."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2011-01-03", periods=n_days)
    frames = {}
    for k in range(n_sym):
        r = rng.normal(0.0002, 0.015, n_days)
        for t in range(1, n_days - 1):
            if r[t] < -0.02:
                r[t + 1] += 0.012
        c = 100 * np.exp(np.cumsum(r))
        o = c * np.exp(rng.normal(0, 0.002, n_days))
        o[1:] = c[:-1] * np.exp(r[1:] * 0.5)
        frames[f"S{k}"] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996,
                                        "close": c, "volume": rng.uniform(1e5, 1e6, n_days)}, index=idx)
    return frames


def test_modelle_lernen_echten_zusammenhang_und_kalibrieren():
    cfg = load_config()
    data = build_dataset(_learnable(), "S0", horizon=1, min_move=0.005)
    wf = ev.walk_forward(data, cfg, kinds=("tree", "base"), progress=lambda m: None)
    t = wf["gesamt"]["tree"]
    assert t["auc"] > 0.55 and t["brier_skill"] > 0 and t["ece"] < 0.06
    assert wf["gesamt"]["base"]["auc"] == pytest.approx(0.5, abs=0.02) or wf["gesamt"]["base"]["auc"] is None


def test_gegenprobe_mit_vertauschter_zielgroesse_findet_nichts():
    cfg = load_config()
    data = build_dataset(_learnable(), "S0", horizon=1, min_move=0.005)
    years = sorted(data["date"].dt.year.unique())[5:]
    res = ev.leak_check(data, cfg, years)
    assert all(abs(x["auc"] - 0.5) < 0.05 for x in res["ergebnisse"])


def test_kalibrierung_auf_eigenem_zeitraum():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(4000, 3))
    y = (X[:, 0] + rng.normal(size=4000) > 0).astype(float)
    m = fit_calibrated("tree", {"max_depth": 3, "min_samples_leaf": 100}, X[:3000], y[:3000], X[3000:], y[3000:], 0)
    p = m.predict(X)
    assert 0 < p.min() and p.max() < 1
    _, ece = ev.reliability(p[3000:], y[3000:])
    assert ece < 0.08


def test_kennzahlen():
    y = np.array([1, 0, 1, 0, 1, 1, 0, 0], float)
    p = np.array([.9, .1, .8, .3, .6, .4, .2, .7])
    m = ev.forecast_metrics(p, y, base_rate=0.5)
    assert m["trefferquote"] == 0.75 and m["precision"] == 0.75 and m["recall"] == 0.75
    assert m["brier"] == pytest.approx(np.mean((p - y) ** 2)) and m["brier_skill"] > 0


def test_versionen_champion_nur_bei_zulassung_und_besser(tmp_path):
    reg = Registry(tmp_path)
    kw = dict(level=1, data_from="2020-01-01", data_to="2024-12-31", n_rows=1000, params={})
    v1 = reg.save("tree", {"m": 1}, metrics={"bewertung": 0.01}, admitted=False, reason="AUC", **kw)
    assert reg.promote_if_better("tree", v1, 0.01, False)[0] is False and reg.pointer("champion:tree") is None
    v2 = reg.save("tree", {"m": 2}, metrics={"bewertung": 0.02}, admitted=True, reason="ok", **kw)
    assert reg.promote_if_better("tree", v2, 0.02, True)[0] and reg.pointer("champion:tree") == v2
    v3 = reg.save("tree", {"m": 3}, metrics={"bewertung": 0.01}, admitted=True, reason="ok", **kw)
    assert reg.promote_if_better("tree", v3, 0.01, True)[0] is False and reg.pointer("champion:tree") == v2
    assert reg.load(v1) == {"m": 1} and reg.pointer("aktuell:tree") == v3
    with pytest.raises(ValueError):
        reg.restore("tree", v1)  # nicht zugelassen
    reg.restore("tree", v3)
    assert reg.pointer("champion:tree") == v3


def test_zulassung_streng():
    cfg = load_config()
    good_f = {"auc": 0.6, "brier_skill": 0.02, "anteil_jahre_brier_skill_positiv": 0.8, "ece": 0.02}
    good_t = {"trades": 300, "cagr": 0.15, "max_rueckgang": -0.2, "anteil_positive_jahre": 0.7}
    a = ev.admission("svm", good_f, good_t, 0.11, 0.0, cfg)
    assert a["zugelassen"] and a["status"] == "Für Phase 7 zugelassen"
    b = ev.admission("svm", good_f, {**good_t, "cagr": 0.05}, 0.11, 0.0, cfg)
    assert not b["zugelassen"] and b["status"] == "Nicht freigegeben"
    c = ev.admission("svm", {**good_f, "ece": 0.09}, good_t, 0.11, 0.0, cfg)
    assert not c["zugelassen"]


class _FakeSource:
    name = "Test"

    def __init__(self, prof, dates):
        self.prof, self._dates = prof, dates

    def profile(self, s):
        return self.prof[s]

    def dates(self, s):
        return self._dates.get(s, pd.DataFrame())


def test_branchenwarnung_nur_bei_gleicher_branche(tmp_path, monkeypatch):
    monkeypatch.setenv("TA_MODEL_DIR", str(tmp_path))
    now = pd.Timestamp("2026-11-12T12:00Z")
    prof = {"MSFT": {"industry": "Software", "market_cap": 3e12, "name": "Microsoft"},
            "SAP.DE": {"industry": "Software", "market_cap": 3e11, "name": "SAP"},
            "BAS.DE": {"industry": "Chemicals", "market_cap": 4e10, "name": "BASF"},
            "XYZ": {"industry": None, "market_cap": None}}
    when = pd.Timestamp("2026-11-11T21:05Z")
    dates = {"MSFT": pd.DataFrame({"eps_est": [3.1], "eps": [3.3], "surprise": [6.4]}, index=pd.DatetimeIndex([when])),
             "XYZ": pd.DataFrame({"eps_est": [1.0], "eps": [1.1], "surprise": [10.0]}, index=pd.DatetimeIndex([when]))}
    meta = {s: {"kind": "aktie", "name": prof[s].get("name") or s, "id": s} for s in prof}
    res = calendar(meta, None, source=_FakeSource(prof, dates), now=now.to_pydatetime())
    assert len(res["veroeffentlicht"]) == 2 and res["ohne_branche"] == 1
    w = res["warnungen"]
    assert len(w) == 1 and w[0]["ausloeser"] == "Microsoft" and [b["symbol"] for b in w[0]["betroffen"]] == ["SAP.DE"]
    assert "BASF" not in w[0]["text"]


def test_gesamtlauf_training_prognose(tmp_path, monkeypatch):
    from tradingagent.ml import service

    site = tmp_path / "site" / "data"
    site.mkdir(parents=True)
    monkeypatch.setenv("TA_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("TA_QUOTES_FILE", str(tmp_path / "q.json"))
    monkeypatch.setenv("TA_EXPORT_DIR", str(site))
    frames = _learnable(n_days=2300, n_sym=6)
    frames["EUNL.DE"] = frames.pop("S0")
    meta = {s: {"kind": "etf" if s == "EUNL.DE" else "aktie", "name": s, "id": s, "usd": False} for s in frames}
    inputs = (meta, frames, None, {})
    rep = service.train(1, inputs)
    assert set(rep["zulassung"]) == {"svm", "tree"} and rep["leckpruefung"]["ergebnisse"]
    assert rep["modelle"]["aktuell"]["svm"] and rep["handel"]["tree"]["trades"] >= 0
    pr = service.predict(inputs)
    assert len(pr["prognosen"]) == len(frames)
    row = pr["prognosen"][0]
    assert {s["modell"] for s in row["signale"]} == {"SVM", "Entscheidungsbaum"} and row["datenqualitaet"]
    for name in ("ki.json", "ki_prognosen.json", "ki_status.json"):
        assert json.loads((tmp_path / "site" / name).read_text())
    assert "Zulassung" in service.markdown(rep)
