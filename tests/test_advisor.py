"""Ratgeber: Punktzahl, Rückblick-Test, Sparplan, Nachkauf-Stufen, Nachrichten."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tradingagent import advisor, backtest, news, scoring
from tradingagent.storage import SQLiteStorage


def walk(n, mu, sig, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(mu, sig, n)))


def test_score_needs_a_year():
    assert scoring.score(walk(200, 0, 0.01)) is None
    assert scoring.score(walk(300, 0, 0.01)) is not None


def test_uptrend_calm_beats_downtrend_volatile():
    up = scoring.score(walk(800, 0.0015, 0.004), kind="etf")
    down = scoring.score(walk(800, -0.001, 0.03), kind="aktie")
    assert up["score"] > 65 > 40 > down["score"]
    assert up["grade"] == "interessant" and down["grade"] == "vorsicht"
    assert up["risk"] < down["risk"]
    assert "Aufwärtstrend" in up["why"][0] and "Abwärtstrend" in down["why"][0]


def test_score_independent_recomputation():
    c = walk(800, 0.0005, 0.01)
    f = scoring.factors(c)
    # unabhängig nachgerechnet
    assert abs(f["r12_1"] - (c[-22] / c[-253] - 1)) < 1e-12
    assert abs(f["vol"] - np.std(np.diff(np.log(c[-253:])), ddof=1) * np.sqrt(252)) < 1e-12
    peak = np.maximum.accumulate(c[-756:])
    assert abs(f["mdd3"] - np.max(1 - c[-756:] / peak)) < 1e-12
    assert abs(f["dd12"] - (1 - c[-1] / c[-252:].max())) < 1e-12
    # fehlende Faktoren verteilen ihr Gewicht
    f2 = dict(f, trade=None)
    w = scoring.WEIGHTS
    expect = (w["trend"] * f["trend"] + w["risk"] * f["risk"] + w["fit"] * f["fit"]) / (w["trend"] + w["risk"] + w["fit"])
    assert abs(scoring.total(f2) - expect) < 1e-9


def test_leveraged_never_fits():
    assert scoring.is_leveraged("ProShares UltraPro Short QQQ")
    assert not scoring.is_leveraged("iShares Core MSCI World")
    f = scoring.factors(walk(400, 0, 0.01), name="Daily 3x Long")
    assert f["fit"] == 0


def test_pyramid_simulation_by_hand():
    # 300 Tage bei 100, dann −10 %, −20 %, −30 %, dann Erholung auf 100
    c = np.array([100.0] * 300 + [90.0] * 5 + [80.0] * 5 + [70.0] * 5 + [100.0] * 5)
    r = backtest.simulate_pyramid(c, 260, len(c) - 1)
    # Stufen: 10 % bei 90, 15 %+20 % bei 80, 25 %+30 % bei 70 → alles angelegt
    units = 0.10 / 90 + 0.35 / 80 + 0.55 / 70
    assert abs(r["value"] - units * 100) < 1e-12 and r["cash_left"] < 1e-12
    # nur erlaubt, wenn die Punktzahl reicht: nichts erlaubt → Bargeld bleibt
    r2 = backtest.simulate_pyramid(c, 260, len(c) - 1, allowed=np.zeros(len(c), bool))
    assert r2["value"] == 1.0 and r2["first_buy_days"] == -1


def test_backtest_runs_on_synthetic_data():
    rng = np.random.default_rng(3)

    def loader(symbols):
        out = {}
        for k, s in enumerate(symbols[:12] + [x for _, x, _ in backtest.PYRAMID_SERIES]):
            idx = pd.bdate_range("2005-01-03", periods=5000, tz="UTC")
            out[s] = pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 5000))),
                                   "volume": 1e6}, index=idx)
        return out

    r = backtest.run(loader)
    md = backtest.report_markdown(r)
    assert "Nachkauf-Stufen" in md and "Grenzen" in md
    row = r["pyramid"][0]
    assert 0 <= row["5J"]["staffel"]["win_share"] <= 1


class _Inst(SimpleNamespace):
    pass


class _Loader:
    def __init__(self, df):
        self.df = df

    def load(self, interval, start=None):
        return self.df if start is None else self.df[self.df.index >= start]


def _items(now):
    idx = pd.bdate_range(end=now.normalize() - pd.Timedelta(days=1), periods=900, tz="UTC")
    def frame(c):
        return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e6}, index=idx)
    base = walk(900, 0.0006, 0.008, seed=5)
    world = base.copy()
    world[-30:] = world[-31] * 0.86  # Kern 14 % unter dem Hoch
    defs = [
        ("VWCE", "Vanguard FTSE All-World", "etf", world),
        ("EUNL", "iShares Core MSCI World", "etf", base * 1.01),
        ("GOLD", "Gold ETC Fonds", "etf", walk(900, 0.0007, 0.007, seed=9)),
        ("TECH", "Tech Aktie", "aktie", walk(900, 0.0012, 0.012, seed=11)),
        ("BTC", "Bitcoin", "krypto", walk(900, 0.002, 0.04, seed=2)),
    ]
    items = []
    for vid, name, kind, c in defs:
        inst = _Inst(id=vid, name=name, kind=kind, market="xetra")
        items.append((inst, _Loader(frame(c)), {"last": None, "last_ts": None}))
    return items


def test_advisor_plan_stages_and_state():
    now = pd.Timestamp("2026-11-13 10:00", tz="UTC")
    st = SQLiteStorage(":memory:")
    out = advisor.build(st, now, _items(now), fetch_news=False)
    plan = out["plan"]["items"]
    assert plan[0]["id"] in advisor.CORE_IDS and plan[0]["role"] == "Kern"
    assert sum(p["eur"] for p in plan) == 120 and all(p["eur"] >= 10 for p in plan)
    assert len(plan) <= 3
    assert "BTC" not in [p["id"] for p in plan]  # kein Krypto im Sparplan
    assert "EUNL" not in [p["id"] for p in plan[1:]]  # zu ähnlich zum Kern
    assert out["values"]["BTC"]["r"] == 5
    # Kern 14 % unter Hoch → Stufe 1 ausgelöst, einmalig
    core = plan[0]["id"]
    if core == "VWCE":
        ev = [e for e in out["events"] if e["id"] == "VWCE"]
        assert len(ev) == 1 and ev[0]["stage"] == 1
        again = advisor.build(st, now + pd.Timedelta(hours=1), _items(now), fetch_news=False)
        assert len([e for e in again["events"] if e["id"] == "VWCE"]) == 1
    # Plan bleibt beim nächsten Lauf stabil
    again = advisor.build(st, now + pd.Timedelta(days=1), _items(now), fetch_news=False)
    assert [p["id"] for p in again["plan"]["items"]] == [p["id"] for p in plan]


def test_weak_rule_needs_three_months_below_40():
    hist = {(pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d"): {"X": 30} for i in range(95)}
    assert advisor.is_weak(hist, "X", "2026-11-03")
    hist["2026-10-01"]["X"] = 45
    assert not advisor.is_weak(hist, "X", "2026-11-03")
    assert not advisor.is_weak({"2026-11-01": {"X": 10}}, "X", "2026-11-03")


class FakeGdelt(news.Gdelt):
    def __init__(self, payloads):
        self.payloads = payloads
        self.t = 0.0
        super().__init__(session=object(), sleep=self._sleep, clock=lambda: self.t)

    def _sleep(self, s):
        self.t += s

    def get(self, params):
        self.requests += 1
        self.t += news.SPACING_S
        return self.payloads(params)


def test_news_update_budget_and_flags():
    days = [f"202611{d:02d}T000000Z" for d in range(1, 31)]
    def payloads(p):
        if p["mode"] == "timelinetone":
            vals = [-1.0] * 23 + [-3.0] * 7
            return {"timeline": [{"data": [{"date": d, "value": v} for d, v in zip(days, vals)]}]}
        return {"articles": [{"title": "Lawsuit filed", "url": "https://x.example/1", "domain": "x.example"},
                             {"title": "Probe", "url": "https://x.example/2", "domain": "x.example"}]}
    st = SQLiteStorage(":memory:")
    now = pd.Timestamp("2026-11-13 10:00", tz="UTC")
    client = FakeGdelt(payloads)
    state = news.update(st, now, [("AAA", "Apple Inc."), ("BBB", "BASF SE")], client=client, max_requests=4)
    assert client.requests == 4
    assert news.flagged(state["values"]["AAA"]) and state["values"]["AAA"]["q"] == "Apple"
    w = news.world_summary(state)
    assert any(t["state"] and t["state"]["level"] == "angespannt" for t in w["topics"])
    # Zweiter Lauf: nichts fällig außer restlichen Weltthemen
    client2 = FakeGdelt(payloads)
    news.update(st, now + pd.Timedelta(minutes=15), [("AAA", "Apple Inc.")], client=client2, max_requests=10)
    assert all(p != "AAA" for p in json.loads(st.get_meta("news_state"))["values"] if False)
    assert "2026-11-13" in json.loads(st.get_meta("news_hist"))


def test_clean_name():
    assert news.clean_name("Apple Inc.") == "Apple"
    assert news.clean_name("Siemens Energy AG") == "Siemens Energy"
    assert news.clean_name("SAP SE") is None  # zu kurz, mehrdeutig
    assert news.clean_name("Alphabet Inc. Class A") == "Alphabet"


def test_push_once_per_stage_and_report_every_two_days():
    from tradingagent import push
    st = SQLiteStorage(":memory:")
    now = pd.Timestamp("2026-11-13 07:30", tz="UTC")  # 08:30 Berlin
    st.set_meta("adv_updated", str(int(now.timestamp())))
    ev = {"key": "VWCE:2026-11-01:2", "id": "VWCE", "name": "Vanguard FTSE All-World", "stage": 2, "dd": 0.16,
          "ts": int(now.timestamp()) - 600, "allowed": True, "check_news": False, "score": 60}
    st.set_meta("adv_events", json.dumps([ev]))
    st.set_meta("adv_report", json.dumps({"world": "ruhig", "plan": [{"id": "VWCE", "role": "Kern", "eur": 120}],
                                          "changes": [], "market": [], "stages": [ev]}))
    sent = []
    def send(title, msg, **kw):
        sent.append((title, msg))
        return True
    state = push.run(st, now, {}, {"VWCE": "Vanguard FTSE All-World"}, send)
    assert [t for t, _ in sent] == ["Nachkauf-Stufe 2: Vanguard FTSE All-World", "Lagebericht Ratgeber"]
    assert all("€" not in m for _, m in sent)  # keine Beträge
    sent.clear()
    state = push.run(st, now + pd.Timedelta(hours=1), state, {}, send)
    assert sent == []
    state = push.run(st, now + pd.Timedelta(days=2), state, {}, send)
    assert sent == []  # Daten zu alt
    st.set_meta("adv_updated", str(int((now + pd.Timedelta(days=2)).timestamp())))
    push.run(st, now + pd.Timedelta(days=2), state, {}, send)
    assert [t for t, _ in sent] == ["Lagebericht Ratgeber"]


def test_ladder_matches_day_by_day_pyramid():
    """Die schnelle Staffel-Simulation muss mit der Tagesschleife übereinstimmen."""
    rng = np.random.default_rng(7)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 1800)))
    ls = backtest.LadderSeries(c)
    lad = backtest.LADDERS["bisher"]
    for st in (260, 400, 700):
        for kw in ({}, {"cash_rate": 0.02}, {"deploy_after": 252}):
            a = backtest.simulate_pyramid(c, st, st + 1000, **kw)
            b = backtest.simulate_ladder(ls, lad, st, st + 1000, **kw)
            assert abs(a["value"] - b["value"]) < 1e-9 * max(1, a["value"])
            assert abs(a["cash_left"] - b["cash_left"]) < 1e-9


def test_ladder_upfront_and_sigma():
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 1500)))
    ls = backtest.LadderSeries(c)
    r = backtest.simulate_ladder(ls, backtest.LADDERS["etf_haelfte"], 300, 300)
    assert abs(r["value"] - 1.0) < 1e-12 and abs(r["cash_left"] - 0.5) < 1e-12
    r = backtest.simulate_ladder(ls, backtest.LADDERS["sigma"], 300, 1400)
    assert 0 <= r["cash_left"] <= 1
