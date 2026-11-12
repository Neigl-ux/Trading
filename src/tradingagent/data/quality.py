"""Datenqualität prüfen und bereinigen.

Fehler (severity="error") werden entfernt, Warnungen werden gemeldet und
in der App angezeigt, die Kerzen bleiben aber erhalten. Lieber eine
ehrliche Warnung als stillschweigend „reparierte" Daten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .sessions import session_for
from .timeframes import interval_delta


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    kind: str
    detail: str
    count: int = 1


@dataclass
class QualityReport:
    instrument: str
    interval: str
    rows_in: int = 0
    rows_out: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(i.count for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(i.count for i in self.issues if i.severity == "warning")

    def add(self, severity: str, kind: str, detail: str, count: int = 1) -> None:
        if count > 0:
            self.issues.append(Issue(severity, kind, detail, count))

    def as_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "interval": self.interval,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "issues": [i.__dict__ for i in self.issues],
        }


# Ab welcher Kursänderung pro Kerze eine Bewegung auffällig ist (zusätzlich
# zum robusten Statistik-Test). Krypto schwankt stärker als Aktien.
_JUMP_THRESHOLD = {
    ("crypto", "intraday"): 0.10,
    ("crypto", "daily"): 0.30,
    ("xetra", "intraday"): 0.06,
    ("xetra", "daily"): 0.20,
    ("europe", "intraday"): 0.06,
    ("europe", "daily"): 0.20,
    ("us", "intraday"): 0.06,
    ("us", "daily"): 0.20,
}


def check_and_clean(df: pd.DataFrame, instrument: str, interval: str, market: str,
                    jumps_after: pd.Timestamp | None = None) -> tuple[pd.DataFrame, QualityReport]:
    """Prüft die Struktur der Kerzen und entfernt fehlerhafte Zeilen.

    Kurssprünge werden nur für Kerzen nach `jumps_after` gemeldet, damit
    historische Ereignisse (z. B. Corona-Crash) nicht bei jedem Lauf erneut
    auftauchen. `None` = beim Erstbefüllen keine Sprungmeldungen.
    """
    report = QualityReport(instrument, interval, rows_in=len(df))
    if df.empty:
        return df, report

    dup = int(df.index.duplicated().sum())
    report.add("error", "duplikat", "Doppelte Zeitstempel entfernt", dup)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    bad_nan = df[["open", "high", "low", "close"]].isna().any(axis=1)
    report.add("error", "fehlende_werte", "Kerzen mit fehlenden Preisen entfernt", int(bad_nan.sum()))

    prices = df[["open", "high", "low", "close"]]
    bad_price = (prices <= 0).any(axis=1)
    report.add("error", "preis_ungueltig", "Kerzen mit Preis <= 0 entfernt", int((bad_price & ~bad_nan).sum()))

    bad_vol = df["volume"] < 0
    report.add("error", "volumen_negativ", "Kerzen mit negativem Volumen entfernt", int(bad_vol.sum()))

    tol = 1e-9 * df["close"].abs()
    body_hi = df[["open", "close"]].max(axis=1)
    body_lo = df[["open", "close"]].min(axis=1)
    bad_ohlc = (df["high"] + tol < body_hi) | (df["low"] - tol > body_lo) | (df["high"] + tol < df["low"])
    bad_ohlc &= ~bad_nan
    report.add("error", "ohlc_widerspruch", "Hoch/Tief passen nicht zu Eröffnung/Schluss", int(bad_ohlc.sum()))

    clean = df[~(bad_nan | bad_price | bad_vol | bad_ohlc)].copy()
    clean["volume"] = clean["volume"].fillna(0.0)
    report.rows_out = len(clean)

    if jumps_after is not None:
        _check_jumps(clean, report, interval, market, jumps_after)
    return clean, report


def _check_jumps(df: pd.DataFrame, report: QualityReport, interval: str, market: str,
                 after: pd.Timestamp) -> None:
    if len(df) < 20:
        return
    kind = "daily" if interval in ("1d", "1w", "2w", "1M") else "intraday"
    threshold = _JUMP_THRESHOLD.get((market, kind), 0.2)
    rets = np.log(df["close"]).diff().dropna()
    med = rets.median()
    mad = (rets - med).abs().median() * 1.4826
    if mad == 0 or np.isnan(mad):
        return
    z = (rets - med).abs() / mad
    jumps = rets[(z > 12) & (rets.abs() > threshold) & (rets.index > after)]
    if len(jumps):
        largest = jumps.abs().idxmax()
        report.add(
            "warning", "kurssprung",
            f"Auffällige Kursbewegung, größte {jumps[largest]:+.1%} am {largest:%d.%m.%Y %H:%M} UTC "
            "(echte Bewegung oder Datenfehler, bitte prüfen)",
            len(jumps),
        )


def check_gaps(df: pd.DataFrame, instrument: str, interval: str, market: str, report: QualityReport) -> None:
    """Fehlende Kerzen innerhalb der Handelszeiten melden.

    Tage ganz ohne Kerzen (Feiertage) zählen nicht als Lücke. Für Tageskerzen
    wird nur bei Krypto (jeden Tag Handel) geprüft.
    """
    if len(df) < 2:
        return
    session = session_for(market)
    if interval == "1d":
        if market != "crypto":
            return
        expected = pd.date_range(df.index[0], df.index[-1], freq="1D", tz="UTC")
    else:
        freq = {"5m": "5min", "15m": "15min", "1h": "1h"}.get(interval)
        if freq is None:
            return
        expected = session.expected_index(df.index[0], df.index[-1] + interval_delta(interval), freq)
        if session.open is not None:
            # nur Tage berücksichtigen, an denen überhaupt gehandelt wurde
            days_with_data = set(df.index.tz_convert(session.tz).date)
            local = expected.tz_convert(session.tz)
            expected = expected[[d in days_with_data for d in local.date]]
    missing = expected.difference(df.index)
    if len(expected) and len(missing):
        share = len(missing) / len(expected)
        severity = "warning" if share > 0.01 else "info"
        report.add(severity, "luecke", f"{len(missing)} fehlende Kerzen ({share:.1%} der erwarteten)", len(missing))


def check_freshness(last_ts: pd.Timestamp | None, interval: str, market: str, now: pd.Timestamp,
                    delay_minutes: int, report: QualityReport) -> None:
    """Warnt, wenn während der Handelszeit keine neuen Kerzen ankommen."""
    session = session_for(market)
    ref = now - pd.Timedelta(minutes=delay_minutes)
    if not session.is_open(ref) or last_ts is None or interval == "1d":
        return
    age = ref - (last_ts + interval_delta(interval))
    if age > 3 * interval_delta(interval) + pd.Timedelta(minutes=10):
        report.add("warning", "veraltet", f"Letzte Kerze ist {int(age.total_seconds() // 60)} Minuten alt")
