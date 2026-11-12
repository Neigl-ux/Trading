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


def _should_skip(storage: Storage, inst: Instrument, interval: str, cfg: MarketConfig, now: pd.Timestamp,
                 min_refresh: int = 0) -> str | None:
    """Abruf auslassen, wenn er gerade nichts Neues bringen kann."""
    last_fetch = storage.get_meta(f"fetched:{inst.id}:{interval}")
    if last_fetch and min_refresh and now - pd.Timestamp(last_fetch) < pd.Timedelta(minutes=min_refresh):
        return "kürzlich abgerufen"
    session = session_for(inst.market)
    if session.open is None or interval == "1d":
        return None
    ref = now - pd.Timedelta(minutes=cfg.delay_minutes)
    if session.is_open(ref) or session.is_open(now):
        return None
    close = _last_session_close(inst.market, now)
    if last_fetch and close is not None:
        if pd.Timestamp(last_fetch) >= close + pd.Timedelta(minutes=cfg.delay_minutes + 15):
            return "Markt geschlossen, Daten aktuell"
    return None


@dataclass
class Job:
    inst: Instrument
    interval: str
    cfg: MarketConfig
    chain: list[str]
    start: pd.Timestamp | None = None
    last: pd.Timestamp | None = None
    full_refresh: bool = False
    skip: str | None = None


def _plan(settings: Settings, storage: Storage, inst: Instrument, interval: str, cfg: MarketConfig,
          now: pd.Timestamp) -> Job:
    chain = cfg.daily_providers if interval == "1d" else cfg.providers
    job = Job(inst, interval, cfg, chain)
    full_refresh = False
    if interval == "1d":
        last_refresh = storage.get_meta(f"daily_refresh:{inst.id}")
        full_refresh = last_refresh is None or now - pd.Timestamp(last_refresh) > timedelta(days=settings.daily_full_refresh_days)
    if not full_refresh:
        job.skip = _should_skip(storage, inst, interval, cfg, now, settings.min_refresh_minutes.get(interval, 0))
        if job.skip:
            return job
    step = interval_delta(interval)
    job.last = storage.last_ts(inst.id, interval)
    job.full_refresh = full_refresh
    if job.last is None or full_refresh:
        job.start = now - timedelta(days=settings.backfill_days.get(interval, 30))
    else:
        job.start = job.last - step * settings.overlap_bars
    return job


def _prefetch(jobs: list[Job], providers: dict[str, MarketDataProvider], now: pd.Timestamp) -> None:
    """Werte, deren erste Quelle Bündelabrufe kann (Yahoo), gemeinsam holen."""
    groups: dict[tuple[str, str, bool], list[Job]] = {}
    for job in jobs:
        if job.skip or not job.chain:
            continue
        name = job.chain[0]
        provider = providers.get(name)
        if provider is None or not hasattr(provider, "prefetch") or job.inst.symbol_for(name) is None:
            continue
        backfill = job.last is None or job.full_refresh
        groups.setdefault((name, job.interval, backfill), []).append(job)
    for (name, interval, _), group in groups.items():
        start = min(j.start for j in group)
        providers[name].prefetch([j.inst.symbol_for(name) for j in group], interval, start, now)


def collect(settings: Settings, storage: Storage, providers: dict[str, MarketDataProvider],
            now: pd.Timestamp | None = None) -> RunSummary:
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC")
    summary = RunSummary(started=now)

    jobs: list[Job] = []
    for market_name, cfg in settings.markets.items():
        if not cfg.enabled:
            continue
        intervals = list(dict.fromkeys([*cfg.intervals, "1d"]))
        for inst in settings.instruments_for(market_name):
            for interval in intervals:
                jobs.append(_plan(settings, storage, inst, interval, cfg, now))

    _prefetch(jobs, providers, now)
    for job in jobs:
        summary.logs.append(_run(settings, storage, providers, job, now))

    storage.log_run(summary.as_dict())
    return summary


def _run(settings: Settings, storage: Storage, providers: dict[str, MarketDataProvider], job: Job,
         now: pd.Timestamp) -> FetchLog:
    inst, interval, cfg = job.inst, job.interval, job.cfg
    log = FetchLog(inst.id, interval)
    if job.skip:
        log.skipped = job.skip
        return log

    step = interval_delta(interval)
    last, start = job.last, job.start
    result = fetch_with_fallback(inst, interval, start, now, job.chain, providers)
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
    if job.full_refresh:
        storage.set_meta(f"daily_refresh:{inst.id}", now.isoformat())
    return log
