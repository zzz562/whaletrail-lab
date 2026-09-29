"""Distribution shape and the screen entry stay deterministic on synthetic bars."""

from __future__ import annotations

import numpy as np

from whaletrail.chips import chip_histogram, distribution_stats
from whaletrail.screen import describe_window, screen_similar, trim_partial_session


def _flat(n, price, turn=5.0):
    p = np.full(n, price, dtype=float)
    return p, p, p, np.full(n, turn, dtype=float)


def test_one_price_is_a_single_peak_on_the_close():
    high, low, close, turn = _flat(12, 10.0)
    hist, pmin, pmax = chip_histogram(high, low, close, turn)
    stats = distribution_stats(hist, pmin, pmax, close=10.0)
    assert stats is not None
    assert stats["n_peaks"] == 1
    assert stats["peaks"][0]["mass"] > 0.9
    assert abs(stats["peaks"][0]["vs_close"]) < 1e-6
    assert stats["std_over_close"] < 1e-6


def test_two_price_levels_can_leave_two_peaks():
    high = np.concatenate([np.full(30, 10.0), np.full(8, 20.0)])
    low = high.copy()
    close = high.copy()
    turn = np.full(high.size, 4.0)  # slow decay, the old pile is still a peak
    hist, pmin, pmax = chip_histogram(high, low, close, turn)
    stats = distribution_stats(hist, pmin, pmax, close=20.0)
    assert stats is not None
    assert stats["n_peaks"] >= 2
    prices = sorted(p["px"] for p in stats["peaks"])
    assert prices[0] < 12
    assert prices[-1] > 18


def test_trim_drops_a_partial_last_session():
    def bars(dates):
        n = len(dates)
        return {"trade_date": dates, "close": [1.0] * n}

    full = [f"2026-09-{d:02d}" for d in range(1, 6)]
    universe = {f"sh.{i:06d}": bars(full) for i in range(100)}
    for i in range(40):
        universe[f"sz.{i:06d}"] = bars(full + ["2026-09-06"])
    trimmed, dropped = trim_partial_session(universe)
    assert dropped == "2026-09-06"
    assert trimmed["sz.000000"]["trade_date"][-1] == "2026-09-05"
    assert trimmed["sh.000000"]["trade_date"][-1] == "2026-09-05"


def test_clone_of_the_template_ranks_first():
    rng = np.random.default_rng(0)
    n = 40
    steps = rng.normal(0, 1, n)
    close = 20 + np.cumsum(steps)
    high = close + 0.4
    low = close - 0.4
    turn = np.full(n, 3.0)
    volume = np.full(n, 1_000_000.0)
    template = {
        "open": close - 0.1, "high": high, "low": low, "close": close,
        "volume": volume, "turn": turn, "tradestatus": np.ones(n),
    }
    pool = {"template": template, "clone": {k: np.array(v, copy=True) for k, v in template.items()}}
    for i in range(6):
        noise = close + rng.normal(0, 3, n)
        pool[f"n{i}"] = {
            "open": noise, "high": noise + 0.5, "low": noise - 0.5, "close": noise,
            "volume": volume * (1 + i), "turn": turn, "tradestatus": np.ones(n),
        }
    hits, info = screen_similar(template, pool, recall_n=8)
    assert info["template_features"]["end_n_peaks"] is not None
    ranked = [h for h in hits if h.code != "template"]
    assert ranked[0].code == "clone"
    assert ranked[0].score < ranked[1].score
    assert describe_window(template)["end_n_peaks"] == describe_window(pool["clone"])["end_n_peaks"]
