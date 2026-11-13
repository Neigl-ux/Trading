"""Phase 4: jede Regel mit Rechenbeispiel."""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from tradingagent.risk import BERLIN, Account, Quote, RiskConfig, RiskManager, add_trading_days, atr, fill_price

WED = datetime(2026, 11, 11, 12, 0, tzinfo=BERLIN)  # Mittwoch, Handelszeit


def q(price, t=WED, **kw):
    return Quote(price, t.timestamp(), **kw)


@pytest.fixture
def rm():
    return RiskManager(RiskConfig.load())


def acc(rm, mode="ueber_nacht"):
    a = rm.new_account("test", mode)
    rm.roll(a, WED, {})
    return a


def test_freigegebene_werte(rm):
    c = rm.cfg
    assert (c.start_capital, c.fee_stock, c.risk_pct, c.kill_drawdown_pct, c.allow_short) == (5000, 8, 1, 15, False)
    assert c.modes == ("daytrading", "ueber_nacht")


def test_positionsrechner_beispiel_sap(rm):
    # Konto 5.000 €, 1 % = 50 €. SAP 240 €, ATR 5 € → Stop 230 €.
    a = acc(rm)
    d = rm.buy(a, "SAP", "aktie", q(240), 5.0, WED, {})
    assert d.ok and d.qty == 4  # Kauf zu ca. 240,24 € (Spread + Schlupf): 50 € / 10,24 € = 4 Stück
    assert d.fee == 8 and a.positions["SAP"].stop == 230
    risk = 4 * (d.price - 230)
    assert risk <= 50
    assert a.cash == pytest.approx(5000 - 4 * d.price - 8)


def test_obergrenze_20_prozent(rm):
    a = acc(rm)
    d = rm.buy(a, "ETF", "etf", q(100), 0.2, WED, {})  # sehr ruhig: 50 € / 0,5 € wären 100 Stück = 10.000 €
    assert d.ok and d.qty * d.price <= 1000 + 1e-6 and "20 %" in d.reason


def test_gebuehr_ueber_ein_prozent_wird_nicht_gehandelt(rm):
    a = acc(rm)
    d = rm.buy(a, "X", "aktie", q(100), 10.0, WED, {})  # Stop 20 € entfernt: 2 Stück = 200 €, Gebühr 8 € = 4 %
    assert not d.ok and d.rule == "position" and "Gebühr" in d.reason


def test_krypto_bruchteile_gebuehr_und_10_prozent(rm):
    a = acc(rm)
    d = rm.buy(a, "BTC", "krypto", q(80000), 400.0, WED, {})
    assert d.ok and d.qty < 1 and d.qty * d.price <= 500 + 1e-6
    assert d.fee == pytest.approx(round(d.qty * d.price * 0.002, 2))


def test_krypto_zusammen_hoechstens_20_prozent(rm):
    a = acc(rm)
    prices = {}
    for i, sym in enumerate(["BTC", "ETH"]):
        assert rm.buy(a, sym, "krypto", q(1000), 1.0, WED, prices).ok
        prices[sym] = 1000
    d = rm.buy(a, "SOL", "krypto", q(1000), 1.0, WED, prices)
    assert not d.ok and d.rule == "klumpen"


def test_hoechstens_5_positionen_und_2_je_branche(rm):
    a = acc(rm)
    for i, sec in enumerate(["Auto", "Auto", "Bank", "Chemie", "Pharma"]):
        assert rm.buy(a, f"A{i}", "aktie", q(100), 0.5, WED, {}, sector=sec).ok
    assert not rm.buy(a, "A9", "aktie", q(100), 0.5, WED, {}).ok
    b = acc(rm)
    rm.buy(b, "B1", "aktie", q(100), 0.5, WED, {}, sector="Auto")
    rm.buy(b, "B2", "aktie", q(100), 0.5, WED, {}, sector="Auto")
    d = rm.buy(b, "B3", "aktie", q(100), 0.5, WED, {}, sector="Auto")
    assert not d.ok and d.rule == "klumpen"


def test_tagesverlust_sperrt_neue_trades_aber_nicht_den_verkauf(rm):
    a = acc(rm)
    rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
    later = WED + timedelta(hours=1)
    prices = {"SAP": 85.0}  # 9 Stück à −15 € plus Gebühr: Konto rund −2,9 %
    d = rm.buy(a, "BAS", "aktie", q(50, later), 0.5, later, prices)
    assert not d.ok and d.rule == "tagesverlust"
    assert rm.sell(a, "SAP", q(85, later), later, prices).ok
    nxt = datetime(2026, 11, 12, 10, 0, tzinfo=BERLIN)
    assert rm.buy(a, "BAS", "aktie", q(50, nxt), 0.5, nxt, {}).ok  # neuer Tag: wieder erlaubt


def test_wochenverlust_bis_montag(rm):
    a = acc(rm)
    a.cash = 4700  # −6 % gegenüber Wochenbeginn 5.000 €
    fri = datetime(2026, 11, 13, 10, 0, tzinfo=BERLIN)
    d = rm.buy(a, "SAP", "aktie", q(100, fri), 0.5, fri, {})
    assert not d.ok and d.rule == "wochenverlust"
    mon = datetime(2026, 11, 16, 10, 0, tzinfo=BERLIN)
    assert rm.buy(a, "SAP", "aktie", q(100, mon), 0.5, mon, {}).ok


def test_kill_switch_rueckgang_15_prozent(rm):
    sent = []
    rm.notify = lambda t, m: sent.append((t, m))
    a = acc(rm)
    rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
    a.cash -= 800  # Konto fällt um rund 16 %
    rm.roll(a, WED, {"SAP": 100})
    assert a.killed and not a.positions and sent and "€" not in sent[0][1]
    assert not rm.buy(a, "BAS", "aktie", q(50), 0.5, WED, {}).ok
    assert not rm.restart(a, WED, confirmed=False) and a.killed
    assert rm.restart(a, WED, confirmed=True) and not a.killed


def test_kill_switch_bei_mehr_als_20_ordern(rm):
    a = acc(rm)
    for _ in range(10):
        rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
        rm.sell(a, "SAP", q(103), WED, {})  # Gewinn-Trades, damit keine Verlustserie greift
    assert a.orders_today == 20 and not a.killed
    d = rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
    assert not d.ok and a.killed and "20 Order" in a.kill_reason


def test_verkauf_ohne_kurs_ist_technischer_fehler(rm):
    a = acc(rm)
    rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
    d = rm.sell(a, "SAP", None, WED, {"SAP": 100})
    assert not d.ok and a.killed and not a.positions


def test_verlustserie_pausiert_zwei_handelstage(rm):
    a = acc(rm)
    fri = datetime(2026, 11, 13, 10, 0, tzinfo=BERLIN)
    for i in range(5):
        rm.buy(a, f"V{i}", "aktie", q(100, fri), 0.5, fri, {})
        rm.sell(a, f"V{i}", q(99.5, fri), fri, {})
    assert a.paused_until == "2026-11-17"  # Mo und Di
    tue = datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN)
    assert rm.buy(a, "X", "aktie", q(100, tue), 0.5, tue, {}).rule == "verlustserie"
    wed = datetime(2026, 11, 18, 10, 0, tzinfo=BERLIN)
    assert rm.buy(a, "X", "aktie", q(100, wed), 0.5, wed, {}).ok
    assert add_trading_days(datetime(2026, 11, 13).date(), 2).isoformat() == "2026-11-17"


@pytest.mark.parametrize("when,kind,ok", [
    (datetime(2026, 11, 11, 9, 10, tzinfo=BERLIN), "aktie", False),
    (datetime(2026, 11, 11, 9, 20, tzinfo=BERLIN), "aktie", True),
    (datetime(2026, 11, 11, 21, 50, tzinfo=BERLIN), "etf", False),
    (datetime(2026, 11, 14, 12, 0, tzinfo=BERLIN), "aktie", False),   # Samstag
    (datetime(2026, 11, 14, 3, 0, tzinfo=BERLIN), "krypto", True),    # Krypto rund um die Uhr
])
def test_handelszeit(rm, when, kind, ok):
    a = rm.new_account("t", "ueber_nacht")
    price, a_ = (1000, 1.0) if kind == "krypto" else (100, 0.5)
    assert rm.buy(a, "X", kind, q(price, when), a_, when, {}).ok is ok


def test_datenqualitaet(rm):
    a = acc(rm)
    assert rm.buy(a, "X", "aktie", q(100, WED - timedelta(minutes=3)), 0.5, WED, {}).rule == "datenqualitaet"
    assert rm.buy(a, "X", "aktie", q(100, status="VERZÖGERT"), 0.5, WED, {}).rule == "datenqualitaet"
    assert rm.buy(a, "X", "aktie", q(100, prev=85), 0.5, WED, {}).rule == "datenqualitaet"
    assert rm.buy(a, "X", "aktie", q(100, prev=85, jump_confirmed=True), 0.5, WED, {}).ok


def test_nachrichten_sperre_und_keine_leerverkaeufe(rm):
    a = acc(rm)
    assert rm.buy(a, "X", "aktie", q(100), 0.5, WED, {}, news_block=True).rule == "nachrichten"
    d = rm.sell(a, "Y", q(100), WED, {})
    assert not d.ok and d.rule == "leerverkauf"


def test_stop_wird_nur_nachgezogen_und_loest_aus(rm):
    a = acc(rm)
    rm.buy(a, "SAP", "aktie", q(100), 1.0, WED, {})
    assert a.positions["SAP"].stop == 98
    rm.on_price(a, "SAP", q(105), 1.0, WED, {"SAP": 105})
    assert a.positions["SAP"].stop == 103
    rm.on_price(a, "SAP", q(104), 1.0, WED, {"SAP": 104})
    assert a.positions["SAP"].stop == 103  # nie zurück
    d = rm.on_price(a, "SAP", q(102.9), 1.0, WED, {"SAP": 102.9})
    assert d.ok and "Stop" in d.reason and not a.positions
    assert d.price == pytest.approx(fill_price(rm.cfg, "sell", 102.9, "aktie", stop=True))


def test_daytrading_schliesst_vor_handelsende(rm):
    day, night = acc(rm, "daytrading"), acc(rm, "ueber_nacht")
    for a in (day, night):
        rm.buy(a, "SAP", "aktie", q(100), 0.5, WED, {})
    late = datetime(2026, 11, 11, 21, 46, tzinfo=BERLIN)
    quotes = {"SAP": q(101, late)}
    assert [d.ok for d in rm.end_of_day(day, late, quotes)] == [True] and not day.positions
    assert rm.end_of_day(night, late, quotes) == [] and night.positions


def test_konto_speichern_und_protokoll(rm):
    a = acc(rm)
    rm.buy(a, "SAP", "aktie", q(240), 5.0, WED, {})
    rm.buy(a, "SAP", "aktie", q(240), 5.0, WED, {})  # schon im Konto
    b = Account.from_dict(a.to_dict())
    assert b.positions["SAP"].qty == 4 and b.cash == a.cash
    assert [e["ok"] for e in a.log] == [True, False] and a.log[1]["rule"] == "position"
    rep = rm.report(a, {"SAP": 240})
    assert rep["trades"] == 0 and rep["wert"] < 5000  # Kosten sind schon abgezogen


def test_atr_wilder():
    df = pd.DataFrame({"high": [11, 12, 13, 12, 14] * 4, "low": [9, 10, 11, 10, 12] * 4, "close": [10, 11, 12, 11, 13] * 4})
    v = atr(df, 14)
    assert v is not None and 2.0 <= v <= 3.0
    assert atr(df.head(5), 14) is None
