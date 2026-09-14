#!/usr/bin/env python3
"""Fetch A-share daily bars from baostock into SQLite ``daily_kline``.

Phase 1 of the chart-similarity feature.  Free, tokenless baostock backfills
whole-market A-share daily OHLCV, which the DTW scan reads to find stocks
whose recent chart resembles a reference.  Runs on Mac mini (China-direct,
no proxy — see ENVIRONMENT.md).

Incremental by default: skips symbols already synced through today, so the
nightly cron only pulls the newest bars.  ``--start`` forces a backfill floor
for symbols with no rows yet.

``--from`` / ``--to`` (YYYYMMDD) fetch a fixed calendar window for every
symbol and upsert into ``daily_kline`` (unadjusted adjustflag=3).  Use this
for year-by-year historical backfill without touching newer bars' forward
incremental logic.

Usage:
  python scripts/fetch-baostock-universe.py                     # incremental all
  python scripts/fetch-baostock-universe.py --start 20250101    # floor for empty symbols
  python scripts/fetch-baostock-universe.py --from 20240101 --to 20241231
  python scripts/fetch-baostock-universe.py --codes sh.600690,sz.000338
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import BaostockSource
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"
DEFAULT_START = date(2015, 1, 1)  # matches ValarmClub's default history floor


def _next_day(d: date) -> date:
    return d + timedelta(days=1)


def _parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _rows_from_df(code: str, df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for d, r in df.iterrows():
        rows.append(
            {
                "code": code,
                "trade_date": d.strftime("%Y-%m-%d"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
                "amount": None if pd.isna(r["amount"]) else float(r["amount"]),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="Incremental floor YYYYMMDD for empty symbols (default 20150101)")
    parser.add_argument("--from", dest="date_from", help="Fixed-window start YYYYMMDD (inclusive)")
    parser.add_argument("--to", dest="date_to", help="Fixed-window end YYYYMMDD (inclusive)")
    parser.add_argument("--codes", help="Comma-separated baostock codes (skip universe query)")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    args = parser.parse_args()

    if (args.date_from is None) ^ (args.date_to is None):
        parser.error("--from and --to must be used together")

    repo = Repository(args.db)
    source = BaostockSource()
    today = datetime.now().date()
    fixed = args.date_from is not None and args.date_to is not None
    if fixed:
        win_from = _parse_yyyymmdd(args.date_from)
        win_to = _parse_yyyymmdd(args.date_to)
        if win_to < win_from:
            parser.error("--to must be >= --from")
        print(f"固定窗模式（不复权 upsert）：{win_from.isoformat()} → {win_to.isoformat()}")

    try:
        source.login()

        if args.codes:
            codes = [(c.strip(), "") for c in args.codes.split(",") if c.strip()]
        else:
            print("查询 A 股全市场代码…")
            codes = source.list_universe()
            if codes:
                repo.save_universe(codes)
            print(f"universe: {len(codes)} 只（交易中）")

        start_floor = (
            _parse_yyyymmdd(args.start) if args.start else DEFAULT_START
        )

        total_new = 0
        for idx, (code, _name) in enumerate(codes, start=1):
            if fixed:
                start, end = win_from, win_to
            else:
                last = repo.daily_last_date(code)
                start = _next_day(datetime.strptime(last, "%Y-%m-%d").date()) if last else start_floor
                end = today
                if start > end:
                    continue

            df = source.fetch_daily(code, start, end)
            if df.empty:
                continue

            total_new += repo.save_daily_bars(_rows_from_df(code, df))

            if idx % 200 == 0:
                print(f"  进度 {idx}/{len(codes)} · 写入 bar {total_new}")

        print(f"完成：{len(codes)} 只，写入 {total_new} 行")
    finally:
        source.logout()
        repo.close()


if __name__ == "__main__":
    main()
