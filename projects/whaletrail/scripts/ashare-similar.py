#!/usr/bin/env python3
"""A-share similar-stock scan: kline recall, then volume/chip rank.

Usage:
  python scripts/ashare-similar.py --symbol sh.601899
  python scripts/ashare-similar.py --symbol SSE:601899 --window 90 --recall 80 --top 20
  python scripts/ashare-similar.py --symbol sz.000823 --start 2025-12-02 --end 2026-05-21

The marked dates (or, without them, the reference's own last ``--window`` bars)
are the template: its demo waveform and chip profile.  Every candidate is
compared on its own most recent bars of that same length, so shapes are matched
and dates are not — the same rules as the dashboard's 相似选股 page.  Stage 1
DTW on close over the universe.  Stage 2 reranks the recall pool by turnover L1
+ chip Wasserstein.  Observation only — not a trade list.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import to_baostock_code
from whaletrail.similarity import (
    DEFAULT_RANK_WEIGHTS,
    DEFAULT_RECALL_N,
    build_scan_pool,
    retrieve_rank,
)
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"


def _parse_rank(raw: str) -> dict[str, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("rank weights must be volume,chip e.g. 0.6,0.4")
    v, c = (float(x) for x in parts)
    return {"volume": v, "chip": c}


def np_finite(x: float) -> bool:
    return x == x and x != float("inf") and x != float("-inf")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Reference, e.g. sh.601899 or SSE:601899")
    parser.add_argument("--window", type=int, default=90, help="Template length in bars when no --start/--end (default 90)")
    parser.add_argument("--start", help="Template window start YYYY-MM-DD (with --end)")
    parser.add_argument("--end", help="Template window end YYYY-MM-DD (with --start)")
    parser.add_argument("--recall", type=int, default=DEFAULT_RECALL_N, help="K-line recall pool (default 80)")
    parser.add_argument("--top", type=int, default=20, help="Rows to print (default 20)")
    parser.add_argument(
        "--rank",
        type=_parse_rank,
        default=DEFAULT_RANK_WEIGHTS,
        help="volume,chip rank weights inside the pool (default 0.35,0.65)",
    )
    parser.add_argument("--include-st", action="store_true", help="Keep ST names in the ranking")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    args = parser.parse_args()

    try:
        code = to_baostock_code(args.symbol)
    except ValueError:
        code = args.symbol.strip().lower()

    horizon = 420  # universe fetch window, same as the dashboard's scan page
    recent_start = (date.today() - timedelta(days=horizon)).isoformat()
    repo = Repository(args.db)
    names = repo.universe_names()
    bars = repo.daily_bars(start=recent_start)
    # A template can reach further back than the universe fetch; pull deep
    # history for the reference name alone so the cost stays bounded.
    deep_start = None
    if args.start and args.start < recent_start:
        deep_start = args.start
    elif not args.start and len((bars.get(code) or {}).get("close") or []) < args.window:
        deep_start = (date.today() - timedelta(days=max(horizon, args.window * 2))).isoformat()
    if deep_start:
        deep = repo.daily_bars(start=deep_start, codes=[code])
        if deep.get(code):
            bars = {**bars, code: deep[code]}
    repo.close()

    if code not in bars:
        print(
            f"⚠️ {code} 不在 daily_kline（{recent_start}→今）。符号要带市场（sz.000823 / SSE:601899）；"
            "没有全市场日线则先在 Mac mini 跑 fetch-baostock-universe.py"
        )
        sys.exit(1)

    if bool(args.start) != bool(args.end):
        print("⚠️ --start 与 --end 要成对给（模板窗口）")
        sys.exit(2)
    if args.start:
        lo, hi = args.start, args.end
    else:
        ref_dates = [str(d)[:10] for d in bars[code].get("trade_date") or []]
        if len(ref_dates) < args.window:
            print(f"⚠️ {code} 只有 {len(ref_dates)} 根，不足 --window {args.window}")
            sys.exit(1)
        lo, hi = ref_dates[-args.window], ref_dates[-1]

    built = build_scan_pool(bars, code, lo, hi)
    if built is None:
        print(f"⚠️ {code} 在 {lo}→{hi} 不足 10 根，换区间或换一只")
        sys.exit(1)
    template, pool, slice_n = built
    cand_end = max(
        (str((b.get("trade_date") or [""])[-1])[:10] for b in bars.values()), default=""
    )

    t0 = time.perf_counter()
    matches, used = retrieve_rank(
        template,
        pool,
        window=None,  # both sides already sliced to their own window
        recall_n=args.recall,
        rank_weights=args.rank,
        exclude_st=not args.include_st,
    )
    elapsed = time.perf_counter() - t0
    matches = [m for m in matches if m.code != code][: args.top]

    ref_name = names.get(code, "")
    wtxt = " ".join(f"{k}={v:.2f}" for k, v in used.items())
    print(
        f"\n🐋 相似选股 · 模板 {ref_name} ({code}) {lo}→{hi} · {slice_n} 根"
        f" · 候选 {len(pool) - 1} 只（各自最近 {slice_n} 根到 {cand_end or '—'}）"
        f" · 召回 {args.recall} · {elapsed:.2f}s"
    )
    print(f"重排 {wtxt}")
    print(
        f"{'序':<4}{'Δ':<6}{'召回':<6}{'代码':<12}{'名称':<10}"
        f"{'K':>10}{'相关':>8}{'量':>10}{'筹':>10}"
    )
    print("-" * 86)
    for i, m in enumerate(matches, start=1):
        d_vol = f"{m.d_vol:.4f}" if m.d_vol is not None and np_finite(m.d_vol) else "—"
        d_chip = f"{m.d_chip:.4f}" if m.d_chip is not None and np_finite(m.d_chip) else "—"
        corr = f"{m.close_corr:.2f}" if m.close_corr is not None else "—"
        delta = m.delta
        dtxt = "—" if delta is None else (f"+{delta}" if delta > 0 else str(delta))
        rtxt = "—" if m.recall_rank is None else str(m.recall_rank)
        print(
            f"{i:<4}{dtxt:<6}{rtxt:<6}{m.code:<12}{(names.get(m.code) or '')[:10]:<10}"
            f"{m.d_kline:10.4f}{corr:>8}{d_vol:>10}{d_chip:>10}"
        )
    if not matches:
        print("（无候选）")


if __name__ == "__main__":
    main()
