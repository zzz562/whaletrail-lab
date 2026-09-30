"""Similar-stock screen: waveform recall, then a weighted feature distance.

The public entry is :func:`screen_similar`.  The dashboard and
``scripts/similar-screen.py`` both call it.  Recall is close-price DTW.
Inside that pool, names are ordered by how close their campaign, chip
distribution, volume, box and moving-average readings are to the template.
Nothing is dropped for missing a single reading.

Chip snapshots are rebuilt at scan time from the bars already in memory
(the A-peak day, the trough day, and the last day).  No chip table.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from whaletrail.chips import chip_histogram, distribution_stats
from whaletrail.similarity import _last_is_st, pair_corr, pair_dtw

DEFAULT_SCREEN_RECALL = 500

# Group weights sum to 1.  Chip distribution is the stage reading;
# waveform similarity is already spent on recall and is not in here.
WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "偏筹码": {
        "chip": 0.50, "campaign": 0.15, "volume": 0.15,
        "box": 0.10, "confirm": 0.05, "ma": 0.05,
    },
    "均衡": {
        "chip": 0.30, "campaign": 0.20, "volume": 0.20,
        "box": 0.15, "confirm": 0.10, "ma": 0.05,
    },
    "偏确认": {
        "chip": 0.25, "campaign": 0.15, "volume": 0.30,
        "box": 0.10, "confirm": 0.15, "ma": 0.05,
    },
}

DEMOS: dict[str, dict[str, str]] = {
    "yuandong": {
        "title": "远东股份",
        "symbol": "sh.600869",
        "start": "2026-02-20",
        "end": "2026-08-20",
    },
    "chaosheng": {
        "title": "超声电子",
        "symbol": "sz.000823",
        "start": "2026-03-07",
        "end": "2026-09-07",
    },
}

_GROUPS: dict[str, tuple[str, ...]] = {
    "chip": (
        "end_n_peaks", "end_std", "end_skew", "end_mass_below", "end_top_vs", "end_top_mass",
        "peak_n_peaks", "peak_std", "peak_skew", "peak_mass_below",
        "trough_n_peaks", "trough_std", "trough_mass_below",
    ),
    "campaign": ("drop", "speed", "run", "recovery"),
    "volume": ("vol_climax", "vol_ma20", "turn"),
    "box": ("box_width", "chop", "er", "box_pos"),
    "confirm": ("ret1", "close_loc"),
    "ma": ("ma7", "ma30", "ma55", "ma120", "all_under"),
}


@dataclass
class ScreenHit:
    """One recalled name.  ``score`` is distance to the template; lower is closer."""

    code: str
    score: float
    recall_rank: int
    delta: int | None
    d_kline: float
    close_corr: float | None
    groups: dict[str, float | None]
    features: dict[str, float | None]
    stage: str
    group_weights: dict[str, float] = field(default_factory=dict)


def trim_partial_session(
    bars: Mapping[str, Mapping[str, Sequence]],
    min_frac: float = 0.8,
) -> tuple[dict[str, dict], str | None]:
    """Drop the newest date when only part of the universe has a bar.

    Compares that date's name count with the median of the few sessions
    before it.  Returns the trimmed mapping and the dropped date, or the
    original mapping and None.
    """
    counts: Counter[str] = Counter()
    for rec in bars.values():
        for raw in rec.get("trade_date") or []:
            counts[str(raw)[:10]] += 1
    if len(counts) < 6:
        return dict(bars), None
    ordered = sorted(counts)
    last = ordered[-1]
    prior = [counts[d] for d in ordered[-6:-1]]
    typical = float(np.median(prior))
    if typical <= 0 or counts[last] >= min_frac * typical:
        return dict(bars), None
    out: dict[str, dict] = {}
    for code, rec in bars.items():
        dates = [str(d)[:10] for d in rec.get("trade_date") or []]
        if not dates or dates[-1] != last:
            out[code] = dict(rec)
            continue
        cut = {}
        width = len(dates)
        for key, values in rec.items():
            if isinstance(values, list) and len(values) == width:
                cut[key] = values[:-1]
            else:
                cut[key] = values
        out[code] = cut
    return out, last


def _num(series: Sequence[float] | None) -> np.ndarray | None:
    if series is None:
        return None
    arr = np.asarray(series, dtype=float)
    return arr if arr.size else None


def _ma_gap(close: np.ndarray, n: int) -> float | None:
    if close.size < n:
        return None
    window = close[-n:]
    window = window[np.isfinite(window)]
    if window.size < n or not np.isfinite(close[-1]) or close[-1] <= 0:
        return None
    mean = float(window.mean())
    if mean <= 0:
        return None
    return float(close[-1] / mean - 1.0)


def _choppiness(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float | None:
    n = close.size
    if n < 3:
        return None
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    span = float(np.nanmax(high) - np.nanmin(low))
    if not np.isfinite(span) or span <= 0:
        return None
    return float(100.0 * np.log10(float(np.nansum(tr)) / span) / np.log10(n))


def _efficiency(close: np.ndarray) -> float | None:
    if close.size < 2:
        return None
    denom = float(np.nansum(np.abs(np.diff(close))))
    if denom <= 0 or not np.isfinite(close[0]) or not np.isfinite(close[-1]):
        return None
    return float(abs(close[-1] - close[0]) / denom)


def _down_run(close: np.ndarray, i0: int, i1: int) -> int:
    best = run = 0
    for k in range(i0 + 1, i1 + 1):
        if np.isfinite(close[k]) and np.isfinite(close[k - 1]) and close[k] < close[k - 1]:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _swing_highs(high: np.ndarray, order: int = 5) -> list[int]:
    n = high.size
    found: list[int] = []
    i = order
    while i < n - order:
        window = high[i - order : i + order + 1]
        if np.isfinite(high[i]) and high[i] >= np.nanmax(window) - 1e-9:
            found.append(i)
            i += order
        else:
            i += 1
    return found


def _campaign(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> dict | None:
    """Largest prior swing drop whose trough finishes before the last 5 bars."""
    n = high.size
    best: dict | None = None
    for i in _swing_highs(high):
        j1 = min(n - 6, i + 60)
        if j1 <= i + 3:
            continue
        seg = low[i : j1 + 1]
        if not np.isfinite(seg).any():
            continue
        j = i + int(np.nanargmin(seg))
        span = int(j - i)
        if span < 1 or not np.isfinite(high[i]) or high[i] <= 0 or not np.isfinite(low[j]):
            continue
        drop = float((high[i] - low[j]) / high[i])
        pre_lo = max(0, i - 40)
        pre = low[pre_lo : i + 1]
        base = float(np.nanmin(pre)) if np.isfinite(pre).any() else np.nan
        rise = float(high[i] / base - 1.0) if base and base > 0 else None
        a = max(0, i - 20)
        b = min(n - 6, i + 5)
        if b < a:
            b = i
        climax = volume[a : b + 1]
        climax_vol = None
        if climax.size and np.isfinite(climax).any():
            k = a + int(np.nanargmax(np.nan_to_num(climax, nan=-1.0)))
            if np.isfinite(volume[k]) and volume[k] > 0:
                climax_vol = float(volume[k])
        rec = {
            "i": i,
            "j": j,
            "drop": drop,
            "span": span,
            "run": _down_run(close, i, j),
            "speed": drop / span,
            "rise": rise,
            "trough": float(low[j]),
            "peak_high": float(high[i]),
            "climax_vol": climax_vol,
        }
        if best is None or drop > best["drop"]:
            best = rec
    if best is None:
        return None
    last = float(close[-1])
    span_px = best["peak_high"] - best["trough"]
    best["recovery"] = float((last - best["trough"]) / span_px) if span_px > 0 else None
    return best


def _chip_at(high, low, close, turn, ts, end: int) -> dict | None:
    if end < 2:
        return None
    sl = slice(0, end + 1)
    status = None if ts is None else ts[sl]
    hist, pmin, pmax = chip_histogram(high[sl], low[sl], close[sl], turn[sl], status)
    if float(hist.sum()) <= 0 or not np.isfinite(close[end]):
        return None
    return distribution_stats(hist, pmin, pmax, float(close[end]))


def _flat_chip(prefix: str, stats: dict | None) -> dict[str, float | None]:
    if not stats:
        return {
            f"{prefix}_n_peaks": None,
            f"{prefix}_std": None,
            f"{prefix}_skew": None,
            f"{prefix}_mass_below": None,
            f"{prefix}_top_vs": None,
            f"{prefix}_top_mass": None,
            f"{prefix}_kurt": None,
        }
    top = stats["peaks"][0] if stats["peaks"] else None
    return {
        f"{prefix}_n_peaks": float(stats["n_peaks"]),
        f"{prefix}_std": stats["std_over_close"],
        f"{prefix}_skew": stats["skew"],
        f"{prefix}_mass_below": stats["mass_below_close"],
        f"{prefix}_top_vs": None if top is None else top["vs_close"],
        f"{prefix}_top_mass": None if top is None else top["mass"],
        f"{prefix}_kurt": stats["kurt"],
    }


def describe_window(bars: Mapping[str, Sequence[float]]) -> dict[str, float | None]:
    """Feature vector for one already-sliced window.  Missing readings are None."""
    high = _num(bars.get("high"))
    low = _num(bars.get("low"))
    close = _num(bars.get("close"))
    volume = _num(bars.get("volume"))
    turn = _num(bars.get("turn"))
    empty: dict[str, float | None] = {}
    if high is None or low is None or close is None or close.size < 10:
        return empty
    if volume is None:
        volume = np.zeros(close.size)
    if turn is None:
        turn = np.zeros(close.size)
    ts = _num(bars.get("tradestatus"))
    camp = _campaign(high, low, close, volume)
    end = _chip_at(high, low, close, turn, ts, close.size - 1) if turn is not None else None
    feat: dict[str, float | None] = {}
    feat.update(_flat_chip("end", end))
    if camp is None:
        for key in ("drop", "speed", "run", "recovery", "rise"):
            feat[key] = None
        feat.update(_flat_chip("peak", None))
        feat.update(_flat_chip("trough", None))
        box_from = max(0, close.size - 20)
        feat["vol_climax"] = None
    else:
        feat["drop"] = camp["drop"]
        feat["speed"] = camp["speed"]
        feat["run"] = float(camp["run"])
        feat["recovery"] = camp["recovery"]
        feat["rise"] = camp["rise"]
        feat.update(_flat_chip("peak", _chip_at(high, low, close, turn, ts, camp["i"])))
        feat.update(_flat_chip("trough", _chip_at(high, low, close, turn, ts, camp["j"])))
        box_from = camp["j"]
        last_vol = float(volume[-1]) if np.isfinite(volume[-1]) else None
        feat["vol_climax"] = (
            last_vol / camp["climax_vol"]
            if last_vol and camp["climax_vol"]
            else None
        )
    feat["ret1"] = (
        float(close[-1] / close[-2] - 1.0)
        if close.size > 1 and np.isfinite(close[-2]) and close[-2] > 0 and np.isfinite(close[-1])
        else None
    )
    day_span = float(high[-1] - low[-1]) if np.isfinite(high[-1]) and np.isfinite(low[-1]) else 0.0
    open_ = _num(bars.get("open"))
    feat["close_loc"] = (
        float((close[-1] - low[-1]) / day_span) if day_span > 0 and np.isfinite(close[-1]) else None
    )
    feat["body"] = (
        float((close[-1] - open_[-1]) / day_span)
        if open_ is not None and open_.size == close.size and day_span > 0
        else None
    )
    feat["turn"] = float(turn[-1]) if np.isfinite(turn[-1]) else None

    def _ratio(series: np.ndarray, n: int) -> float | None:
        if series.size < n + 1 or not np.isfinite(series[-1]) or series[-1] <= 0:
            return None
        base = series[-(n + 1) : -1]
        base = base[np.isfinite(base) & (base > 0)]
        if base.size < max(3, n // 2):
            return None
        return float(series[-1] / float(base.mean()))

    feat["vol_ma20"] = _ratio(volume, 20)
    seg = slice(box_from, None)
    hh, ll, cc = high[seg], low[seg], close[seg]
    span = float(np.nanmax(hh) - np.nanmin(ll)) if hh.size else 0.0
    feat["box_width"] = span / float(close[-1]) if span > 0 and close[-1] > 0 else None
    feat["chop"] = _choppiness(hh, ll, cc)
    feat["er"] = _efficiency(cc)
    feat["box_pos"] = (
        float((close[-1] - np.nanmin(ll)) / span) if span > 0 and np.isfinite(close[-1]) else None
    )
    for n in (7, 30, 55, 120):
        feat[f"ma{n}"] = _ma_gap(close, n)
    gaps = [feat[f"ma{n}"] for n in (7, 30, 55, 120)]
    feat["all_under"] = 1.0 if all(g is not None and g < 0 for g in gaps) else 0.0
    return feat


def window_brief(bars: Mapping[str, Sequence[float]]) -> dict:
    """Readable fingerprint of one window.  Not used in the score.

    Dates and chip-peak prices are for the booklet and the CLI.  The ranker
    only sees the flat readings from :func:`describe_window`.
    """
    feat = describe_window(bars)
    dates = [str(d)[:10] for d in (bars.get("trade_date") or [])]
    high = _num(bars.get("high"))
    low = _num(bars.get("low"))
    close = _num(bars.get("close"))
    volume = _num(bars.get("volume"))
    turn = _num(bars.get("turn"))
    ts = _num(bars.get("tradestatus"))
    brief: dict = {
        "first": dates[0] if dates else None,
        "last": dates[-1] if dates else None,
        "bars": len(dates) if dates else (0 if close is None else int(close.size)),
        "stage": format_stage(feat),
        "features": feat,
        "campaign": None,
        "chips": {},
    }
    if high is None or low is None or close is None or volume is None or turn is None:
        return brief

    def _snap(end: int) -> dict | None:
        stats = _chip_at(high, low, close, turn, ts, end)
        if not stats:
            return None
        return {
            "date": dates[end] if end < len(dates) else None,
            "close": float(close[end]),
            "n_peaks": stats["n_peaks"],
            "std_over_close": stats["std_over_close"],
            "skew": stats["skew"],
            "kurt": stats["kurt"],
            "mass_below_close": stats["mass_below_close"],
            "peaks": stats["peaks"],
        }

    camp = _campaign(high, low, close, volume)
    if camp is not None:
        brief["campaign"] = {
            "peak_date": dates[camp["i"]] if camp["i"] < len(dates) else None,
            "trough_date": dates[camp["j"]] if camp["j"] < len(dates) else None,
            "peak_high": camp["peak_high"],
            "trough_low": camp["trough"],
            "drop": camp["drop"],
            "span": camp["span"],
            "run": camp["run"],
            "speed": camp["speed"],
            "rise": camp["rise"],
            "recovery": camp["recovery"],
        }
        brief["chips"]["peak"] = _snap(camp["i"])
        brief["chips"]["trough"] = _snap(camp["j"])
    if close.size:
        brief["chips"]["end"] = _snap(close.size - 1)
    return brief


def format_stage(feat: Mapping[str, float | None]) -> str:
    """One line a person can read beside the rank."""
    def n(key: str, digits: int = 0) -> str:
        val = feat.get(key)
        if val is None:
            return "—"
        return f"{val:.{digits}f}"

    end_n = n("end_n_peaks")
    peak_n = n("peak_n_peaks")
    trough_n = n("trough_n_peaks")
    skew = feat.get("end_skew")
    skew_txt = "—" if skew is None else f"{skew:+.1f}"
    under = "四线全在价格下" if feat.get("all_under") == 1.0 else "均线未全亏"
    return (
        f"末{end_n}峰 峰日{peak_n}峰 谷日{trough_n}峰"
        f" · 偏度{skew_txt} · 离散{n('end_std', 2)}"
        f" · 前峰量比{n('vol_climax', 2)} · {under}"
    )


def _scale(values: list[float | None]) -> float:
    arr = np.asarray([v for v in values if v is not None and v == v], dtype=float)
    if arr.size < 2:
        return 1.0
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad > 1e-6:
        return mad
    sd = float(np.std(arr))
    return sd if sd > 1e-6 else 1.0


def _normalize_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    src = dict(WEIGHT_PRESETS["均衡"] if weights is None else weights)
    active = {k: float(v) for k, v in src.items() if k in _GROUPS and float(v) > 0}
    total = sum(active.values())
    if total <= 0:
        active = dict(WEIGHT_PRESETS["均衡"])
        total = sum(active.values())
    return {k: v / total for k, v in active.items()}


def screen_similar(
    template: Mapping[str, Sequence[float]],
    candidates: Mapping[str, Mapping[str, Sequence[float]]],
    recall_n: int = DEFAULT_SCREEN_RECALL,
    weights: Mapping[str, float] | None = None,
    exclude_st: bool = True,
) -> tuple[list[ScreenHit], dict]:
    """Recall by close DTW, then rank the pool by feature distance to *template*.

    ``candidates`` must already be sliced to each name's own window, the
    same contract as :func:`whaletrail.similarity.retrieve_rank`.  The
    template's own code may sit in *candidates*; callers drop it.

    Returns ``(hits, info)``.  ``info`` carries the weights used and the
    template's own feature vector (``template_features`` / ``template_stage``).
    """
    used = _normalize_weights(weights)
    t_close = _num(template.get("close"))
    template_features = describe_window(template)
    info = {
        "weights": used,
        "template_features": template_features,
        "template_stage": format_stage(template_features),
    }
    if t_close is None or t_close.size < 2:
        return [], info

    recalled: list[tuple[str, float, Mapping[str, Sequence[float]]]] = []
    for code, bars in candidates.items():
        if exclude_st and _last_is_st(bars, None):
            continue
        close = _num(bars.get("close"))
        if close is None or close.size < 2:
            continue
        dist = pair_dtw(t_close, close)
        if np.isfinite(dist):
            recalled.append((code, float(dist), bars))
    recalled.sort(key=lambda item: (item[1], item[0]))
    if not recalled:
        return [], info
    keep = recalled[: max(1, int(recall_n))]
    place = {code: i + 1 for i, (code, _, _) in enumerate(recalled)}

    described = [(code, d_k, bars, describe_window(bars)) for code, d_k, bars in keep]
    scales = {
        key: _scale([feat.get(key) for *_, feat in described])
        for key in {name for names in _GROUPS.values() for name in names}
    }
    tfeat = template_features

    def group_distance(feat: Mapping[str, float | None], group: str) -> float | None:
        parts: list[float] = []
        for key in _GROUPS[group]:
            left = tfeat.get(key)
            right = feat.get(key)
            if left is None or right is None:
                continue
            parts.append(abs(float(right) - float(left)) / scales[key])
        if not parts:
            return None
        return float(np.mean(parts))

    hits: list[ScreenHit] = []
    for code, d_k, bars, feat in described:
        groups = {name: group_distance(feat, name) for name in used}
        score = 0.0
        seen = 0.0
        for name, weight in used.items():
            dist = groups[name]
            if dist is None:
                continue
            score += weight * dist
            seen += weight
        if seen <= 0:
            continue
        score /= seen
        cand_close = _num(bars.get("close"))
        corr = pair_corr(t_close, cand_close if cand_close is not None else [])
        hits.append(ScreenHit(
            code=code,
            score=score,
            recall_rank=place[code],
            delta=None,
            d_kline=d_k,
            close_corr=None if corr is None or corr != corr else float(corr),
            groups=groups,
            features=feat,
            stage=format_stage(feat),
            group_weights=used,
        ))
    hits.sort(key=lambda hit: (hit.score, hit.d_kline, hit.code))
    ranked: list[ScreenHit] = []
    for i, hit in enumerate(hits, start=1):
        ranked.append(ScreenHit(
            code=hit.code,
            score=hit.score,
            recall_rank=hit.recall_rank,
            delta=hit.recall_rank - i,
            d_kline=hit.d_kline,
            close_corr=hit.close_corr,
            groups=hit.groups,
            features=hit.features,
            stage=hit.stage,
            group_weights=hit.group_weights,
        ))
    return ranked, info
