#!/usr/bin/env python3
"""Fetch A-share daily bars from baostock into SQLite ``daily_kline``.

Phase 1 of the chart-similarity feature.  Free, tokenless baostock backfills
whole-market A-share daily OHLCV, which the DTW scan reads to find stocks
whose recent chart resembles a reference.  Runs on Mac mini (China-direct,
no proxy — see ENVIRONMENT.md).

Incremental by default: skips symbols already synced through today, so the
nightly cron only pulls the newest bars.  ``--start`` forces a backfill floor.

Usage:
  python scripts/fetch-baostock-universe.py                     # incremental all
  python scripts/fetch-baostock-universe.py --start 20260101    # backfill from date
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="Backfill floor YYYYMMDD (default 20150101)")
    parser.add_argument("--codes", help="Comma-separated baostock codes (skip universe query)")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    args = parser.parse_args()

    repo = Repository(args.db)
    source = BaostockSource()
    today = datetime.now().date()

    try:
        source.login()

        if args.codes:
            codes = [(c.strip(), "") for c in args.codes.split(",") if c.strip()]
        else:
            print("查询 A 股全市场代码…")
            codes = source.list_universe(today)
            if codes:
                repo.save_universe(codes)
            print(f"universe: {len(codes)} 只（交易中）")

        start_floor = (
            datetime.strptime(args.start, "%Y%m%d").date()
            if args.start
            else DEFAULT_START
        )

        total_new = 0
        for idx, (code, _name) in enumerate(codes, start=1):
            last = repo.daily_last_date(code)
            start = _next_day(datetime.strptime(last, "%Y-%m-%d").date()) if last else start_floor
            if start > today:
                continue

            df = source.fetch_daily(code, start, today)
            if df.empty:
                continue

            rows = [
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
                for d, r in df.iterrows()
            ]
            total_new += repo.save_daily_bars(rows)

            if idx % 200 == 0:
                print(f"  进度 {idx}/{len(codes)} · 新增 bar {total_new}")

        print(f"完成：{len(codes)} 只，写入 {total_new} 行")
    finally:
        source.logout()
        repo.close()


if __name__ == "__main__":
    main()
