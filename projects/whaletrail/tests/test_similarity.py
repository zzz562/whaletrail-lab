from __future__ import annotations

import numpy as np

from whaletrail.similarity import (
    DEFAULT_WEIGHTS,
    normalize,
    pair_dtw,
    pair_l1,
    rank_multi,
    rank_similar,
    retrieve_rank,
)


def test_dtw_self_zero_and_scale_shift_invariant():
    x = np.linspace(1.0, 2.0, 30)
    assert pair_dtw(x, x) == 0.0
    assert pair_dtw(x, x * 3 + 5) < 1e-12
    y = np.sin(np.linspace(0, 6, 30))
    assert pair_dtw(x, y) > pair_dtw(x, x)


def test_rank_similar_order():
    target = np.linspace(0, 1, 20)
    close_a = target * 2 + 3
    close_b = np.sin(np.linspace(0, 8, 20))
    ranked = rank_similar(target, {"a": close_a, "b": close_b})
    assert [c for c, _ in ranked] == ["a", "b"]


def _bars(close, turn=None, high=None, low=None, is_st=0, status=1):
    close = np.asarray(close, dtype=float)
    n = close.size
    high = close if high is None else np.asarray(high, dtype=float)
    low = close if low is None else np.asarray(low, dtype=float)
    if turn is None:
        turn = np.full(n, 1.0)
    out = {
        "close": close,
        "high": high,
        "low": low,
        "turn": np.asarray(turn, dtype=float),
        "volume": np.full(n, 1000.0),
        "tradestatus": np.full(n, status, dtype=float),
        "is_st": np.full(n, is_st, dtype=float),
    }
    return out


def test_rank_multi_kline_only_matches_rank_similar():
    rng = np.random.default_rng(0)
    target = np.linspace(10, 12, 40)
    cands = {}
    for i in range(8):
        cands[f"s{i}"] = target + rng.normal(0, 0.05 * (i + 1), size=40)
    cands["far"] = np.sin(np.linspace(0, 9, 40)) * 5 + 20
    closes = {k: v for k, v in cands.items()}
    ranked = rank_similar(target, closes)
    bars_t = _bars(target)
    bars_c = {k: _bars(v) for k, v in cands.items()}
    matches, used = rank_multi(
        bars_t, bars_c, weights={"kline": 1, "volume": 0, "chip": 0}, exclude_st=False
    )
    assert used == {"kline": 1.0}
    assert [m.code for m in matches] == [c for c, _ in ranked]


def test_volume_channel_prefers_aligned_spikes():
    n = 50
    close = np.linspace(10, 11, n)
    turn_ref = np.ones(n)
    turn_ref[30:35] = 8.0
    turn_sync = turn_ref.copy()
    turn_shift = np.ones(n)
    turn_shift[5:10] = 8.0
    assert pair_l1(turn_ref, turn_sync) == 0.0
    assert pair_l1(turn_ref, turn_shift) > 0.0
    matches, _ = rank_multi(
        _bars(close, turn=turn_ref),
        {"sync": _bars(close, turn=turn_sync), "shift": _bars(close, turn=turn_shift)},
        weights={"kline": 0, "volume": 1, "chip": 0},
        exclude_st=False,
    )
    assert [m.code for m in matches] == ["sync", "shift"]
    assert matches[0].d_vol < matches[1].d_vol


def test_missing_turn_drops_chip_weight():
    close = np.linspace(10, 12, 30)
    target = {"close": close, "volume": np.ones(30)}  # no turn / high / low
    other = {"close": close + 0.1, "volume": np.ones(30)}
    matches, used = rank_multi(target, {"x": other}, weights=DEFAULT_WEIGHTS)
    assert "chip" not in used
    assert matches[0].d_chip is None
    assert np.isfinite(matches[0].fused)


def test_exclude_st_drops_last_bar_st():
    close = np.linspace(1, 2, 20)
    matches, _ = rank_multi(
        _bars(close),
        {"ok": _bars(close), "st": _bars(close, is_st=1)},
        weights={"kline": 1, "volume": 0, "chip": 0},
        exclude_st=True,
    )
    assert [m.code for m in matches] == ["ok"]


def test_normalize_flat_is_zeros():
    assert np.allclose(normalize([3, 3, 3]), 0)


def test_retrieve_rank_kline_gate_then_volume_rerank():
    n = 40
    target = np.linspace(10, 12, n)
    like = target + 0.02
    like_bad = target + 0.05
    far = np.sin(np.linspace(0, 9, n)) * 5 + 20
    turn_t = np.ones(n)
    turn_t[20:25] = 8.0
    turn_shift = np.ones(n)
    turn_shift[0:5] = 8.0
    matches, used = retrieve_rank(
        _bars(target, turn=turn_t),
        {
            "like": _bars(like, turn=turn_t),
            "like_bad": _bars(like_bad, turn=turn_shift),
            "far": _bars(far, turn=turn_t),
        },
        recall_n=2,
        rank_weights={"volume": 1, "chip": 0},
        exclude_st=False,
    )
    codes = [m.code for m in matches]
    assert "far" not in codes
    assert set(codes) == {"like", "like_bad"}
    assert codes[0] == "like"
    assert matches[0].recall_rank == 1
    assert matches[0].delta is not None
    assert "volume" in used
