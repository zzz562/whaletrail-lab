"""Window-local chip distribution (CYQ-style), computed from OHLC + turn.

Not East Money / Tongdaxin output.  Each scan window rebuilds the histogram
from that window's bars: decay existing chips by daily turnover, then add
today's mass as a triangle on [low, high] peaked at close.  Halt days
(``tradestatus == 0`` or missing/zero turn) neither decay nor add.

Histograms are L1-normalised onto a relative price axis ``[0, 1]`` so two
names can be compared by shape regardless of yuan price.  Unadjusted bars
(baostock ``adjustflag=3``) will smear across an ex-div gap; that is accepted
for a 90-day window and must not be papered over with a second qfq series.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

CHIP_BINS = 60
COVER_MASS = 0.80


def _as_float(series: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(series, dtype=float)


def _triangle_mass(
    centers: np.ndarray, low: float, high: float, close: float
) -> np.ndarray:
    """Unit-mass triangle on ``[low, high]`` with peak at ``close``."""
    w = np.zeros_like(centers)
    if not np.isfinite(low) or not np.isfinite(high) or not np.isfinite(close):
        return w
    if high < low:
        low, high = high, low
    close = min(max(close, low), high)
    if high - low <= 1e-12:
        idx = int(np.argmin(np.abs(centers - close)))
        w[idx] = 1.0
        return w
    if close > low:
        left = (centers >= low) & (centers <= close)
        w[left] = (centers[left] - low) / (close - low)
    if high > close:
        right = (centers > close) & (centers <= high)
        w[right] = (high - centers[right]) / (high - close)
    total = float(w.sum())
    if total <= 0:
        idx = int(np.argmin(np.abs(centers - close)))
        w[idx] = 1.0
        return w
    return w / total


def chip_histogram(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    turn: Sequence[float],
    tradestatus: Sequence[float] | None = None,
    n_bins: int = CHIP_BINS,
) -> tuple[np.ndarray, float, float]:
    """Build a window-local chip histogram.

    ``turn`` is baostock percent (0.632 means 0.632%), not a fraction.

    Returns
    -------
    hist, pmin, pmax
        ``hist`` has length *n_bins* on the relative axis of ``[pmin, pmax]``
        (window low/high).  Sums to 1 when any mass accumulated, else zeros.
    """
    h = _as_float(high)
    l = _as_float(low)
    c = _as_float(close)
    t = _as_float(turn)
    n = h.size
    if l.size != n or c.size != n or t.size != n:
        raise ValueError("high/low/close/turn must be the same length")
    if tradestatus is None:
        ts = np.ones(n, dtype=float)
    else:
        ts = _as_float(tradestatus)
        if ts.size != n:
            raise ValueError("tradestatus must match bar length")

    finite_price = np.isfinite(h) & np.isfinite(l)
    if not finite_price.any():
        return np.zeros(n_bins), 0.0, 0.0
    pmin = float(np.nanmin(l))
    pmax = float(np.nanmax(h))
    if pmax < pmin:
        pmin, pmax = pmax, pmin

    edges = np.linspace(pmin, pmax if pmax > pmin else pmin + 1e-9, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    chips = np.zeros(n_bins, dtype=float)

    for i in range(n):
        if not np.isfinite(ts[i]) or ts[i] == 0:
            continue
        turn_pct = t[i]
        if not np.isfinite(turn_pct) or turn_pct <= 0:
            continue
        r = float(turn_pct) * 0.01
        if r > 1.0:
            r = 1.0
        chips *= 1.0 - r
        chips += r * _triangle_mass(centers, float(l[i]), float(h[i]), float(c[i]))

    mass = float(chips.sum())
    if mass > 0:
        chips = chips / mass
    return chips, pmin, pmax


def wasserstein_1d(a: Sequence[float], b: Sequence[float]) -> float:
    """1-D Earth Mover's distance on a shared relative axis.

    Both series are L1-normalised.  Empty-vs-empty is 0; empty-vs-mass is 1
    (all mass moved across the unit interval).
    """
    x = _as_float(a)
    y = _as_float(b)
    if x.size == 0 or y.size == 0 or x.size != y.size:
        return float("inf")
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    sx, sy = float(x.sum()), float(y.sum())
    if sx <= 0 and sy <= 0:
        return 0.0
    if sx <= 0 or sy <= 0:
        return 1.0
    cdf_x = np.cumsum(x / sx)
    cdf_y = np.cumsum(y / sy)
    width = 1.0 / x.size
    return float(np.sum(np.abs(cdf_x - cdf_y)) * width)


def chip_stats(
    hist: Sequence[float],
    close: float,
    pmin: float,
    pmax: float,
    cover: float = COVER_MASS,
) -> tuple[float | None, float | None]:
    """Winner ratio (mass below *close*) and concentration (shortest width
    covering *cover* of mass, as a fraction of the window range)."""
    h = _as_float(hist)
    mass = float(h.sum())
    if mass <= 0 or not np.isfinite(close):
        return None, None
    h = h / mass
    if pmax <= pmin:
        return 1.0 if close >= pmin else 0.0, 1.0 / max(h.size, 1)

    rel = (close - pmin) / (pmax - pmin)
    rel = min(max(rel, 0.0), 1.0)
    n = h.size
    centers = (np.arange(n) + 0.5) / n
    winner = float(h[centers <= rel + 1e-12].sum())

    target = min(max(cover, 0.0), 1.0)
    csum = np.concatenate([[0.0], np.cumsum(h)])
    best = n
    j = 0
    for i in range(n):
        if j < i:
            j = i
        while j < n and (csum[j + 1] - csum[i]) < target - 1e-12:
            j += 1
        if j < n and (csum[j + 1] - csum[i]) >= target - 1e-12:
            best = min(best, j - i + 1)
    concentration = best / n
    return winner, concentration
