"""Display context for 相似选股: board, float cap, index and sector direction.

These readings sit on the table and inside the commentary. They are not
inputs to ``screen_similar`` and do not change rank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

FLAT_PP = 0.10

INDEX_CODES = {
    "sh.000001": "上证",
    "sz.399006": "创业板指",
    "sz.399001": "深证成指",
}

_BOARD_INDEX = {
    "创业板": "sz.399006",
    "深主板": "sz.399001",
}


def board_of(code: str) -> str:
    """Listing board from the baostock code. 科创50 is not in the database."""
    text = (code or "").strip().lower()
    market, _, num = text.partition(".")
    if market == "bj":
        return "北交所"
    if num.startswith(("688", "689")):
        return "科创板"
    if num.startswith(("300", "301")):
        return "创业板"
    if market == "sh":
        return "沪主板"
    if market == "sz":
        return "深主板"
    return "其他"


def industry_short(industry: str | None) -> str:
    """Drop the CSRC code prefix: ``C39计算机…`` → ``计算机…``."""
    s = industry or ""
    if len(s) >= 3 and s[0].isalpha() and s[1].isdigit():
        i = 0
        while i < len(s) and s[i].isascii() and (s[i].isalpha() or s[i].isdigit()):
            i += 1
        s = s[i:]
    return s or (industry or "")


def industry_cut(industry: str | None, n: int = 6) -> str:
    short = industry_short(industry)
    if not short:
        return ""
    return short[:n]


def cap_band(yi: float | None) -> str | None:
    """Float-cap preference band, in 亿.

    几十亿 (30–100) is the preferred band. Above 100 the reading gets
    duller in steps. Below 30 is smaller than that band.
    """
    if yi is None or yi != yi or yi < 0:
        return None
    if yi < 30:
        return "偏小"
    if yi < 100:
        return "合适"
    if yi < 300:
        return "偏大"
    if yi < 1000:
        return "大"
    return "超大"


def cap_text(yi: float | None) -> str:
    if yi is None or yi != yi:
        return "—"
    if yi >= 10:
        return f"{yi:.0f}亿"
    return f"{yi:.1f}亿"


def float_mcap_yi(close, volume, turn) -> float | None:
    """流通市值（亿）= close × volume × 100 / turn / 1e8.

    ``turn`` is percent. ``volume`` is shares, matching the sector panel.
    """
    try:
        px = float(close)
        vol = float(volume)
        tv = float(turn)
    except (TypeError, ValueError):
        return None
    if px != px or vol != vol or tv != tv or px <= 0 or vol <= 0 or tv <= 0:
        return None
    return px * vol * 100.0 / tv / 1e8


def latest_float_yi(bars: Mapping[str, Sequence]) -> float | None:
    close = bars.get("close") or []
    volume = bars.get("volume") or []
    turn = bars.get("turn") or []
    for i in range(len(close) - 1, -1, -1):
        vol = volume[i] if i < len(volume) else None
        tv = turn[i] if i < len(turn) else None
        yi = float_mcap_yi(close[i], vol, tv)
        if yi is not None:
            return yi
    return None


def walk_vs(stock_pp: float | None, ref_pp: float | None, flat: float = FLAT_PP) -> str:
    """Same-day direction versus a reference return, both in percentage points.

    Moves inside ``flat`` are quiet. 顺/逆 need both sides outside that band
    and on the same or opposite side of zero.
    """
    if stock_pp is None or ref_pp is None or stock_pp != stock_pp or ref_pp != ref_pp:
        return "—"
    stock_flat = abs(stock_pp) < flat
    ref_flat = abs(ref_pp) < flat
    if stock_flat and ref_flat:
        return "平"
    if ref_flat:
        return "独立"
    if stock_flat:
        return "未跟"
    if stock_pp > 0 and ref_pp > 0:
        return "顺涨"
    if stock_pp < 0 and ref_pp < 0:
        return "顺跌"
    if stock_pp > 0:
        return "逆涨"
    return "逆跌"


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _is_st(flags: Sequence) -> bool:
    if not flags:
        return False
    return _num(flags[-1]) == 1.0


def _index_of(dates: Sequence[str], asof: str) -> int | None:
    for i in range(len(dates) - 1, -1, -1):
        if dates[i] == asof:
            return i
    return None


def day_return_pp(bars: Mapping[str, Sequence], asof: str) -> float | None:
    """Close-to-close return of *asof* in percentage points, or None if absent."""
    dates = [str(d)[:10] for d in (bars.get("trade_date") or [])]
    close = list(bars.get("close") or [])
    i = _index_of(dates, asof)
    if i is None or i < 1 or i >= len(close):
        return None
    status = bars.get("tradestatus") or []
    if i < len(status) and _num(status[i]) == 0.0:
        return None
    prev, last = _num(close[i - 1]), _num(close[i])
    if prev is None or last is None or prev <= 0:
        return None
    return (last / prev - 1.0) * 100.0


def sector_moves(
    bars: Mapping[str, Mapping[str, Sequence]],
    industry: Mapping[str, str],
    asof: str,
    min_names: int = 8,
) -> dict[str, tuple[float, int]]:
    """Equal-weight same-day return by CSRC industry. ST and halts are out."""
    buckets: dict[str, list[float]] = {}
    for code, rec in bars.items():
        ind = industry.get(code)
        if not ind or _is_st(rec.get("is_st") or []):
            continue
        pp = day_return_pp(rec, asof)
        if pp is None:
            continue
        buckets.setdefault(ind, []).append(pp)
    out: dict[str, tuple[float, int]] = {}
    for ind, vals in buckets.items():
        if len(vals) < min_names:
            continue
        out[ind] = (sum(vals) / len(vals), len(vals))
    return out


def load_index_moves(conn) -> dict[str, dict[str, float]]:
    """``{trade_date: {index code: pct_chg}}``. Stored unit is percentage points."""
    codes = tuple(INDEX_CODES)
    marks = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT trade_date, code, pct_chg FROM index_kline WHERE code IN ({marks})",
        codes,
    )
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        pp = _num(row["pct_chg"])
        if pp is None:
            continue
        out.setdefault(str(row["trade_date"])[:10], {})[row["code"]] = pp
    return out


def name_context(
    code: str,
    bars: Mapping[str, Sequence],
    industry: Mapping[str, str],
    sectors: Mapping[str, tuple[float, int]],
    index_day: Mapping[str, float],
    asof: str,
) -> dict:
    """One name's board, float cap and same-day direction. Safe to JSON."""
    ind = industry.get(code) or ""
    yi = latest_float_yi(bars)
    band = cap_band(yi)
    ret = day_return_pp(bars, asof)
    index_pp = _num(index_day.get("sh.000001"))
    sec = sectors.get(ind)
    sector_pp = None if sec is None else float(sec[0])
    sector_n = None if sec is None else int(sec[1])
    board = board_of(code)
    board_code = _BOARD_INDEX.get(board)
    board_pp = _num(index_day.get(board_code)) if board_code else None
    return {
        "board": board,
        "industry": ind,
        "industry_short": industry_short(ind),
        "industry_cut": industry_cut(ind),
        "float_yi": None if yi is None else round(yi, 1),
        "cap_band": band,
        "day": asof,
        "ret_pp": None if ret is None else round(ret, 2),
        "vs_index": walk_vs(ret, index_pp),
        "index_pp": None if index_pp is None else round(index_pp, 2),
        "index_name": "上证",
        "vs_sector": walk_vs(ret, sector_pp) if sector_pp is not None else "—",
        "sector_pp": None if sector_pp is None else round(sector_pp, 2),
        "sector_n": sector_n,
        "vs_board_index": walk_vs(ret, board_pp) if board_code else None,
        "board_index_pp": None if board_pp is None else round(board_pp, 2),
        "board_index_name": INDEX_CODES.get(board_code or ""),
    }


def _fmt_pct(value, digits: int = 1) -> str:
    num = _num(value)
    if num is None:
        return "—"
    return f"{num:.{digits}%}"


def _signed_pp(value) -> str:
    num = _num(value)
    if num is None:
        return "—"
    return f"{num:+.2f}%"


def _peak_place(vs) -> str:
    num = _num(vs)
    if num is None:
        return "主峰位置缺读数"
    if abs(num) <= 0.005:
        return "主峰贴着收盘"
    if num < 0:
        return f"主峰在收盘下方 {abs(num):.1%}"
    return f"主峰在收盘上方 {num:.1%}"


def _wave_sentence(recall_rank: int, d_kline: float, close_corr: float | None) -> str:
    dtw = f"{float(d_kline):.2f}"
    if recall_rank <= 80:
        text = f"波形在召回前排，第 {recall_rank}，DTW {dtw}。"
    elif recall_rank <= 200:
        text = f"波形在召回池中部，第 {recall_rank}，DTW {dtw}。"
    else:
        text = f"波形在召回池后部，第 {recall_rank}，DTW {dtw}。名次是后面的特征抬上来的。"
    corr = _num(close_corr)
    if corr is not None and corr < -0.3:
        text += f"归一化收盘相关 {corr:+.2f}。"
    return text


def _trend_sentence(feat: Mapping, template: Mapping) -> str:
    drop = _num(feat.get("drop"))
    run = _num(feat.get("run"))
    recovery = _num(feat.get("recovery"))
    box_pos = _num(feat.get("box_pos"))
    bits = []
    if drop is not None:
        bits.append(f"此前回撤 {drop:.0%}")
    if run is not None:
        bits.append(f"最长连续收跌 {int(run)} 根")
    if recovery is not None:
        bits.append(f"从谷底修回这段跌幅的 {recovery:.0%}")
    text = "，".join(bits) + "。" if bits else "近期这段回撤缺读数。"
    if box_pos is not None:
        text += f"收盘在谷后箱内 {box_pos:.0%} 的位置。"
    t_rec = _num(template.get("recovery"))
    if recovery is not None and t_rec is not None:
        if recovery <= t_rec + 0.05:
            text += "修复程度还停在蓝本那天附近。"
        elif recovery > t_rec + 0.15:
            text += "从谷底离开的距离已经大于蓝本。"
        else:
            text += "修复比蓝本略远一点。"
    return text


def _confirm_sentence(feat: Mapping, template: Mapping) -> str:
    ret = _num(feat.get("ret1"))
    loc = _num(feat.get("close_loc"))
    if ret is None:
        bar = "末日涨跌缺读数。"
    elif ret > 0:
        bar = f"末日收阳 {_fmt_pct(ret)}。"
    else:
        bar = f"末日收阴 {_fmt_pct(ret)}。"
    if loc is not None:
        bar += f"收在当日区间 {loc:.2f}。"
    vc = _num(feat.get("vol_climax"))
    vm = _num(feat.get("vol_ma20"))
    turn = _num(feat.get("turn"))
    vol = []
    if vc is not None:
        vol.append(f"前峰量比 {vc:.2f}")
    if vm is not None:
        vol.append(f"20 日量比 {vm:.2f}")
    if turn is not None:
        vol.append(f"换手 {turn:.1f}%")
    text = bar + ("，".join(vol) + "。" if vol else "")
    if _volume_near(feat, template):
        text += "量能靠近蓝本的末端。"
    elif ret is not None and ret > 0:
        text += "量能还没到蓝本那种末端放量。"
    else:
        text += "末端还没有阳线放量。"
    return text


def _volume_near(feat: Mapping, template: Mapping) -> bool:
    vc = _num(feat.get("vol_climax"))
    vm = _num(feat.get("vol_ma20"))
    tvc = _num(template.get("vol_climax"))
    tvm = _num(template.get("vol_ma20"))
    near_climax = vc is not None and tvc not in (None, 0.0) and vc >= 0.75 * tvc
    near_ma = (
        vm is not None
        and tvm not in (None, 0.0)
        and vm >= 0.85 * tvm
        and vm >= 1.0
    )
    return bool(near_climax or near_ma)


def _ma_sentence(feat: Mapping, template: Mapping) -> str:
    if _num(feat.get("all_under")) == 1.0:
        return "MA7、MA30、MA55、MA120 都在价格上方。"
    bits = []
    for key, label in (("ma7", "MA7"), ("ma30", "MA30"), ("ma55", "MA55"), ("ma120", "MA120")):
        val = _num(feat.get(key))
        if val is None:
            continue
        if val >= 0:
            bits.append(f"高于{label} {val:.1%}")
        else:
            bits.append(f"低于{label} {abs(val):.1%}")
    text = ("价格" + "、".join(bits) + "。") if bits else "均线缺读数。"
    if _ma_same_side(feat, template):
        text += "短均在价格下、长均还在价格上，和蓝本同侧。"
    elif _template_ma_split(template):
        text += "均线位置和蓝本不同侧。"
    return text


def _template_ma_split(template: Mapping) -> bool:
    ma7 = _num(template.get("ma7"))
    ma30 = _num(template.get("ma30"))
    ma55 = _num(template.get("ma55"))
    ma120 = _num(template.get("ma120"))
    short = ma7 is not None and ma30 is not None and ma7 > 0 and ma30 > 0
    long_above = (ma55 is not None and ma55 < 0) or (ma120 is not None and ma120 < 0)
    return bool(short and long_above)


def _ma_same_side(feat: Mapping, template: Mapping) -> bool:
    if _num(feat.get("all_under")) == 1.0:
        return False
    if not _template_ma_split(template):
        return False
    ma7 = _num(feat.get("ma7"))
    ma30 = _num(feat.get("ma30"))
    ma55 = _num(feat.get("ma55"))
    ma120 = _num(feat.get("ma120"))
    short = ma7 is not None and ma30 is not None and ma7 > -0.01 and ma30 > -0.02
    long_above = (ma55 is not None and ma55 < 0) or (ma120 is not None and ma120 < 0)
    return bool(short and long_above)


def _chip_sentence(feat: Mapping, template: Mapping) -> str:
    n_peaks = _num(feat.get("end_n_peaks"))
    mass = _num(feat.get("end_mass_below"))
    std = _num(feat.get("end_std"))
    peak_mass = _num(feat.get("peak_mass_below"))
    trough_mass = _num(feat.get("trough_mass_below"))
    head = "末日筹码缺读数。"
    if n_peaks is not None:
        head = f"末日 {int(n_peaks)} 个峰，{_peak_place(feat.get('end_top_vs'))}"
        if mass is not None:
            head += f"，获利 {mass:.0%}"
        if std is not None:
            head += f"，离散 {std:.2f}"
        head += "。"
    extra = []
    if peak_mass is not None:
        extra.append(f"峰日获利 {peak_mass:.0%}")
    if trough_mass is not None:
        extra.append(f"谷日获利 {trough_mass:.0%}")
    text = head + ("，".join(extra) + "。" if extra else "")
    if _chip_same(feat, template):
        text += "末日筹码阶段和蓝本同侧。"
    elif mass is not None and mass >= 0.90 and (_num(feat.get("end_top_vs")) or 0) < -0.08:
        text += "末日筹码大部分已经在收盘下方，主峰离收盘较远。"
    elif mass is not None and mass <= 0.25:
        text += "末日获利很低，主峰还压在收盘上方。"
    else:
        text += "末日筹码和蓝本只是部分靠近。"
    return text


def _chip_same(feat: Mapping, template: Mapping) -> bool:
    def near(key: str, tol: float) -> bool:
        left = _num(feat.get(key))
        right = _num(template.get(key))
        return left is not None and right is not None and abs(left - right) <= tol

    n_left = _num(feat.get("end_n_peaks"))
    n_right = _num(template.get("end_n_peaks"))
    if n_left is None or n_right is None or abs(n_left - n_right) > 0.5:
        return False
    trough = _num(feat.get("trough_mass_below"))
    t_trough = _num(template.get("trough_mass_below"))
    trough_ok = trough is not None and t_trough is not None and abs(trough - t_trough) <= 0.10
    return near("end_mass_below", 0.20) and near("end_top_vs", 0.08) and trough_ok


def _still_early(feat: Mapping, template: Mapping) -> bool:
    recovery = _num(feat.get("recovery"))
    t_rec = _num(template.get("recovery"))
    if recovery is None or t_rec is None:
        return True
    return recovery <= t_rec + 0.15


def _confirm_ok(feat: Mapping, template: Mapping) -> bool:
    ret = _num(feat.get("ret1"))
    loc = _num(feat.get("close_loc"))
    if ret is None or ret <= 0 or loc is None or loc < 0.60:
        return False
    return _volume_near(feat, template)


def _cap_clause(ctx: Mapping, positive: bool) -> str:
    band = ctx.get("cap_band")
    yi = _num(ctx.get("float_yi"))
    shown = "—" if yi is None else f"{yi:.0f} 亿" if yi >= 10 else f"{yi:.1f} 亿"
    if band == "合适":
        return f"流通市值 {shown}，落在几十亿这档。"
    if band == "偏小":
        return f"流通市值 {shown}，不到三十亿，比偏好的几十亿更小。"
    if band == "偏大":
        if positive:
            return f"流通市值 {shown}，过了一百亿，幅度上会比几十亿的票更钝。"
        return f"流通市值 {shown}，过了一百亿。"
    if band == "大":
        if positive:
            return f"流通市值 {shown}，在几百亿，这种体量上近期照蓝本那种幅度走出来的可能要往下看。"
        return f"流通市值 {shown}，在几百亿。"
    if band == "超大":
        if positive:
            return f"流通市值 {shown}，到了上千亿，这种体量上近期照蓝本那种幅度走出来的可能要往下看。"
        return f"流通市值 {shown}，到了上千亿。"
    return "流通市值缺换手，体量读不出来。"


def _judge(feat: Mapping, template: Mapping, recall_rank: int, ctx: Mapping) -> str:
    if _num(feat.get("all_under")) == 1.0:
        base = "四条均线都在价格上方，近期按蓝本这段去看拉升不成立。"
        return base + _cap_clause(ctx, positive=False)
    wave_ok = recall_rank <= 200
    chip_ok = _chip_same(feat, template)
    confirm_ok = _confirm_ok(feat, template)
    ma_ok = _ma_same_side(feat, template)
    early = _still_early(feat, template)
    aligned = chip_ok and confirm_ok and ma_ok and early
    if aligned and wave_ok:
        base = "波形、筹码、末端量价和均线都在蓝本大涨前夜的同一侧，近期有沿这段结构走出来的可能。"
        cap = _cap_clause(ctx, positive=True)
        if ctx.get("cap_band") in ("大", "超大"):
            base = "波形、筹码、末端量价和均线都在蓝本大涨前夜的同一侧。"
        return base + cap
    if aligned and not wave_ok:
        base = "筹码、末端量价和均线靠近蓝本，波形在召回池后部，形状这一关还不齐。"
        return base + _cap_clause(ctx, positive=False)
    if (chip_ok or confirm_ok) and ma_ok:
        base = "有一段读数靠近蓝本，末端量价、筹码和波形还没有叠在一起，近期大涨的读数不齐。"
        return base + _cap_clause(ctx, positive=False)
    if chip_ok:
        base = "筹码阶段靠近蓝本，末端量价和均线还没有叠上，近期按这段去看拉升不成立。"
        return base + _cap_clause(ctx, positive=False)
    if confirm_ok:
        base = "末端有阳线放量，筹码和均线还没到蓝本那一侧，近期按这段去看拉升不成立。"
        return base + _cap_clause(ctx, positive=False)
    base = "和蓝本大涨前夜不在同一侧，近期按这段去看拉升不成立。"
    return base + _cap_clause(ctx, positive=False)


def _backdrop_sentence(ctx: Mapping) -> str:
    board = ctx.get("board") or "—"
    day = ctx.get("day") or "—"
    own = "当日无行情" if ctx.get("ret_pp") is None else f"自身 {_signed_pp(ctx.get('ret_pp'))}"
    text = (
        f"属性是{board}。窗口最后一天 {day} {own}，"
        f"相对上证{ctx.get('vs_index') or '—'}"
    )
    if ctx.get("index_pp") is not None:
        text += f"（上证 {_signed_pp(ctx.get('index_pp'))}）"
    sector_name = ctx.get("industry_short") or "所属行业"
    text += f"，相对{sector_name}{ctx.get('vs_sector') or '—'}"
    if ctx.get("sector_pp") is not None:
        n = ctx.get("sector_n")
        text += f"（板块等权 {_signed_pp(ctx.get('sector_pp'))}，{n} 只）"
    text += "。"
    if board == "科创板":
        text += "库里没有科创50，大盘对照用的是上证。"
    elif ctx.get("board_index_name") and ctx.get("board_index_pp") is not None:
        text += (
            f"相对{ctx['board_index_name']}{ctx.get('vs_board_index') or '—'}"
            f"（{_signed_pp(ctx.get('board_index_pp'))}）。"
        )
    return text


def commentary(
    feat: Mapping,
    template: Mapping,
    recall_rank: int,
    d_kline: float,
    close_corr: float | None,
    ctx: Mapping,
) -> str:
    """Fixed-order note: waveform, trend, yang and volume, MAs, chips, backdrop, judgment."""
    parts = [
        _wave_sentence(int(recall_rank), float(d_kline), close_corr),
        _trend_sentence(feat, template),
        _confirm_sentence(feat, template),
        _ma_sentence(feat, template),
        _chip_sentence(feat, template),
        _backdrop_sentence(ctx),
        _judge(feat, template, int(recall_rank), ctx),
    ]
    return "\n\n".join(parts)
