"""Export der Kursdaten für die Handy-App.

Ergebnis im Export-Ordner:
- index.json         Übersicht (verschlüsselt): Instrumente, letzte Kurse, Status
- s/<kennung>.json   je Instrument alle Kerzenreihen für die 14 Chart-Ansichten

Die Dateinamen sind zufällig je Lauf, damit sie nichts über die
Beobachtungsliste verraten. Rohdaten von Yahoo werden nur verschlüsselt
veröffentlicht.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Instrument, Settings
from .crypto_box import Box
from . import metrics as metrics_mod
from .data.resample import resample
from .data.sessions import MARKET_TZ
from .data.timeframes import CHART_VIEWS, DERIVED_FROM, export_lookbacks
from .storage import Storage


def _rows(df: pd.DataFrame) -> list[list[float]]:
    out = []
    for ts, r in df.iterrows():
        out.append([int(ts.timestamp()), _r(r.open), _r(r.high), _r(r.low), _r(r.close), round(float(r.volume), 4)])
    return out


def _r(x: float) -> float:
    x = float(x)
    if abs(x) >= 1000:
        return round(x, 2)
    if abs(x) >= 1:
        return round(x, 4)
    return float(f"{x:.6g}")


FX_ID = "EURUSD"  # Kurs: US-Dollar je Euro


def to_eur(df: pd.DataFrame, fx_same: pd.DataFrame, fx_daily: pd.DataFrame) -> pd.DataFrame:
    """Rechnet Dollar-Kerzen zum Wechselkurs ihres Zeitpunkts in Euro um.

    Genommen wird der letzte bekannte EUR/USD-Kurs derselben Kerzenlänge,
    sonst der letzte Tageskurs, ganz am Anfang der Historie der erste bekannte.
    """
    if df.empty:
        return df
    rate = pd.Series(float("nan"), index=df.index)
    for fx, tol in ((fx_same, pd.Timedelta(days=4)), (fx_daily, pd.Timedelta(days=14))):
        if fx is None or fx.empty:
            continue
        r = fx["close"].reindex(df.index, method="ffill", tolerance=tol)
        rate = rate.fillna(r)
    if rate.isna().all():
        raise ValueError("Kein EUR/USD-Kurs vorhanden")
    rate = rate.bfill().ffill()
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = df[col] / rate
    return out


class _Loader:
    """Lädt Kerzen eines Werts, bei Dollar-Werten gleich in Euro umgerechnet."""

    def __init__(self, storage: Storage, instrument: Instrument):
        self.storage = storage
        self.inst = instrument
        self.convert = instrument.unit == "$" and instrument.id != FX_ID
        self._fx: dict[str, pd.DataFrame] = {}

    def fx(self, interval: str) -> pd.DataFrame:
        if interval not in self._fx:
            self._fx[interval] = self.storage.load_bars(FX_ID, interval)
        return self._fx[interval]

    def load(self, interval: str, start: pd.Timestamp | None = None) -> pd.DataFrame:
        df = self.storage.load_bars(self.inst.id, interval, start)
        if self.convert and not df.empty:
            df = to_eur(df, self.fx(interval), self.fx("1d"))
        return df

    @property
    def unit(self) -> str:
        return "€" if self.convert else self.inst.unit


def build_series(loader: _Loader, now: pd.Timestamp) -> dict[str, list[list[float]]]:
    tz = MARKET_TZ[loader.inst.market]
    series: dict[str, list[list[float]]] = {}
    for interval, lookback in export_lookbacks().items():
        start = now - lookback
        if interval in DERIVED_FROM:
            src = DERIVED_FROM[interval]
            # etwas mehr laden, damit die erste Woche/der erste Monat vollständig ist
            raw = loader.load(src, start - timedelta(days=35))
            df = resample(raw, interval, tz=tz)
            df = df[df.index >= start - timedelta(days=31)]
        else:
            df = loader.load(interval, start)
        series[interval] = _rows(df)
    return series


_REF_ORDER = ("5m", "15m", "1h", "1d")
_REF_STEP = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def reference_close(loader: _Loader, T: pd.Timestamp) -> float | None:
    """Schluss der letzten abgeschlossenen Kerze vor T, aus der feinsten Reihe,
    die bis T reicht. Dieselbe Regel wie in der App (app/perf.js)."""
    for interval in _REF_ORDER:
        df = loader.load(interval, T - pd.Timedelta(days=15))
        if df.empty:
            continue
        step = pd.Timedelta(seconds=_REF_STEP[interval])
        if df.index[-1] + step < T:
            continue  # Reihe reicht nicht bis T
        starts = df.index
        ends = starts + step
        nxt = starts[1:].append(pd.DatetimeIndex([pd.Timestamp.max.tz_localize("UTC")]))
        ends = ends.where(ends < nxt, nxt)
        done = df[ends <= T]
        if len(done):
            return float(done["close"].iloc[-1])
    return None


def _summary(loader: _Loader, now: pd.Timestamp, quote=None) -> dict[str, Any]:
    """Letzter Kurs und Veränderung „heute“ für die Liste.

    Mit Tradegate-Kurs ist der Listenkurs derselbe wie die Echtzeit im Chart.
    „Heute“ vergleicht mit dem Schluss der letzten Kerze vor Tagesbeginn
    (deutsche Zeit) des Kurszeitpunkts – wie die 1D-Anzeige im Chart.
    """
    last_close, last_ts, source = None, None, None
    for interval, step in (("5m", 300), ("15m", 900), ("1d", 86400)):
        df = loader.load(interval, now - pd.Timedelta(days=10))
        if not df.empty:
            last_close = float(df["close"].iloc[-1])
            last_ts = min(df.index[-1] + pd.Timedelta(seconds=step), now)
            source = "yahoo"
            break
    home_close, home_ts = last_close, last_ts  # Kurs der Heimatbörse (Yahoo), für die Einstellung „wie Google“
    if quote is not None and (last_ts is None or quote.time >= last_ts - pd.Timedelta(hours=12)):
        last_close, last_ts, source = quote.last, quote.time, "tradegate"
    prev_close = change = None
    if last_ts is not None:
        day_start = last_ts.tz_convert("Europe/Berlin").normalize().tz_convert("UTC")
        prev_close = reference_close(loader, day_start)
        if prev_close:
            change = last_close / prev_close - 1
    return {
        "last": None if last_close is None else _r(last_close),
        "last_ts": None if last_ts is None else int(last_ts.timestamp()),
        "last_source": source,
        "prev_close": None if prev_close is None else _r(prev_close),
        "change_1d": None if change is None else round(change, 6),
        "last_home": None if home_close is None else _r(home_close),
        "last_home_ts": None if home_ts is None else int(home_ts.timestamp()),
    }


def live_symbols(inst) -> dict | None:
    """Symbole für Live-Ströme in der App: Binance für Krypto, Finnhub für US-Aktien."""
    if inst.market == "crypto":
        return {"binance": inst.symbol_for("binance")}
    if inst.market == "us" and inst.kind in ("aktie", "etf"):
        sym = inst.symbol_for("finnhub") or inst.symbol_for("yahoo")
        if sym and not sym.startswith("^"):
            return {"finnhub": sym.replace("-", ".")}  # Finnhub schreibt BRK.B statt BRK-B
    return None


_SUFFIX_VENUE = {".DE": "Xetra", ".F": "Frankfurt", ".PA": "Euronext Paris", ".AS": "Euronext Amsterdam",
                 ".BR": "Euronext Brüssel", ".MI": "Borsa Italiana", ".MC": "Madrid", ".SW": "SIX Zürich",
                 ".L": "London", ".CO": "Kopenhagen", ".ST": "Stockholm", ".HE": "Helsinki", ".OL": "Oslo",
                 ".VI": "Wien", ".LS": "Lissabon", ".T": "Tokio", ".HK": "Hongkong"}


def home_venue(inst) -> str:
    """Börsenplatz, von dem Kerzen und Tageswerte stammen."""
    if inst.market == "crypto":
        return "Kraken/Binance"
    if inst.kind == "index":
        return "Index"
    if inst.kind == "devisen":
        return "Devisenmarkt"
    sym = inst.symbol_for("yahoo") or ""
    if "=F" in sym:
        return "Terminbörse (CME/ICE)"
    for suf, name in _SUFFIX_VENUE.items():
        if sym.endswith(suf):
            return name
    if inst.market == "us":
        return "NYSE/Nasdaq"
    return inst.market


def wkn_of(isin: str | None) -> str | None:
    """Deutsche ISINs enthalten die WKN (DE000 + WKN + Prüfziffer bzw. DE + 3 Ziffern + WKN)."""
    if isin and isin.startswith("DE") and len(isin) == 12:
        return isin[5:11]
    return None


class _SkipAdvisor(Exception):
    pass


def _previous_advisor(dest: Path) -> bool:
    """Ratgeber-Datei der aktuell veröffentlichten App holen (gleiche Verschlüsselung). False, wenn nicht möglich."""
    try:
        import requests

        from .notify import APP_URL

        r = requests.get(f"{APP_URL}data/advisor.json", params={"t": int(time.time())}, timeout=20)
        if r.status_code != 200 or not r.content.startswith(b"{"):
            return False
        dest.write_bytes(r.content)
        return True
    except Exception:  # noqa: BLE001
        return False


def export_all(settings: Settings, storage: Storage, now: pd.Timestamp, *, status: dict[str, Any],
               passphrase: str | None, quotes: dict | None = None) -> Path:
    """Schreibt alle Dateien für die App. Gibt den Export-Ordner zurück."""
    out_dir = settings.export_dir
    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / "s").mkdir(parents=True)

    box: Box | None = None
    if settings.encrypt:
        if not passphrase:
            raise RuntimeError(
                "Export ist auf Verschlüsselung eingestellt, aber APP_PASSPHRASE fehlt. "
                "Lege das Passwort als GitHub-Secret bzw. Umgebungsvariable an."
            )
        # Salt bleibt über alle Läufe gleich, damit die App den abgeleiteten
        # Schlüssel merken kann und nicht bei jedem Öffnen neu rechnen muss.
        # Auf dem Server dieselbe Verschlüsselung wie auf GitHub (gleiches Salz), damit die App
        # zwischen beiden Quellen wechseln kann, ohne neu zu laden.
        env_salt = os.environ.get("TA_EXPORT_SALT_B64")
        if env_salt:
            storage.set_meta("export_salt", base64.b64decode(env_salt).hex())
        salt_hex_stored = storage.get_meta("export_salt")
        salt = bytes.fromhex(salt_hex_stored) if salt_hex_stored else os.urandom(16)
        if not salt_hex_stored:
            storage.set_meta("export_salt", salt.hex())
        box = Box(passphrase, salt=salt)
    # Dateinamen-Kennungen wechseln bei jedem Lauf
    salt_hex = os.urandom(8).hex()

    def wrap(payload: Any) -> dict[str, Any]:
        return box.seal(payload) if box else {"v": 1, "enc": False, "data": payload}

    entries = []
    adv_items = []
    for inst in settings.instruments:
        if not settings.markets[inst.market].enabled:
            continue
        file_id = hashlib.sha256(f"{salt_hex}:{inst.id}".encode()).hexdigest()[:20]
        market_cfg = settings.markets[inst.market]
        loader = _Loader(storage, inst)
        try:
            series = build_series(loader, now)
            summary = _summary(loader, now, (quotes or {}).get(inst.id))
        except ValueError:  # noch kein Wechselkurs vorhanden: vorerst in Dollar zeigen
            loader.convert = False
            series = build_series(loader, now)
            summary = _summary(loader, now, (quotes or {}).get(inst.id))
        payload = {
            "id": inst.id,
            "name": inst.name,
            "market": inst.market,
            "delay_minutes": market_cfg.delay_minutes,
            "updated": int(now.timestamp()),
            "converted_from": "USD" if loader.convert else None,
            "series": series,
        }
        (tmp_dir / "s" / f"{file_id}.json").write_text(json.dumps(wrap(payload), separators=(",", ":")), encoding="utf-8")
        last_ts = pd.Timestamp(summary["last_ts"], unit="s", tz="UTC") if summary["last_ts"] else None
        metrics = metrics_mod.compute(loader, now, summary["last"], last_ts, crypto=inst.market == "crypto")
        entries.append({"id": inst.id, "name": inst.name, "market": inst.market, "file": f"s/{file_id}.json",
                        "unit": loader.unit, "kind": inst.kind, "isin": inst.isin, "wkn": inst.wkn or wkn_of(inst.isin), "tier": inst.tier,
                        "ticker": inst.symbol_for("yahoo") or inst.symbol_for("kraken") or inst.id,
                        "venue": home_venue(inst), "metrics": metrics,
                        "converted_from": "USD" if loader.convert else None,
                        "delay_minutes": market_cfg.delay_minutes,
                        "live": live_symbols(inst),
                        **summary})
        adv_items.append((inst, loader, entries[-1]))

    advisor_file = None
    # Server: Ratgeber nur alle ADVISOR_MIN_INTERVAL Sekunden neu rechnen (er ist der teuerste Teil),
    # sonst die letzte Fassung übernehmen.
    min_iv = int(os.environ.get("ADVISOR_MIN_INTERVAL", "0") or 0)
    prev_adv = out_dir / "advisor.json"
    reuse = bool(min_iv) and prev_adv.exists() and (time.time() - prev_adv.stat().st_mtime) < min_iv
    if not reuse and os.environ.get("GITHUB_EVENT_NAME") == "push" and _previous_advisor(tmp_dir / "advisor.json"):
        # GitHub, Code-Änderung: nur die App wird neu veröffentlicht, der Ratgeber der letzten Veröffentlichung bleibt
        reuse = True
        print("Ratgeber: Fassung der letzten Veröffentlichung übernommen (Code-Änderung)")
    elif reuse:
        shutil.copy2(prev_adv, tmp_dir / "advisor.json")
        print("Ratgeber: letzte Fassung übernommen")
    if reuse:
        advisor_file = "advisor.json"
    try:
        if reuse:
            raise _SkipAdvisor
        from . import advisor as advisor_mod
        adv = advisor_mod.build(storage, now, adv_items, fetch_news=os.environ.get("ADVISOR_NEWS", "1") != "0")
        (tmp_dir / "advisor.json").write_text(json.dumps(wrap(adv), separators=(",", ":")), encoding="utf-8")
        advisor_file = "advisor.json"
        print(f"Ratgeber: {len(adv['values'])} Werte bewertet, Nachrichten-Abfragen: "
              f"{len(adv['news_errors'])} Fehler" + (f" ({'; '.join(adv['news_errors'])})" if adv['news_errors'] else ""))
    except _SkipAdvisor:
        pass
    except Exception as exc:  # noqa: BLE001 - der Ratgeber darf den Kurs-Export nie verhindern
        print(f"Ratgeber übersprungen: {type(exc).__name__}: {exc}")

    index_payload = {
        "advisor": advisor_file,
        "updated": int(now.timestamp()),
        "instruments": entries,
        "views": [{"key": v.key, "label": v.label, "interval": v.interval,
                   "lookback_s": int(v.lookback.total_seconds())} for v in CHART_VIEWS],
        "status": status,
    }
    index = wrap(index_payload)
    if box:
        index["kdf"] = box.kdf_header()
    (tmp_dir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)
    return out_dir
