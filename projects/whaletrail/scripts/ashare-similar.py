#!/usr/bin/env python3
"""A-share chart-similarity scan (DTW) over accumulated tvscreener history.

Usage:
  python scripts/ashare-similar.py --symbol SSE:601899
  python scripts/ashare-similar.py --symbol SSE:601899 --window 90 --top 5

Phase 0 of the "find similar charts" feature: pick a reference stock and rank
the rest of the A-share watchlist by how closely their recent close series
resembles the reference.  Reads the same accumulated snapshot history as
``ashare-paper.py`` (``quote_snapshots`` → ``build_daily_history``), so it
works today on the 8-stock watchlist with no new data source.

Whole-market scanning (Phase 1) replaces the ``build_daily_history`` source
with the baostock ``daily_kline`` table; the ranking logic stays the same.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.history import build_daily_history
from whaletrail.data.watchlist import load_watchlist
from whaletrail.similarity import rank_similar

DB_PATH = ROOT / "results" / "whaletrail.db"
WATCHLIST = ROOT / "config" / "watchlist.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Reference tvscreener symbol, e.g. SSE:601899")
    parser.add_argument("--window", type=int, default=90, help="Lookback days (default 90)")
    parser.add_argument("--top", type=int, default=20, help="Number of matches to show (default 20)")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path (default results/whaletrail.db)")
    args = parser.parse_args()

    items = load_watchlist(WATCHLIST)
    a_items = [i for i in items if i.market == "china" and i.tradable]
    if args.symbol not in {i.tv_symbol for i in a_items}:
        print(f"⚠️ {args.symbol} 不在 A 股 watchlist 中（market=china, tradable=true）")
        sys.exit(1)

    series: dict[str, list[float]] = {}
    for item in a_items:
        hist = build_daily_history(args.db, item.tv_symbol)
        if hist.empty or len(hist) < args.window:
            print(f"⚠️ {item.name} ({item.tv_symbol}): 历史不足 {len(hist)} 天，跳过")
            continue
        series[item.tv_symbol] = [float(x) for x in hist["close"].tolist()]

    target = series[args.symbol]
    ranked = rank_similar(target, series, window=args.window)
    ranked = [r for r in ranked if r[0] != args.symbol]  # exclude self

    name_by_tv = {i.tv_symbol: i.name for i in a_items}
    ref_name = name_by_tv.get(args.symbol, args.symbol)
    print(f"\n🐋 相似走势 · 参考 {ref_name} ({args.symbol}) · 近 {args.window} 日")
    print(f"{'排名':<4}{'代码':<14}{'名称':<10}{'DTW 距离':>12}")
    print("-" * 44)
    for rank, (tv, dist) in enumerate(ranked[: args.top], start=1):
        print(f"{rank:<4}{tv:<14}{name_by_tv.get(tv, ''):<10}{dist:>12.4f}")
    if not ranked:
        print("（无候选）")


if __name__ == "__main__":
    main()
