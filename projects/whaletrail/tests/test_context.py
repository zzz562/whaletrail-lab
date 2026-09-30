"""Board, float-cap band, same-day direction, and the commentary order."""

from whaletrail.context import (
    board_of,
    cap_band,
    commentary,
    day_return_pp,
    float_mcap_yi,
    name_context,
    sector_moves,
    walk_vs,
)


def _template():
    return {
        "drop": 0.67, "run": 9, "recovery": 0.15, "box_pos": 0.77,
        "ret1": 0.078, "close_loc": 0.82, "vol_climax": 0.76, "vol_ma20": 1.53, "turn": 8.3,
        "ma7": 0.011, "ma30": 0.020, "ma55": -0.276, "ma120": -0.156, "all_under": 0,
        "end_n_peaks": 1, "end_top_vs": -0.027, "end_mass_below": 0.56, "end_std": 0.34,
        "peak_mass_below": 0.98, "trough_mass_below": 0.00,
    }


def _match():
    feat = _template()
    feat["ret1"] = 0.05
    feat["close_loc"] = 0.80
    feat["vol_climax"] = 0.70
    return feat


def _ctx(yi=46.0, band="合适"):
    return {
        "board": "创业板",
        "industry_short": "专用设备制造业",
        "day": "2026-09-29",
        "ret_pp": 1.2,
        "vs_index": "顺涨",
        "index_pp": 0.18,
        "vs_sector": "逆涨",
        "sector_pp": -0.4,
        "sector_n": 40,
        "vs_board_index": "顺涨",
        "board_index_pp": 0.2,
        "board_index_name": "创业板指",
        "float_yi": yi,
        "cap_band": band,
    }


def test_board_from_code():
    assert board_of("sh.688079") == "科创板"
    assert board_of("sz.301369") == "创业板"
    assert board_of("sz.300672") == "创业板"
    assert board_of("sh.600869") == "沪主板"
    assert board_of("sz.002741") == "深主板"
    assert board_of("sz.001211") == "深主板"
    assert board_of("bj.830001") == "北交所"


def test_cap_bands_follow_the_tens_of_yi_preference():
    assert cap_band(29.9) == "偏小"
    assert cap_band(30) == "合适"
    assert cap_band(99.9) == "合适"
    assert cap_band(100) == "偏大"
    assert cap_band(299.9) == "偏大"
    assert cap_band(300) == "大"
    assert cap_band(999.9) == "大"
    assert cap_band(1000) == "超大"
    assert cap_band(None) is None


def test_float_mcap_uses_share_volume_and_percent_turn():
    # 1e8 shares * 10 yuan = 10 亿, turn 10% → volume = shares * turn/100 = 1e7
    yi = float_mcap_yi(10.0, 1e7, 10.0)
    assert yi is not None
    assert abs(yi - 10.0) < 1e-6


def test_walk_uses_a_tenth_of_a_point_as_quiet():
    assert walk_vs(1.1, 0.18) == "顺涨"
    assert walk_vs(-1.2, -0.4) == "顺跌"
    assert walk_vs(1.1, -0.4) == "逆涨"
    assert walk_vs(-1.1, 0.4) == "逆跌"
    assert walk_vs(0.05, 0.05) == "平"
    assert walk_vs(1.0, 0.05) == "独立"
    assert walk_vs(0.05, 1.0) == "未跟"
    assert walk_vs(None, 1.0) == "—"


def test_sector_move_is_equal_weight_and_skips_st():
    def bars(ret_close, st=0):
        return {
            "trade_date": ["2026-09-28", "2026-09-29"],
            "close": [100.0, ret_close],
            "tradestatus": [1, 1],
            "is_st": [st, st],
        }

    universe = {f"sh.{i:06d}": bars(101.0) for i in range(10)}
    universe["sh.000010"] = bars(90.0, st=1)
    industry = {code: "C39电子" for code in universe}
    moves = sector_moves(universe, industry, "2026-09-29")
    assert "C39电子" in moves
    mean, n = moves["C39电子"]
    assert n == 10
    assert abs(mean - 1.0) < 1e-9


def test_day_return_uses_the_asof_session_and_skips_halts():
    bars = {
        "trade_date": ["2026-09-26", "2026-09-28", "2026-09-29"],
        "close": [10.0, 11.0, 12.1],
        "tradestatus": [1, 1, 0],
    }
    assert day_return_pp(bars, "2026-09-29") is None
    assert abs(day_return_pp(bars, "2026-09-28") - 10.0) < 1e-9
    bars["tradestatus"] = [1, 1, 1]
    assert abs(day_return_pp(bars, "2026-09-29") - 10.0) < 1e-6


def test_commentary_leads_with_waveform_and_can_call_a_near_term_lift():
    text = commentary(_match(), _template(), 40, 4.2, 0.1, _ctx())
    parts = text.split("\n\n")
    assert parts[0].startswith("波形在召回前排")
    assert "末日收阳" in parts[2]
    assert "和蓝本同侧" in parts[3]
    assert "末日筹码阶段和蓝本同侧" in parts[4]
    assert "相对上证顺涨" in parts[5]
    assert "相对创业板指" in parts[5]
    assert "近期有沿这段结构走出来的可能" in parts[6]
    assert "几十亿" in parts[6]


def test_all_under_ma_blocks_the_lift_reading():
    feat = _match()
    feat["all_under"] = 1
    feat["ma7"] = -0.05
    feat["ma30"] = -0.04
    feat["ma55"] = -0.03
    feat["ma120"] = -0.1
    text = commentary(feat, _template(), 12, 3.0, 0.2, _ctx())
    assert "都在价格上方" in text
    assert "近期按蓝本这段去看拉升不成立" in text
    assert "近期有沿这段结构走出来的可能" not in text


def test_chip_match_without_ma_does_not_call_the_whole_stack_aligned():
    feat = _match()
    feat["ma7"] = -0.03
    text = commentary(feat, _template(), 40, 4.2, 0.1, _ctx())
    assert "筹码阶段靠近蓝本" in text
    assert "近期按这段去看拉升不成立" in text
    assert "不在同一侧" not in text


def test_back_half_waveform_keeps_the_shape_short():
    text = commentary(_match(), _template(), 327, 9.1, -0.4, _ctx())
    assert "波形在召回池后部" in text
    assert "形状这一关还不齐" in text


def test_thousand_yi_softens_an_otherwise_aligned_name():
    text = commentary(_match(), _template(), 40, 4.2, 0.1, _ctx(yi=1200, band="超大"))
    assert "上千亿" in text
    assert "往下看" in text
    assert "近期有沿这段结构走出来的可能" not in text


def test_name_context_reads_cap_and_directions():
    bars = {
        "trade_date": ["2026-09-28", "2026-09-29"],
        "close": [10.0, 10.5],
        "volume": [1e7, 1e7],
        "turn": [10.0, 10.0],
        "tradestatus": [1, 1],
        "is_st": [0, 0],
    }
    ctx = name_context(
        "sz.300001",
        bars,
        {"sz.300001": "C35专用设备制造业"},
        {"C35专用设备制造业": (-0.5, 20)},
        {"sh.000001": 0.2, "sz.399006": 0.4},
        "2026-09-29",
    )
    assert ctx["board"] == "创业板"
    assert ctx["cap_band"] == "偏小"
    assert abs(ctx["float_yi"] - 10.5) < 0.05
    assert ctx["vs_index"] == "顺涨"
    assert ctx["vs_sector"] == "逆涨"
    assert ctx["vs_board_index"] == "顺涨"
    assert ctx["industry_cut"] == "专用设备制造"[:6]
