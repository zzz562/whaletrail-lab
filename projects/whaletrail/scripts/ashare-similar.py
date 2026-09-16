#!/usr/bin/env python3
"""A-share fused similarity scan over baostock daily_kline.

Usage:
  python scripts/ashare-similar.py --symbol sh.601899
  python scripts/ashare-similar.py --symbol SSE:601899 --window 90 --top 20

Ranks the whole-market universe by kline DTW + turnover DTW + chip
Wasserstein (percentile fusion).  Observation only — not a trade list.
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
from whaletrail.similarity import DEFAULT_WEIGHTS, rank_multi
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"


def _parse_weights(raw: str) -> dict[str, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("weights must be k,v,c e.g. 0.5,0.3,0.2")
    k, v, c = (float(x) for x in parts)
    return {"kline": k, "volume": v, "chip": c}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Reference, e.g. sh.601899 or SSE:601899")
    parser.add_argument("--window", type=int, default=90, help="Lookback trading days (default 90)")
    parser.add_argument("--top", type=int, default=20, help="Rows to print (default 20)")
    parser.add_argument(
        "--weights",
        type=_parse_weights,
        default=DEFAULT_WEIGHTS,
        help="kline,volume,chip weights (default 0.5,0.3,0.2)",
    )
    parser.add_argument("--include-st", action="store_true", help="Keep ST names in the ranking")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    args = parser.parse_args()

    try:
        code = to_baostock_code(args.symbol)
    except ValueError:
        code = args.symbol.strip().lower()

    start = (date.today() - timedelta(days=max(420, int(args.window * 2)))).isoformat()
    repo = Repository(args.db)
    names = repo.universe_names()
    bars = repo.daily_bars(start=start)
    repo.close()

    if code not in bars:
        print(f"⚠️ {code} 不在 daily_kline（{start}→今）。先在 Mac mini 跑 fetch-baostock-universe.py")
        sys.exit(1)

    eligible = {c: b for c, b in bars.items() if len(b.get("close") or []) >= args.window}
    if code not in eligible:
        print(f"⚠️ {code} 窗口内不足 {args.window} 根")
        sys.exit(1)

    t0 = time.perf_counter()
    matches, used = rank_multi(
        eligible[code],
        eligible,
        window=args.window,
        weights=args.weights,
        exclude_st=not args.include_st,
    )
    elapsed = time.perf_counter() - t0
    matches = [m for m in matches if m.code != code][: args.top]

    ref_name = names.get(code, "")
    wtxt = " ".join(f"{k}={v:.2f}" for k, v in used.items())
    print(
        f"\n🐋 相似选股 · 参考 {ref_name} ({code}) · 近 {args.window} 日"
        f" · {len(eligible)} 只满窗口 · {elapsed:.2f}s"
    )
    print(f"权重 {wtxt}")
    print(
        f"{'排名':<4}{'代码':<12}{'名称':<10}"
        f"{'综合':>8}{'K':>10}{'量':>10}{'筹':>10}{'获利':>8}{'集中':>8}"
    )
    print("-" * 82)
    for i, m in enumerate(matches, start=1):
        d_vol = f"{m.d_vol:.4f}" if m.d_vol is not None and np_finite(m.d_vol) else "—"
        d_chip = f"{m.d_chip:.4f}" if m.d_chip is not None and np_finite(m.d_chip) else "—"
        wr = f"{m.winner_ratio:.2f}" if m.winner_ratio is not None else "—"
        conc = f"{m.concentration:.2f}" if m.concentration is not None else "—"
        print(
            f"{i:<4}{m.code:<12}{(names.get(m.code) or '')[:10]:<10}"
            f"{m.fused:8.4f}{m.d_kline:10.4f}{d_vol:>10}{d_chip:>10}{wr:>8}{conc:>8}"
        )
    if not matches:
        print("（无候选）")


def np_finite(x: float) -> bool:
    return x == x and x != float("inf") and x != float("-inf")


if __name__ == "__main__":
    main()
