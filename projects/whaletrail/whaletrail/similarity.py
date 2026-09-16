"""Chart-shape similarity via Dynamic Time Warping (DTW) plus fused ranking.

Ported from the ValarmClub lab's "find similar charts" idea
(``models/data_updater.py::calculate_similarity``), re-implemented in pure
NumPy so WhaleTrail gains no new runtime dependency.

Close series use DTW on min-max normalised windows.  Turnover uses
mean-absolute distance on the same aligned window — unconstrained DTW would
warp a volume spike on day 5 onto one on day 30 and call them identical.
Chip histograms (see ``whaletrail.chips``) use 1-D Wasserstein.  ``rank_multi``
combines channels by percentile-within-universe so the raw distance scales
never mix.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from whaletrail.chips import chip_histogram, chip_stats, wasserstein_1d

DEFAULT_WEIGHTS: dict[str, float] = {"kline": 0.50, "volume": 0.30, "chip": 0.20}


@dataclass(frozen=True)
class Match:
    """One fused-ranking row.  Distances are raw; ``pct_*`` are 0=most similar."""

    code: str
    fused: float
    d_kline: float
    d_vol: float | None
    d_chip: float | None
    pct_kline: float
    pct_vol: float | None
    pct_chip: float | None
    winner_ratio: float | None = None
    concentration: float | None = None


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


def _prep_pair(
    a: Sequence[float], b: Sequence[float], window: int | None = None
) -> tuple[np.ndarray, np.ndarray] | None:
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    if window:
        x, y = x[-window:], y[-window:]
    t, c = align_tails(x, y)
    if t.size < 2 or c.size < 2:
        return None
    t = np.nan_to_num(t, nan=0.0)
    c = np.nan_to_num(c, nan=0.0)
    return t, c


def pair_dtw(
    a: Sequence[float], b: Sequence[float], window: int | None = None
) -> float:
    """Min-max DTW distance of two series, tail-aligned to *window*."""
    pair = _prep_pair(a, b, window)
    if pair is None:
        return float("inf")
    t, c = pair
    return dtw_distance(normalize(t), normalize(c))


def pair_l1(
    a: Sequence[float], b: Sequence[float], window: int | None = None
) -> float:
    """Mean |Δ| of min-max series, same days (no time warp)."""
    pair = _prep_pair(a, b, window)
    if pair is None:
        return float("inf")
    t, c = pair
    return float(np.mean(np.abs(normalize(t) - normalize(c))))


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
    results: list[tuple[str, float]] = []
    for code, series in candidates.items():
        dist = pair_dtw(target, series, window=window)
        if not np.isfinite(dist):
            continue
        results.append((code, dist))
    results.sort(key=lambda item: item[1])
    return results


def normalize_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    """Drop non-positive weights and renormalise to sum 1.  Empty → kline only."""
    src = dict(DEFAULT_WEIGHTS if weights is None else weights)
    active = {k: float(v) for k, v in src.items() if float(v) > 0}
    total = sum(active.values())
    if total <= 0:
        return {"kline": 1.0}
    return {k: v / total for k, v in active.items()}


def _tail_slice(values: Sequence[float] | np.ndarray, window: int | None) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if window:
        return arr[-window:]
    return arr


def _col(bars: Mapping[str, Sequence[float]], key: str, window: int | None) -> np.ndarray | None:
    if key not in bars:
        return None
    return _tail_slice(bars[key], window)


def _volume_series(bars: Mapping[str, Sequence[float]], window: int | None) -> np.ndarray | None:
    """Prefer turn (halt → 0).  Volume only when the whole turn column is missing."""
    turn = _col(bars, "turn", window)
    if turn is not None:
        out = np.array(turn, dtype=float, copy=True)
        out[~np.isfinite(out)] = 0.0
        return out
    vol = _col(bars, "volume", window)
    if vol is None:
        return None
    out = np.array(vol, dtype=float, copy=True)
    out[~np.isfinite(out)] = 0.0
    return out


def _has_turn_mass(bars: Mapping[str, Sequence[float]], window: int | None) -> bool:
    turn = _col(bars, "turn", window)
    if turn is None:
        return False
    ts = _col(bars, "tradestatus", window)
    ok = np.isfinite(turn) & (turn > 0)
    if ts is not None:
        ok = ok & (ts != 0)
    return bool(ok.any())


def _last_is_st(bars: Mapping[str, Sequence[float]], window: int | None) -> bool:
    st = _col(bars, "is_st", window)
    if st is None or st.size == 0:
        return False
    val = st[-1]
    return bool(np.isfinite(val) and val == 1)


def _percentiles(values: Sequence[float]) -> np.ndarray:
    """0 = smallest finite (most similar), 1 = largest / non-finite."""
    arr = np.asarray(values, dtype=float)
    out = np.ones(arr.size, dtype=float)
    finite = np.isfinite(arr)
    if not finite.any():
        return out
    sub = arr[finite]
    if float(sub.max()) == float(sub.min()):
        out[finite] = 0.0
        return out
    order = np.argsort(sub, kind="mergesort")
    ranks = np.empty(sub.size, dtype=float)
    ranks[order] = np.arange(sub.size, dtype=float)
    out[finite] = ranks / max(sub.size - 1, 1)
    return out


def _chip_pack(
    bars: Mapping[str, Sequence[float]], window: int | None
) -> tuple[np.ndarray | None, float | None, float | None]:
    high = _col(bars, "high", window)
    low = _col(bars, "low", window)
    close = _col(bars, "close", window)
    turn = _col(bars, "turn", window)
    if high is None or low is None or close is None or turn is None:
        return None, None, None
    ts = _col(bars, "tradestatus", window)
    hist, pmin, pmax = chip_histogram(high, low, close, turn, ts)
    if float(hist.sum()) <= 0:
        return None, None, None
    return hist, pmin, pmax


def rank_multi(
    target: Mapping[str, Sequence[float]],
    candidates: Mapping[str, Mapping[str, Sequence[float]]],
    window: int | None = None,
    weights: Mapping[str, float] | None = None,
    exclude_st: bool = True,
) -> tuple[list[Match], dict[str, float]]:
    """Rank *candidates* by fused kline / volume / chip resemblance.

    Each candidate mapping holds aligned columns (``close`` required;
    ``high`` / ``low`` / ``turn`` / ``tradestatus`` / ``is_st`` / ``volume``
    optional).  Channels with weight 0 are not computed.  Chip is dropped
    (and weights renormalised) when the reference has no usable turnover.

    Returns ``(matches, used_weights)``.  Matches are sorted by fused
    percentile score, then kline distance.
    """
    w = normalize_weights(weights)
    t_close = _col(target, "close", window)
    if t_close is None or t_close.size < 2:
        return [], w

    want_vol = "volume" in w
    want_chip = "chip" in w
    if want_chip and not _has_turn_mass(target, window):
        want_chip = False
        w = normalize_weights({k: (0.0 if k == "chip" else v) for k, v in w.items()})

    t_vol = _volume_series(target, window) if want_vol else None
    t_chip = None
    if want_chip:
        t_chip, _, _ = _chip_pack(target, window)
        if t_chip is None:
            want_chip = False
            w = normalize_weights({k: (0.0 if k == "chip" else v) for k, v in w.items()})

    rows: list[dict] = []
    for code, bars in candidates.items():
        if exclude_st and _last_is_st(bars, window):
            continue
        close = _col(bars, "close", window)
        if close is None or close.size < 2:
            continue
        d_k = pair_dtw(t_close, close, window=None)
        if not np.isfinite(d_k):
            continue
        rec: dict = {"code": code, "d_kline": d_k, "d_vol": None, "d_chip": None,
                     "winner_ratio": None, "concentration": None}
        if want_vol and t_vol is not None:
            v = _volume_series(bars, window)
            rec["d_vol"] = pair_l1(t_vol, v, window=None) if v is not None else float("inf")
        if want_chip and t_chip is not None:
            hist, pmin, pmax = _chip_pack(bars, window)
            if hist is None:
                rec["d_chip"] = float("inf")
            else:
                rec["d_chip"] = wasserstein_1d(t_chip, hist)
                rec["winner_ratio"], rec["concentration"] = chip_stats(
                    hist, float(close[-1]), pmin, pmax
                )
        rows.append(rec)

    if not rows:
        return [], w

    pct_k = _percentiles([r["d_kline"] for r in rows])
    pct_v = (
        _percentiles([r["d_vol"] if r["d_vol"] is not None else float("inf") for r in rows])
        if want_vol
        else None
    )
    pct_c = (
        _percentiles([r["d_chip"] if r["d_chip"] is not None else float("inf") for r in rows])
        if want_chip
        else None
    )

    matches: list[Match] = []
    wk = w.get("kline", 0.0)
    wv = w.get("volume", 0.0) if want_vol else 0.0
    wc = w.get("chip", 0.0) if want_chip else 0.0
    for i, rec in enumerate(rows):
        fused = wk * float(pct_k[i])
        pv = float(pct_v[i]) if pct_v is not None else None
        pc = float(pct_c[i]) if pct_c is not None else None
        if pv is not None:
            fused += wv * pv
        if pc is not None:
            fused += wc * pc
        matches.append(
            Match(
                code=rec["code"],
                fused=fused,
                d_kline=rec["d_kline"],
                d_vol=rec["d_vol"],
                d_chip=rec["d_chip"],
                pct_kline=float(pct_k[i]),
                pct_vol=pv,
                pct_chip=pc,
                winner_ratio=rec["winner_ratio"],
                concentration=rec["concentration"],
            )
        )
    matches.sort(key=lambda m: (m.fused, m.d_kline, m.code))
    return matches, w
