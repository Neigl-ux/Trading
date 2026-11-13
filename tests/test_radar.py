from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagent import radar


def test_levels_and_stages():
    assert radar.day_level_crossed(-0.031, radar.DAY_LEVELS["etf"], []) == 0.03
    assert radar.day_level_crossed(-0.031, radar.DAY_LEVELS["etf"], [-0.02, -0.03]) is None
    assert radar.day_level_crossed(0.021, radar.DAY_LEVELS["etf"], [-0.02, -0.03]) == 0.02
    assert radar.stage_of(0.149) == 1 and radar.stage_of(0.30) == 5 and radar.stage_of(0.05) == 0


def test_fast_move_needs_old_enough_price():
    h = deque([(0, 100.0), (600, 99.0)])
    assert radar.fast_move(h, 900, 97.0) == 97.0 / 100.0 - 1
    assert radar.fast_move(deque([(800, 100.0)]), 900, 97.0) is None


def test_check_sends_once_per_level(tmp_path, monkeypatch):
    monkeypatch.setattr(radar, "STATE", tmp_path / "s.json")
    sent = []
    r = radar.Radar(send=lambda *a, **k: sent.append(a) or True)
    etf = next(w for w in r.watch.values() if w.inst.kind == "etf")
    etf.prev_close, etf.high12 = 100.0, 110.0
    now = datetime(2026, 11, 13, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    m1 = r.check(now, {etf.inst.id: (96.5, None)})  # −3,5 % heute, −12,3 % vom Hoch
    assert any("heute" in t for _, t, *_ in m1) and any("Stufe 1" in t for _, t, *_ in m1)
    m2 = r.check(now, {etf.inst.id: (96.4, None)})
    assert m2 == []
    m3 = r.check(now, {etf.inst.id: (93.0, None)})  # −7 % → Stufe 5 % und Stufe 2 (−15,5 %)
    assert any("Stufe 2" in t for _, t, *_ in m3)
