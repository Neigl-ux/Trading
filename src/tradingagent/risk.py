"""Phase 4 – Risikomanagement für den Simulator (Spielgeld).

Jede Order muss durch diesen Baustein. Er entscheidet nicht, OB eine Strategie kauft, sondern
WIE VIEL sie darf und WANN Schluss ist. Alle Werte stehen in config/risk.yaml (freigegeben 13.11.2026).

Bausteine:
  * Kosten: Gebühr, Spread (Geld-/Briefkurs), Schlupf – eher zu schlecht als zu gut gerechnet
  * Positionsrechner: höchstens 1 % Risiko je Trade, Stop = Einstieg − 2 × ATR(14), Obergrenzen je Position
  * Limits: Anzahl Positionen, Branchen-/Krypto-Klumpen, Tages-/Wochenverlust, Verlustserie,
    Handelszeit, Datenqualität, Nachrichten-Sperre
  * Kill Switch: Rückgang vom Höchststand, technischer Fehler, zu viele Order – schließt alles,
    wieder einschalten nur von Hand
  * Protokoll: jede Order und jede Ablehnung mit Zeit, Wert, Regel und Grund

Keine Leerverkäufe, kein Hebel, kein Kredit. Echter Handel (Phase 8) nur nach ausdrücklicher Freigabe.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from .config import ROOT

BERLIN = ZoneInfo("Europe/Berlin")
CONFIG_FILE = ROOT / "config" / "risk.yaml"


# ---------------------------------------------------------------- Einstellungen
@dataclass(frozen=True)
class RiskConfig:
    start_capital: float = 5000.0
    modes: tuple[str, ...] = ("daytrading", "ueber_nacht")
    allow_short: bool = False
    fee_stock: float = 8.0
    fee_crypto_pct: float = 0.2
    spread_pct: float = 0.1
    spread_crypto_pct: float = 0.05
    slip_pct: float = 0.05
    slip_stop_pct: float = 0.2
    tax_pct: float = 26.375
    risk_pct: float = 1.0
    stop_atr: float = 2.0
    atr_days: int = 14
    max_pos_pct: float = 20.0
    max_pos_crypto_pct: float = 10.0
    max_fee_pct: float = 1.0
    max_positions: int = 5
    max_per_sector: int = 2
    crypto_total_pct: float = 20.0
    day_loss_pct: float = 2.0
    week_loss_pct: float = 5.0
    loss_streak: int = 5
    pause_days: int = 2
    hours_from: time = time(9, 15)
    hours_to: time = time(21, 45)
    max_quote_age_s: int = 120
    max_jump_pct: float = 10.0
    kill_drawdown_pct: float = 15.0
    kill_orders_per_day: int = 20

    @classmethod
    def load(cls, path: Path | None = None) -> "RiskConfig":
        raw = yaml.safe_load((path or CONFIG_FILE).read_text(encoding="utf-8"))
        k, c, p, lim, ks = raw["konto"], raw["kosten"], raw["position"], raw["limits"], raw["kill_switch"]
        hm = lambda s: time(*map(int, str(s).split(":")))  # noqa: E731
        return cls(
            start_capital=float(k["startkapital_eur"]), modes=tuple(k["modi"]), allow_short=bool(k["leerverkaeufe"]),
            fee_stock=float(c["gebuehr_aktie_etf_eur"]), fee_crypto_pct=float(c["gebuehr_krypto_prozent"]),
            spread_pct=float(c["spread_ersatz_prozent"]), spread_crypto_pct=float(c["spread_ersatz_krypto_prozent"]),
            slip_pct=float(c["schlupf_prozent"]), slip_stop_pct=float(c["schlupf_stop_prozent"]), tax_pct=float(c["steuer_prozent"]),
            risk_pct=float(p["risiko_prozent"]), stop_atr=float(p["stop_atr_faktor"]), atr_days=int(p["atr_tage"]),
            max_pos_pct=float(p["max_anteil_prozent"]), max_pos_crypto_pct=float(p["max_anteil_krypto_prozent"]),
            max_fee_pct=float(p["max_gebuehr_anteil_prozent"]),
            max_positions=int(lim["max_positionen"]), max_per_sector=int(lim["max_je_branche"]),
            crypto_total_pct=float(lim["krypto_gesamt_prozent"]), day_loss_pct=float(lim["tagesverlust_prozent"]),
            week_loss_pct=float(lim["wochenverlust_prozent"]), loss_streak=int(lim["verlustserie"]),
            pause_days=int(lim["pause_handelstage"]), hours_from=hm(lim["handelszeit_von"]), hours_to=hm(lim["handelszeit_bis"]),
            max_quote_age_s=int(lim["max_kursalter_s"]), max_jump_pct=float(lim["max_sprung_prozent"]),
            kill_drawdown_pct=float(ks["rueckgang_vom_hoch_prozent"]), kill_orders_per_day=int(ks["max_order_je_tag"]),
        )


def is_crypto(kind: str) -> bool:
    return kind == "krypto"


# ---------------------------------------------------------------- Kosten
def fee(cfg: RiskConfig, kind: str, amount: float) -> float:
    return round(abs(amount) * cfg.fee_crypto_pct / 100, 2) if is_crypto(kind) else cfg.fee_stock


def fill_price(cfg: RiskConfig, side: str, price: float, kind: str, *, bid: float | None = None,
               ask: float | None = None, stop: bool = False) -> float:
    """Ausführungskurs: Kauf zum Brief-, Verkauf zum Geldkurs, dazu Schlupf (bei Stops mehr)."""
    half = (cfg.spread_crypto_pct if is_crypto(kind) else cfg.spread_pct) / 200
    slip = (cfg.slip_stop_pct if stop else cfg.slip_pct) / 100
    if side == "buy":
        base = ask if ask and ask > 0 else price * (1 + half)
        return base * (1 + slip)
    base = bid if bid and bid > 0 else price * (1 - half)
    return base * (1 - slip)


# ---------------------------------------------------------------- Stop
def atr(daily: pd.DataFrame, n: int = 14) -> float | None:
    """ATR nach Wilder aus Tageskerzen (Spalten high, low, close)."""
    if daily is None or len(daily) < n + 1:
        return None
    h, low, c = daily["high"].to_numpy(float), daily["low"].to_numpy(float), daily["close"].to_numpy(float)
    tr = [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, len(c))]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def initial_stop(cfg: RiskConfig, entry: float, atr_value: float) -> float:
    return entry - cfg.stop_atr * atr_value


def trail_stop(cfg: RiskConfig, stop: float, highest: float, atr_value: float) -> float:
    """Der Stop wird nur nachgezogen, nie zurückgesetzt."""
    return max(stop, highest - cfg.stop_atr * atr_value)


# ---------------------------------------------------------------- Konto
@dataclass
class Position:
    id: str
    kind: str
    sector: str | None
    qty: float
    entry: float          # Ausführungskurs je Stück
    stop: float
    highest: float
    opened: float         # Zeit (Sekunden)
    fees: float = 0.0


@dataclass
class Quote:
    price: float
    time: float                    # Zeit des Kurses (Sekunden)
    status: str = "LIVE"
    bid: float | None = None
    ask: float | None = None
    prev: float | None = None      # letzter bekannter Kurs, für die Sprungprüfung
    jump_confirmed: bool = False   # großer Sprung von zweiter Quelle bestätigt


@dataclass
class Account:
    name: str
    mode: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    peak: float = 0.0
    day: str = ""
    day_start: float = 0.0
    week: str = ""
    week_start: float = 0.0
    streak: int = 0
    paused_until: str = ""         # Datum (einschließlich), bis zu dem die Strategie pausiert
    killed: bool = False
    kill_reason: str = ""
    orders_today: int = 0
    realized: list[float] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(i, p.entry) for i, p in self.positions.items())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Account":
        d = dict(d)
        d["positions"] = {k: Position(**v) for k, v in d.get("positions", {}).items()}
        return cls(**d)


@dataclass
class Decision:
    ok: bool
    rule: str
    reason: str
    qty: float = 0.0
    price: float = 0.0
    fee: float = 0.0


@dataclass
class Sizing:
    qty: float
    price: float
    amount: float
    fee: float
    risk_eur: float
    capped_by: str | None = None
    reject: str | None = None      # Grund, wenn nicht gehandelt werden darf


# ---------------------------------------------------------------- Regeln
class RiskManager:
    def __init__(self, cfg: RiskConfig | None = None, notify: Callable[[str, str], Any] | None = None):
        self.cfg = cfg or RiskConfig.load()
        self.notify = notify

    # ---- Konto anlegen und fortschreiben
    def new_account(self, name: str, mode: str) -> Account:
        if mode not in self.cfg.modes:
            raise ValueError(f"unbekannter Modus {mode}")
        c = self.cfg.start_capital
        return Account(name=name, mode=mode, cash=c, peak=c, day_start=c, week_start=c)

    def roll(self, acc: Account, now: datetime, prices: dict[str, float]) -> None:
        """Neuer Tag / neue Woche, Höchststand, Kill Switch bei Rückgang. Bei jedem Kurs-Takt aufrufen."""
        loc = now.astimezone(BERLIN)
        eq = acc.equity(prices)
        d, w = loc.date().isoformat(), f"{loc.isocalendar().year}-W{loc.isocalendar().week:02d}"
        if acc.day != d:
            acc.day, acc.day_start, acc.orders_today = d, eq, 0
        if acc.week != w:
            acc.week, acc.week_start = w, eq
        acc.peak = max(acc.peak, eq)
        if not acc.killed and acc.peak > 0 and eq <= acc.peak * (1 - self.cfg.kill_drawdown_pct / 100):
            self.kill(acc, f"Rückgang vom Höchststand über {self.cfg.kill_drawdown_pct:g} %", now, prices)

    # ---- Positionsrechner
    def size(self, acc: Account, entry_quote: float, stop: float, kind: str, prices: dict[str, float],
             *, bid: float | None = None, ask: float | None = None) -> Sizing:
        cfg = self.cfg
        price = fill_price(cfg, "buy", entry_quote, kind, bid=bid, ask=ask)
        eq = acc.equity(prices)
        dist = price - stop
        if not (dist > 0) or stop <= 0:
            return Sizing(0, price, 0, 0, 0, reject="Stop liegt nicht unter dem Einstieg")
        risk_eur = eq * cfg.risk_pct / 100
        step = 1e-6 if is_crypto(kind) else 1.0
        floor = lambda q: math.floor(q / step + 1e-9) * step  # noqa: E731
        qty, capped = floor(risk_eur / dist), None
        cap = eq * (cfg.max_pos_crypto_pct if is_crypto(kind) else cfg.max_pos_pct) / 100
        if qty * price > cap:
            qty, capped = floor(cap / price), f"höchstens {cfg.max_pos_crypto_pct if is_crypto(kind) else cfg.max_pos_pct:g} % des Kontos je Position"
        # Bargeld reicht (inklusive Gebühr)?
        affordable = (floor(acc.cash / (price * (1 + cfg.fee_crypto_pct / 100))) if is_crypto(kind)
                      else floor(max(0.0, acc.cash - cfg.fee_stock) / price))
        if qty > affordable:
            qty, capped = affordable, "verfügbares Spielgeld"
        qty = max(0.0, round(qty, 6))
        amount = qty * price
        f = fee(cfg, kind, amount) if qty > 0 else 0.0
        out = Sizing(qty, price, amount, f, qty * dist, capped)
        if qty <= 0:
            out.reject = "Positionsgröße wäre 0 Stück"
        elif f > amount * cfg.max_fee_pct / 100:
            out.reject = f"Gebühr über {cfg.max_fee_pct:g} % des Betrags (Position zu klein)"
        return out

    # ---- Prüfungen
    def _hours_ok(self, kind: str, now: datetime) -> bool:
        if is_crypto(kind):
            return True
        loc = now.astimezone(BERLIN)
        return loc.weekday() < 5 and self.cfg.hours_from <= loc.time() < self.cfg.hours_to

    def _data_problem(self, q: Quote | None, now: datetime) -> str | None:
        if q is None or not (q.price and q.price > 0):
            return "kein gültiger Kurs"
        if str(q.status).upper() != "LIVE":
            return f"Kursstatus {q.status}, nicht LIVE"
        if now.timestamp() - q.time > self.cfg.max_quote_age_s:
            return f"Kurs älter als {self.cfg.max_quote_age_s} s"
        if q.prev and abs(q.price / q.prev - 1) * 100 > self.cfg.max_jump_pct and not q.jump_confirmed:
            return f"Kurssprung über {self.cfg.max_jump_pct:g} % ohne Bestätigung"
        return None

    def _entry_block(self, acc: Account, vid: str, kind: str, sector: str | None, now: datetime,
                     prices: dict[str, float], news_block: bool) -> tuple[str, str] | None:
        cfg, eq = self.cfg, acc.equity(prices)
        today = now.astimezone(BERLIN).date()
        if acc.killed:
            return "kill_switch", f"Handel gestoppt: {acc.kill_reason}"
        if acc.paused_until and today.isoformat() <= acc.paused_until:
            return "verlustserie", f"Strategie pausiert bis einschließlich {acc.paused_until} ({cfg.loss_streak} Verluste in Folge)"
        if not self._hours_ok(kind, now):
            return "handelszeit", f"außerhalb der Handelszeit {cfg.hours_from:%H:%M}–{cfg.hours_to:%H:%M} Uhr"
        if acc.mode == "daytrading" and now.astimezone(BERLIN).time() >= cfg.hours_to:
            return "handelszeit", "Daytrading-Konto: nach Handelsschluss keine neuen Positionen"
        if acc.day_start and eq <= acc.day_start * (1 - cfg.day_loss_pct / 100):
            return "tagesverlust", f"Tagesverlust von {cfg.day_loss_pct:g} % erreicht, heute keine neuen Trades"
        if acc.week_start and eq <= acc.week_start * (1 - cfg.week_loss_pct / 100):
            return "wochenverlust", f"Wochenverlust von {cfg.week_loss_pct:g} % erreicht, bis Montag keine neuen Trades"
        if vid in acc.positions:
            return "position", "Wert ist bereits im Konto"
        if len(acc.positions) >= cfg.max_positions:
            return "max_positionen", f"schon {cfg.max_positions} offene Positionen"
        if sector and sum(1 for p in acc.positions.values() if p.sector == sector) >= cfg.max_per_sector:
            return "klumpen", f"schon {cfg.max_per_sector} Positionen in der Branche {sector}"
        if news_block:
            return "nachrichten", "frische Warnmeldung zum Wert, gesperrt bis morgen"
        return None

    def _log(self, acc: Account, now: datetime, vid: str, side: str, d: Decision) -> Decision:
        acc.log.append({"t": int(now.timestamp()), "id": vid, "side": side, "ok": d.ok, "rule": d.rule,
                        "reason": d.reason, "qty": d.qty, "price": round(d.price, 6), "fee": d.fee})
        return d

    def _count_order(self, acc: Account, now: datetime, prices: dict[str, float]) -> bool:
        """Zählt eine Order; mehr als erlaubt löst den Kill Switch aus. False = Order nicht ausführen."""
        if acc.orders_today >= self.cfg.kill_orders_per_day:
            self.kill(acc, f"mehr als {self.cfg.kill_orders_per_day} Order an einem Tag", now, prices)
            return False
        acc.orders_today += 1
        return True

    # ---- Order
    def buy(self, acc: Account, vid: str, kind: str, quote: Quote | None, atr_value: float | None, now: datetime,
            prices: dict[str, float], *, sector: str | None = None, news_block: bool = False,
            stop: float | None = None) -> Decision:
        self.roll(acc, now, prices)
        block = self._entry_block(acc, vid, kind, sector, now, prices, news_block)
        if block:
            return self._log(acc, now, vid, "buy", Decision(False, *block))
        problem = self._data_problem(quote, now)
        if problem:
            return self._log(acc, now, vid, "buy", Decision(False, "datenqualitaet", problem))
        if stop is None:
            if not atr_value or atr_value <= 0:
                return self._log(acc, now, vid, "buy", Decision(False, "stop", "keine ATR für den Stop vorhanden"))
            stop = initial_stop(self.cfg, quote.price, atr_value)
        sz = self.size(acc, quote.price, stop, kind, prices, bid=quote.bid, ask=quote.ask)
        if sz.reject:
            return self._log(acc, now, vid, "buy", Decision(False, "position", sz.reject))
        if is_crypto(kind):
            eq = acc.equity(prices)
            held = sum(p.qty * prices.get(i, p.entry) for i, p in acc.positions.items() if is_crypto(p.kind))
            if held + sz.amount > eq * self.cfg.crypto_total_pct / 100:
                return self._log(acc, now, vid, "buy", Decision(False, "klumpen", f"Krypto zusammen höchstens {self.cfg.crypto_total_pct:g} %"))
        if not self._count_order(acc, now, prices):
            return self._log(acc, now, vid, "buy", Decision(False, "kill_switch", acc.kill_reason))
        acc.cash -= sz.amount + sz.fee
        acc.positions[vid] = Position(vid, kind, sector, sz.qty, sz.price, stop, quote.price, now.timestamp(), sz.fee)
        why = f"{sz.qty:g} Stück, Stop {stop:.4g}, Risiko {sz.risk_eur:.2f} €" + (f", begrenzt durch {sz.capped_by}" if sz.capped_by else "")
        return self._log(acc, now, vid, "buy", Decision(True, "kauf", why, sz.qty, sz.price, sz.fee))

    def sell(self, acc: Account, vid: str, quote: Quote | None, now: datetime, prices: dict[str, float], *,
             reason: str = "Verkauf", stop: bool = False, count: bool = True) -> Decision:
        pos = acc.positions.get(vid)
        if pos is None:
            why = "Leerverkäufe sind nicht erlaubt" if not self.cfg.allow_short else "keine Position"
            return self._log(acc, now, vid, "sell", Decision(False, "leerverkauf", why))
        if quote is None or not (quote.price and quote.price > 0):
            # Schließen ohne Kurs ist ein technischer Fehler: alles stoppen
            self.kill(acc, f"kein Kurs zum Schließen von {vid}", now, prices)
            return self._log(acc, now, vid, "sell", Decision(False, "kill_switch", acc.kill_reason))
        if count and not self._count_order(acc, now, prices):
            return self._log(acc, now, vid, "sell", Decision(False, "kill_switch", acc.kill_reason))
        return self._close(acc, pos, quote, now, reason, stop)

    def _close(self, acc: Account, pos: Position, quote: Quote, now: datetime, reason: str, stop: bool) -> Decision:
        price = fill_price(self.cfg, "sell", quote.price, pos.kind, bid=quote.bid, ask=quote.ask, stop=stop)
        amount = pos.qty * price
        f = fee(self.cfg, pos.kind, amount)
        acc.cash += amount - f
        pnl = amount - f - (pos.qty * pos.entry + pos.fees)
        acc.realized.append(round(pnl, 2))
        del acc.positions[pos.id]
        acc.streak = acc.streak + 1 if pnl < 0 else 0
        if acc.streak >= self.cfg.loss_streak:
            acc.paused_until = add_trading_days(now.astimezone(BERLIN).date(), self.cfg.pause_days).isoformat()
            acc.streak = 0
        return self._log(acc, now, pos.id, "sell", Decision(True, "verkauf", f"{reason}, Ergebnis {pnl:+.2f} €", pos.qty, price, f))

    # ---- Kurs-Takt: Stop nachziehen und auslösen, Daytrading schließen
    def on_price(self, acc: Account, vid: str, quote: Quote, atr_value: float | None, now: datetime,
                 prices: dict[str, float]) -> Decision | None:
        pos = acc.positions.get(vid)
        if pos is None or not quote or not quote.price:
            return None
        pos.highest = max(pos.highest, quote.price)
        if atr_value and atr_value > 0:
            pos.stop = trail_stop(self.cfg, pos.stop, pos.highest, atr_value)
        if quote.price <= pos.stop:
            return self.sell(acc, vid, quote, now, prices, reason="Stop ausgelöst", stop=True)
        return None

    def end_of_day(self, acc: Account, now: datetime, quotes: dict[str, Quote]) -> list[Decision]:
        """Daytrading-Konto: ab Handelsschluss alles schließen (auch Krypto)."""
        if acc.mode != "daytrading" or now.astimezone(BERLIN).time() < self.cfg.hours_to:
            return []
        prices = {k: q.price for k, q in quotes.items() if q and q.price}
        return [self.sell(acc, vid, quotes.get(vid), now, prices, reason="Daytrading: Schluss vor Handelsende")
                for vid in list(acc.positions)]

    # ---- Kill Switch
    def kill(self, acc: Account, reason: str, now: datetime, prices: dict[str, float]) -> None:
        if acc.killed:
            return
        acc.killed, acc.kill_reason = True, reason
        for vid in list(acc.positions):
            p = acc.positions[vid]
            price = prices.get(vid) or p.entry  # ohne aktuellen Kurs: zum Einstieg schließen und im Protokoll vermerken
            self._close(acc, p, Quote(price, now.timestamp()), now, f"Kill Switch: {reason}", stop=True)
        acc.log.append({"t": int(now.timestamp()), "id": "*", "side": "kill", "ok": True, "rule": "kill_switch",
                        "reason": reason, "qty": 0, "price": 0, "fee": 0})
        if self.notify:
            try:
                self.notify("Simulator gestoppt", f"Konto {acc.name}: {reason}. Neustart nur von Hand in der App.")
            except Exception:  # noqa: BLE001 – die Abschaltung darf nie an der Nachricht scheitern
                pass

    def restart(self, acc: Account, now: datetime, *, confirmed: bool) -> bool:
        """Nur von Hand und mit Bestätigung. Höchststand beginnt neu beim aktuellen Kontostand."""
        if not acc.killed or not confirmed:
            return False
        acc.killed, acc.kill_reason = False, ""
        eq = acc.cash  # nach dem Kill Switch ist alles geschlossen
        acc.peak, acc.day_start, acc.week_start, acc.streak = eq, eq, eq, 0
        acc.log.append({"t": int(now.timestamp()), "id": "*", "side": "restart", "ok": True, "rule": "kill_switch",
                        "reason": "von Hand wieder eingeschaltet", "qty": 0, "price": 0, "fee": 0})
        return True

    # ---- Bericht
    def report(self, acc: Account, prices: dict[str, float]) -> dict[str, Any]:
        eq = acc.equity(prices)
        gain = sum(acc.realized)
        return {"konto": acc.name, "modus": acc.mode, "wert": round(eq, 2), "bargeld": round(acc.cash, 2),
                "ergebnis_prozent": round((eq / self.cfg.start_capital - 1) * 100, 2),
                "trades": len(acc.realized), "gewinner": sum(1 for x in acc.realized if x > 0),
                "steuer_geschaetzt": round(max(0.0, gain) * self.cfg.tax_pct / 100, 2),
                "rueckgang_vom_hoch_prozent": round((1 - eq / acc.peak) * 100, 2) if acc.peak else 0.0,
                "gestoppt": acc.killed, "grund": acc.kill_reason}


def add_trading_days(d: date, n: int) -> date:
    """Pause über n Handelstage ab dem nächsten Handelstag (Wochenende zählt nicht)."""
    out, left = d, n
    while left > 0:
        out += timedelta(days=1)
        if out.weekday() < 5:
            left -= 1
    return out
