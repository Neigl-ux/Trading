"""Quartalszahlen-Kalender und Branchenhinweise.

Datenquellen sind austauschbar (Klasse EarningsSource). Eingebaut ist Yahoo Finance über yfinance:
kostenlos, ohne Konto und ohne Schlüssel, aber eine inoffizielle Schnittstelle ohne Garantie.
Geliefert werden je Firma: Termine (vergangen und bevorstehend), erwarteter und gemeldeter Gewinn je
Aktie, Überraschung in %, dazu Branche und Börsenwert. Umsatzzahlen liefert diese Quelle hier nicht.

Branchenhinweise: Firmen gelten als vergleichbar, wenn die Quelle ihnen dieselbe Branche („industry“)
zuordnet. Das ist KEINE geprüfte Wettbewerberliste und wird in der App auch so bezeichnet. Fehlt die
Branche, gibt es für diesen Wert keinen Branchenhinweis – erfunden wird nichts.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from .registry import model_dir

BERLIN = ZoneInfo("Europe/Berlin")
SOURCE_NAME = "Yahoo Finance (über yfinance; kostenlos, ohne Konto, inoffizielle Schnittstelle)"


class EarningsSource(Protocol):
    name: str

    def profile(self, symbol: str) -> dict[str, Any]: ...
    def dates(self, symbol: str) -> pd.DataFrame: ...


class YahooEarnings:
    name = SOURCE_NAME

    def __init__(self, pause: float = 0.3):
        import yfinance as yf

        self.yf, self.pause = yf, pause

    def profile(self, symbol: str) -> dict[str, Any]:
        info = self.yf.Ticker(symbol).info or {}
        time.sleep(self.pause)
        return {"industry": info.get("industry"), "sector": info.get("sector"), "market_cap": info.get("marketCap"),
                "name": info.get("shortName") or info.get("longName")}

    def dates(self, symbol: str) -> pd.DataFrame:
        df = self.yf.Ticker(symbol).get_earnings_dates(limit=12)
        time.sleep(self.pause)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"EPS Estimate": "eps_est", "Reported EPS": "eps", "Surprise(%)": "surprise"})
        df.index = pd.DatetimeIndex(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("America/New_York")
        return df[[c for c in ("eps_est", "eps", "surprise") if c in df.columns]]


# ---------------------------------------------------------------- Branchen (Cache, monatlich erneuert)
def load_profiles(symbols: list[str], source: EarningsSource | None = None, max_age_days: int = 30) -> dict[str, dict[str, Any]]:
    path = model_dir() / "profile.json"
    cache: dict[str, Any] = {}
    if path.exists():
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    fresh = cache.get("_stand") and (datetime.now(timezone.utc) - datetime.fromisoformat(cache["_stand"])).days < max_age_days
    missing = [s for s in symbols if s not in cache]
    if fresh and not missing:
        return {s: cache[s] for s in symbols if s in cache}
    if source is None:
        try:
            source = YahooEarnings()
        except Exception:  # noqa: BLE001
            return {s: cache[s] for s in symbols if s in cache}
    todo = symbols if not fresh else missing
    for s in todo:
        try:
            cache[s] = source.profile(s)
        except Exception as err:  # noqa: BLE001 – Lücke bleibt sichtbar
            cache.setdefault(s, {"fehler": str(err)[:120]})
    cache["_stand"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {s: cache[s] for s in symbols if s in cache}


def industries(symbols: list[str], **kw) -> dict[str, str]:
    return {s: p["industry"] for s, p in load_profiles(symbols, **kw).items() if p.get("industry")}


# ---------------------------------------------------------------- Kalender und Warnungen
def _reaction(df: pd.DataFrame | None, when: pd.Timestamp) -> dict[str, Any] | None:
    """Kursreaktion: vor Handelsbeginn gemeldet → derselbe Tag, sonst der nächste Handelstag."""
    if df is None or df.empty:
        return None
    local = when.tz_convert(BERLIN)
    day = local.normalize().tz_localize(None)
    if local.hour >= 15:
        day += pd.Timedelta(days=1)
    idx = df.index[df.index >= day]
    if not len(idx):
        return None
    d = idx[0]
    pos = df.index.get_loc(d)
    if pos < 21:
        return None
    c = df["close"]
    vol = df["volume"]
    avg = vol.iloc[pos - 20:pos].mean()
    return {"tag": d.date().isoformat(), "kurs_prozent": float(c.iloc[pos] / c.iloc[pos - 1] - 1),
            "volumen_faktor": float(vol.iloc[pos] / avg) if avg and avg > 0 else None}


def calendar(meta: dict[str, dict[str, Any]], frames: dict[str, pd.DataFrame] | None = None,
             source: EarningsSource | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stocks = [s for s, m in meta.items() if m.get("kind") == "aktie"]
    try:
        source = source or YahooEarnings()
    except Exception as err:  # noqa: BLE001
        return {"quelle": SOURCE_NAME, "stand": now.isoformat(timespec="minutes"), "fehler": f"Quelle nicht verfügbar: {err}",
                "termine": [], "veroeffentlicht": [], "warnungen": []}
    prof = load_profiles(stocks, source=source)
    upcoming, reported, failed = [], [], 0
    now_ts = pd.Timestamp(now)
    for s in stocks:
        try:
            df = source.dates(s)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if df.empty:
            continue
        name = meta[s].get("name") or s
        for when, row in df.iterrows():
            delta = (when - now_ts).total_seconds() / 86400
            base = {"symbol": s, "id": meta[s].get("id", s), "name": name, "zeit": when.isoformat(),
                    "branche": prof.get(s, {}).get("industry")}
            if 0 < delta <= 14:
                upcoming.append({**base, "eps_erwartet": _num(row.get("eps_est"))})
            elif -5 <= delta <= 0:
                reported.append({**base, "eps_erwartet": _num(row.get("eps_est")), "eps_gemeldet": _num(row.get("eps")),
                                 "ueberraschung_prozent": _num(row.get("surprise")),
                                 "reaktion": _reaction((frames or {}).get(s), when)})
    warnings = industry_warnings(upcoming, reported, prof, meta)
    return {"quelle": SOURCE_NAME, "stand": now.isoformat(timespec="minutes"), "abgefragt": len(stocks), "fehler_anzahl": failed,
            "ohne_branche": sum(1 for s in stocks if not prof.get(s, {}).get("industry")),
            "termine": sorted(upcoming, key=lambda x: x["zeit"]), "veroeffentlicht": sorted(reported, key=lambda x: x["zeit"], reverse=True),
            "warnungen": warnings,
            "hinweis": "Vergleichbare Firmen = gleiche Branche laut Yahoo Finance. Das ist keine geprüfte Liste von Wettbewerbern. "
                       "Umsatzzahlen liefert die Quelle hier nicht; Termine können sich kurzfristig ändern."}


def _num(x: Any) -> float | None:
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def industry_warnings(upcoming: list[dict], reported: list[dict], prof: dict[str, dict], meta: dict[str, dict]) -> list[dict[str, Any]]:
    """Ein Ereignis gilt als Branchenereignis, wenn die meldende Firma zu den 3 größten (Börsenwert) ihrer Branche
    im beobachteten Kreis gehört und es dort mindestens einen weiteren Wert gibt."""
    groups: dict[str, list[str]] = {}
    for s, p in prof.items():
        if p.get("industry"):
            groups.setdefault(p["industry"], []).append(s)
    out = []
    for kind, events in (("veroeffentlicht", reported), ("bevorstehend", upcoming)):
        for ev in events:
            g = groups.get(ev.get("branche") or "", [])
            peers = [s for s in g if s != ev["symbol"]]
            if not peers:
                continue
            ranked = sorted(g, key=lambda s: prof.get(s, {}).get("market_cap") or 0, reverse=True)
            if ev["symbol"] not in ranked[:3]:
                continue
            names = [meta[s].get("name") or s for s in peers]
            r = ev.get("reaktion") or {}
            if kind == "veroeffentlicht":
                strong = abs(r.get("kurs_prozent") or 0) >= 0.03 or (r.get("volumen_faktor") or 0) >= 2
                text = (f"Branchenereignis: {ev['name']} hat Quartalszahlen veröffentlicht"
                        + (f" (Kursreaktion {r['kurs_prozent'] * 100:+.1f} %" + (f", Volumen {r['volumen_faktor']:.1f}-fach" if r.get('volumen_faktor') else "") + ")" if r.get("kurs_prozent") is not None else "")
                        + f". Erhöhte Aufmerksamkeit für {', '.join(names[:5])} und vergleichbare Unternehmen empfohlen.")
            else:
                strong = False
                text = f"Bevorstehend: {ev['name']} meldet Quartalszahlen. Das kann auch {', '.join(names[:5])} bewegen."
            out.append({"art": kind, "zeit": ev["zeit"], "ausloeser": ev["name"], "symbol": ev["symbol"], "branche": ev["branche"],
                        "betroffen": [{"symbol": s, "id": meta[s].get("id", s), "name": meta[s].get("name") or s} for s in peers],
                        "stark": strong, "text": text})
    return out
