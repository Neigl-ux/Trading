"""Einstellungen und Instrumente aus den YAML-Dateien laden."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@dataclass(frozen=True)
class Instrument:
    """Ein beobachtetes Wertpapier oder eine Kryptowährung."""

    id: str
    name: str
    market: str  # "crypto" oder "xetra"
    symbols: dict[str, str] = field(default_factory=dict)  # Anbieter -> Symbol
    unit: str = "€"  # Indizes: "Punkte", US-Aktien: "$"
    kind: str = "aktie"  # aktie | etf | index | krypto

    def symbol_for(self, provider: str) -> str | None:
        return self.symbols.get(provider)


@dataclass(frozen=True)
class MarketConfig:
    name: str
    enabled: bool
    providers: list[str]
    intervals: list[str]
    daily_providers: list[str]
    delay_minutes: int = 0


@dataclass(frozen=True)
class Settings:
    storage_path: Path
    export_dir: Path
    encrypt: bool
    markets: dict[str, MarketConfig]
    backfill_days: dict[str, int]
    daily_full_refresh_days: int
    overlap_bars: int
    instruments: list[Instrument]
    min_refresh_minutes: dict[str, int] = field(default_factory=dict)

    def instruments_for(self, market: str) -> list[Instrument]:
        return [i for i in self.instruments if i.market == market]


# Alle anderen Schlüssel eines Instruments gelten als Anbieter-Symbol (kraken, binance, yahoo, ...).
_RESERVED_KEYS = ("id", "name", "market", "unit", "kind")


def _resolve(path: str, root: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p


def load_settings(config_dir: Path | None = None, root: Path | None = None) -> Settings:
    """Liest settings.yaml und instruments.yaml.

    Umgebungsvariablen können Pfade überschreiben (praktisch auf dem Server):
    TA_STORAGE_PATH, TA_EXPORT_DIR, TA_ENCRYPT (true/false).
    """
    config_dir = config_dir or CONFIG_DIR
    root = root or ROOT
    raw: dict[str, Any] = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    inst_raw: dict[str, Any] = yaml.safe_load((config_dir / "instruments.yaml").read_text(encoding="utf-8"))

    markets = {}
    for name, m in raw["markets"].items():
        markets[name] = MarketConfig(
            name=name,
            enabled=bool(m.get("enabled", True)),
            providers=list(m.get("providers", [])),
            intervals=list(m.get("intervals", [])),
            daily_providers=list(m.get("daily_providers", m.get("providers", []))),
            delay_minutes=int(m.get("delay_minutes", 0)),
        )

    instruments = []
    seen = set()
    for item in inst_raw["instruments"]:
        if item["id"] in seen:
            raise ValueError(f"Instrument doppelt definiert: {item['id']}")
        seen.add(item["id"])
        if item["market"] not in markets:
            raise ValueError(f"Unbekannter Markt '{item['market']}' bei {item['id']}")
        instruments.append(
            Instrument(
                id=item["id"],
                name=item.get("name", item["id"]),
                market=item["market"],
                symbols={k: str(v) for k, v in item.items() if k not in _RESERVED_KEYS},
                unit=str(item.get("unit", "€")),
                kind=str(item.get("kind", "aktie")),
            )
        )

    storage_path = os.environ.get("TA_STORAGE_PATH") or raw["storage"]["path"]
    export_dir = os.environ.get("TA_EXPORT_DIR") or raw["export"]["dir"]
    encrypt_env = os.environ.get("TA_ENCRYPT")
    encrypt = raw["export"].get("encrypt", True) if encrypt_env is None else encrypt_env.lower() == "true"

    hist = raw.get("history", {})
    return Settings(
        storage_path=_resolve(storage_path, root),
        export_dir=_resolve(export_dir, root),
        encrypt=bool(encrypt),
        markets=markets,
        backfill_days={k: int(v) for k, v in hist.get("backfill_days", {}).items()},
        daily_full_refresh_days=int(hist.get("daily_full_refresh_days", 7)),
        overlap_bars=int(hist.get("overlap_bars", 3)),
        min_refresh_minutes={k: int(v) for k, v in hist.get("min_refresh_minutes", {}).items()},
        instruments=instruments,
    )
