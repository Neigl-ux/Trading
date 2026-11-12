"""Sammel-Lauf: Kurse holen, prüfen, speichern.

Wird alle 15 Minuten von GitHub Actions gestartet (später auf dem Server
per Zeitgeber). Jeder Lauf ist unabhängig: Fällt einer aus, holt der
nächste die fehlenden Kerzen über die Überlappung nach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pandas as pd

from .config import Instrument, MarketConfig, Settings
from .data.bars import drop_unclosed
from .data.providers import MarketDataProvider, fetch_with_fallback
from .data.quality import check_and_clean, check_freshness, check_gaps
from .data.sessions import session_for
from .data.timeframes import interval_delta
from .storage import Storage


@dataclass
class FetchLog:
    instrument: str
    interval: str
    provider: str | None = None
    new_bars: int = 0
    skipped: str | None = None
    errors: list[str] = field(default_factory=list)
    quality: dict[str, Any] | None = None

    @property
    def failed(self) -> bool:
        return self.skipped is None and self.provider is None


@dataclass
class RunSummary:
    started: pd.Timestamp
    logs: list[FetchLog] = field(default_factory=list)

    @property
    def attempted(self) -> list[FetchLog]:
        return [log for log in self.logs if log.skipped is None]

    @property
    def all_failed(self) -> bool:
        attempted = self.attempted
        return bool(attempted) and all(log.failed for log in attempted)

    def as_dict(self) -> dict[str, Any]:
        attempted = self.attempted
        return {
            "started": self.started.isoformat(),
            "fetches": len(attempted),
            "failed": sum(1 for log in attempted if log.failed),
            "new_bars": sum(log.new_bars for log in self.logs),
            "warnings": [
                {"instrument": log.instrument, "interval": log.interval, **issue}
                for log in self.logs if log.quality
                for issue in log.quality["issues"] if issue["severity"] in ("warning", "error")
            ],
            "errors": [
                {"instrument": log.instrument, "interval": log.interval, "errors": log.errors}
                for log in self.logs if log.failed
            ],
        }


def _last_session_close(market: str, now: pd.Timestamp) -> pd.Timestamp | None:
    session = session_for(market)
    if session.open is None:
        return None
    local = now.tz_convert(session.tz)
    day = local.normalize()
    for _ in range(8):
        if not session.weekdays_only or day.weekday() < 5:
            close = day + pd.Timedelta(hours=session.close.hour, minutes=session.close.minute)
            if close <= local:
                return close.tz_convert("UTC")
        day -= pd.Timedelta(days=1)
    return None


def _should_skip(storage: Storage, inst: Instrument, interval: str, cfg: MarketConfig, now: pd.Timestamp) -> str | None:
    """Außerhalb der Handelszeit nach dem letzten erfolgreichen Abruf nicht erneut fragen."""
    session = session_for(inst.market)
    if session.open is None or interval == "1d":
        return None
    ref = now - pd.Timedelta(minutes=cfg.delay_minutes)
    if session.is_open(ref) or session.is_open(now):
        return None
    last_fetch = storage.get_meta(f"fetched:{inst.id}:{interval}")
    close = _last_session_close(inst.market, now)
    if last_fetch and close is not None:
        if pd.Timestamp(last_fetch) >= close + pd.Timedelta(minutes=cfg.delay_minutes + 15):
            return "Markt geschlossen, Daten aktuell"
    return None


def collect(settings: Settings, storage: Storage, providers: dict[str, MarketDataProvider],
            now: pd.Timestamp | None = None) -> RunSummary:
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC")
    summary = RunSummary(started=now)

    for market_name, cfg in settings.markets.items():
        if not cfg.enabled:
            continue
        intervals = list(dict.fromkeys([*cfg.intervals, "1d"]))
        for inst in settings.instruments_for(market_name):
            for interval in intervals:
                summary.logs.append(_collect_one(settings, storage, providers, inst, interval, cfg, now))

    storage.log_run(summary.as_dict())
    return summary


def _collect_one(settings: Settings, storage: Storage, providers: dict[str, MarketDataProvider],
                 inst: Instrument, interval: str, cfg: MarketConfig, now: pd.Timestamp) -> FetchLog:
    log = FetchLog(inst.id, interval)
    skip = _should_skip(storage, inst, interval, cfg, now)
    if skip:
        log.skipped = skip
        return log

    step = interval_delta(interval)
    last = storage.last_ts(inst.id, interval)
    refresh_key = f"daily_refresh:{inst.id}"
    full_refresh = False
    if interval == "1d":
        last_refresh = storage.get_meta(refresh_key)
        full_refresh = last_refresh is None or now - pd.Timestamp(last_refresh) > timedelta(days=settings.daily_full_refresh_days)

    if last is None or full_refresh:
        start = now - timedelta(days=settings.backfill_days.get(interval, 30))
    else:
        start = last - step * settings.overlap_bars

    chain = cfg.daily_providers if interval == "1d" else cfg.providers
    result = fetch_with_fallback(inst, interval, start, now, chain, providers)
    log.errors = result.errors
    if result.provider is None:
        return log
    log.provider = result.provider

    # Sprungprüfung braucht Vorlauf: gespeicherte Historie plus neue Kerzen
    history = storage.load_bars(inst.id, interval, start - step * 60) if last is not None else None
    frame = result.frame
    if history is not None and not history.empty:
        frame = pd.concat([history[history.index < frame.index.min()], frame]) if not frame.empty else history
    clean, report = check_and_clean(frame, inst.id, interval, inst.market, jumps_after=last)
    clean = clean[clean.index >= result.frame.index.min()] if not result.frame.empty else clean
    clean = drop_unclosed(clean, pd.Timedelta(step), now, pd.Timedelta(minutes=cfg.delay_minutes))
    log.new_bars = storage.upsert_bars(inst.id, interval, clean, result.provider)

    stored = storage.load_bars(inst.id, interval, now - timedelta(days=7 if interval != "1d" else 120))
    check_gaps(stored, inst.id, interval, inst.market, report)
    check_freshness(storage.last_ts(inst.id, interval), interval, inst.market, now, cfg.delay_minutes, report)
    log.quality = report.as_dict()

    storage.set_meta(f"fetched:{inst.id}:{interval}", now.isoformat())
    if full_refresh:
        storage.set_meta(refresh_key, now.isoformat())
    return log
