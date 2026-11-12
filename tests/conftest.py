from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from tradingagent.config import load_settings


def make_bars(start: str, periods: int, freq: str, price: float = 100.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC", name="ts")
    close = price * np.exp(np.cumsum(rng.normal(0, 0.002, periods)))
    open_ = np.r_[price, close[:-1]]
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    vol = rng.uniform(1, 10, periods)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


@pytest.fixture
def bars():
    return make_bars


@pytest.fixture
def settings(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text(yaml.safe_dump({
        "storage": {"path": str(tmp_path / "db.sqlite")},
        "export": {"dir": str(tmp_path / "site" / "data"), "encrypt": True},
        "markets": {
            "crypto": {"enabled": True, "providers": ["fakea", "fakeb"], "intervals": ["15m"], "daily_providers": ["fakeb"]},
            "xetra": {"enabled": True, "providers": ["fakeb"], "intervals": ["15m"], "daily_providers": ["fakeb"], "delay_minutes": 15},
        },
        "history": {"backfill_days": {"15m": 3, "1d": 400}, "daily_full_refresh_days": 7, "overlap_bars": 2},
    }))
    (cfg / "instruments.yaml").write_text(yaml.safe_dump({"instruments": [
        {"id": "BTC-EUR", "name": "Bitcoin", "market": "crypto", "fakea": "BTCEUR", "fakeb": "BTC-EUR"},
        {"id": "SAP", "name": "SAP", "market": "xetra", "fakeb": "SAP.DE"},
    ]}))
    return cfg, tmp_path
