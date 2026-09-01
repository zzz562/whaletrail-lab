"""Chart-shape similarity via Dynamic Time Warping (DTW).

Ported from the ValarmClub lab's "find similar charts" idea
(``models/data_updater.py::calculate_similarity``), re-implemented in pure
NumPy so WhaleTrail gains no new runtime dependency.

The workflow: pick a reference stock and a lookback window, then rank every
candidate by how closely its normalised close series matches the reference
over the same trailing window.  This is the "找相似走势" signal — complementary
to ``whaletrail.indicators.whale_flag``, which flags abnormal volume/price
moves on a single stock rather than cross-stock resemblance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def normalize(series: Sequence[float]) -> np.ndarray:
    """Min-max normalise *series* to ``[0, 1]``.

    A flat series normalises to zeros (``max == min``), matching ValarmClub's
    ``_normalize_series``.
    """
    arr = np.asarray(series, dtype=float)
    lo, hi = float(arr.min()), float(arr.max())
    if hi == lo:
        return np.zeros_like(arr)
    return (arr - lo) / (hi - lo)


def align_tails(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Truncate both series to the shorter length, keeping the most recent
    (tail) observations — same alignment rule as ValarmClub."""
    n = min(a.size, b.size)
    return a[-n:], b[-n:]


def dtw_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Classic unconstrained DTW distance with absolute point cost.

    Warp path starts at ``(0, 0)`` (boundary rows/columns are ``inf``).  This
    is a faithful re-implementation of ValarmClub's ``dtw.distance`` call in
    plain NumPy; the ranking semantics are preserved even though the absolute
    (rather than squared) point cost is used.

    For a whole-market scan ``dtaidistance``'s C backend can be dropped in
    later without changing callers — the function contract is the same.
    """
    a = [float(x) for x in a]
    b = [float(x) for x in b]
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")

    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = abs(ai - b[j - 1])
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[m]


def rank_similar(
    target: Sequence[float],
    candidates: Mapping[str, Sequence[float]],
    window: int | None = None,
) -> list[tuple[str, float]]:
    """Rank *candidates* by DTW similarity to *target* (ascending distance).

    Parameters
    ----------
    target:
        Close series of the reference stock (any length).
    candidates:
        Mapping of ``code -> close series`` for the universe to scan.
    window:
        If given, compare only the last *window* points of every series
        (tail-aligned), matching ValarmClub's default 90-day window.

    Returns
    -------
    list of ``(code, distance)`` sorted ascending — most similar first.
    """
    target = np.asarray(target, dtype=float)
    if window:
        target = target[-window:]

    results: list[tuple[str, float]] = []
    for code, series in candidates.items():
        s = np.asarray(series, dtype=float)
        if window:
            s = s[-window:]
        t, c = align_tails(target, s)
        if t.size < 2 or c.size < 2:
            continue  # too short to shape-match
        results.append((code, dtw_distance(normalize(t), normalize(c))))

    results.sort(key=lambda item: item[1])
    return results
