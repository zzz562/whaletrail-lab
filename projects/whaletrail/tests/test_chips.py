from __future__ import annotations

import numpy as np

from whaletrail.chips import chip_histogram, chip_stats, wasserstein_1d


def _const(n, price, turn, status=1):
    p = np.full(n, price, dtype=float)
    return p, p, p, np.full(n, turn, dtype=float), np.full(n, status, dtype=float)


def test_limit_lock_all_mass_in_one_bin():
    h, l, c, t, ts = _const(10, 10.0, 5.0)
    hist, pmin, pmax = chip_histogram(h, l, c, t, ts)
    assert hist.sum() == 1.0
    assert abs(pmin - pmax) < 1e-9
    assert hist.max() == 1.0
    assert int(hist.argmax()) >= 0


def test_decay_moves_mass_to_new_price():
    n = 20
    high = np.concatenate([np.full(n, 10.0), np.full(n, 20.0)])
    low = high.copy()
    close = high.copy()
    turn = np.full(2 * n, 20.0)  # 20% a day — old peak should nearly vanish
    hist, pmin, pmax = chip_histogram(high, low, close, turn)
    assert pmin == 10.0 and pmax == 20.0
    n_bins = hist.size
    old_bin = 0
    new_bin = n_bins - 1
    assert hist[new_bin] > hist[old_bin]
    assert hist[new_bin] > 0.5


def test_halt_day_does_not_change_histogram():
    high = np.array([10.0, 10.0, 12.0])
    low = np.array([10.0, 10.0, 12.0])
    close = high.copy()
    turn = np.array([10.0, np.nan, 0.0])
    ts = np.array([1.0, 0.0, 1.0])
    # Compare against two trading days only (skip the halt in the middle).
    hist_halt, _, _ = chip_histogram(high, low, close, turn, ts)
    hist_skip, _, _ = chip_histogram(
        high[[0, 2]], low[[0, 2]], close[[0, 2]], turn[[0, 2]], ts[[0, 2]]
    )
    assert np.allclose(hist_halt, hist_skip)


def test_wasserstein_identical_zero_shift_increases():
    a = np.zeros(60)
    a[10] = 1.0
    b = a.copy()
    c = np.zeros(60)
    c[20] = 1.0
    d = np.zeros(60)
    d[40] = 1.0
    assert wasserstein_1d(a, b) == 0.0
    assert wasserstein_1d(a, c) > 0
    assert wasserstein_1d(a, d) > wasserstein_1d(a, c)


def test_relative_axis_scale_invariant():
    n = 15
    high = np.linspace(10, 12, n)
    low = high - 0.2
    close = (high + low) / 2
    turn = np.full(n, 3.0)
    h1, _, _ = chip_histogram(high, low, close, turn)
    h2, _, _ = chip_histogram(high * 2, low * 2, close * 2, turn)
    assert np.allclose(h1, h2, atol=1e-9)


def test_chip_stats_winner_and_concentration():
    hist = np.zeros(10)
    hist[0] = 1.0
    winner, conc = chip_stats(hist, close=1.0, pmin=0.0, pmax=10.0)
    assert winner == 1.0
    assert conc == 0.1
    hist2 = np.zeros(10)
    hist2[-1] = 1.0
    winner2, _ = chip_stats(hist2, close=1.0, pmin=0.0, pmax=10.0)
    assert winner2 == 0.0
