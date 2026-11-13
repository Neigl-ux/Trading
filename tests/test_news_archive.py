import io
import zipfile

import numpy as np
import pandas as pd

from tradingagent import news_archive as na


def _zip(rows):
    lines = []
    for quad, gold, art, tone, geo in rows:
        f = [""] * 58
        f[29], f[30], f[33], f[34], f[51] = str(quad), str(gold), str(art), str(tone), geo
        lines.append("\t".join(f))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20200101.export.CSV", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_aggregate_weights_by_articles():
    raw = _zip([(1, 2.0, 10, -1.0, "US"), (4, -8.0, 30, -5.0, "CH"), (2, 1.0, 10, 0.0, "GM")])
    a = na.aggregate(raw)
    assert a["n"] == 50
    assert abs(a["tone"] - (-10 - 150 + 0) / 50) < 1e-9
    assert abs(a["conflict"] - 30 / 50) < 1e-9
    assert abs(a["tone_us"] + 1.0) < 1e-9 and abs(a["tone_cn"] + 5.0) < 1e-9 and abs(a["tone_eu"]) < 1e-9
    assert a["conflict_cn"] == 1.0


def test_study_runs_and_signal_sign():
    idx = pd.bdate_range("2014-01-01", "2020-12-31")
    rng = np.random.default_rng(0)
    hist = pd.DataFrame({c: rng.normal(0, 1, len(idx)) for c in na.SIGNALS}, index=idx)
    px = pd.DataFrame({"close": 100 * np.exp(np.cumsum(rng.normal(3e-4, 0.01, len(idx))))}, index=idx.tz_localize("UTC"))
    res = na.study({"^GSPC": px}, hist)
    s = res["targets"]["^GSPC"]["signals"]["tone"]
    assert s["months"] > 36 and "Q5" in s["by_quintile"]["3M"]
    assert "Nachrichten-Rückblick" in na.report(res)
