#!/usr/bin/env python3
"""Fetch A-share daily bars + static snapshots from baostock into SQLite.

Daily bars go to ``daily_kline`` (OHLCV + turn/tradestatus/pct_chg/is_st/
pe_ttm/pb_mrq).  Static snapshots: ``ashare_universe`` (ipo/status),
``ashare_industry`` (申万一级), ``ashare_index_constituents`` (sz50/hs300/zz500).

Same source as the DTW similarity scan.  No East Money / Tushare / concepts.

Incremental by default for *new* days.  Rows written before the extra-field
migration have ``tradestatus IS NULL``; those codes are re-fetched from the
first incomplete date so old bars pick up turn/ST/PE.  Halted days after a
proper refill keep ``tradestatus=0`` and are not treated as gaps.

Runs on Mac mini (China-direct, no proxy — see ENVIRONMENT.md).

Usage:
  python scripts/fetch-baostock-universe.py                     # incremental + gap refill
  python scripts/fetch-baostock-universe.py --start 20250101    # floor for empty symbols
  python scripts/fetch-baostock-universe.py --from 20240101 --to 20241231
  python scripts/fetch-baostock-universe.py --codes sh.600690,sz.000338
  python scripts/fetch-baostock-universe.py --skip-refill       # new days only
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import INDEX_IDS, BaostockSource
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"
DEFAULT_START = date(2015, 1, 1)  # matches ValarmClub's default history floor


def _next_day(d: date) -> date:
    return d + timedelta(days=1)


def _parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _sql_float(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return float(value)


def _sql_int(value) -> int | None:
    n = _sql_float(value)
    return None if n is None else int(n)


def _refresh_static(source: BaostockSource, repo: Repository) -> list[dict]:
    """Pull stock-basic / industry / index snapshots. Returns type=1 rows."""
    basics = source.fetch_stock_basic(listed_only=False)
    if basics:
        n = repo.save_universe(basics)
        listed_n = sum(1 for r in basics if r.get("status") == "1")
        print(f"ashare_universe: {n} 只（上市 {listed_n}）")
    else:
        print("ashare_universe: 空")

    try:
        industry = source.fetch_industry()
        n = repo.save_industry(industry)
        named = sum(1 for r in industry if r.get("industry"))
        print(f"ashare_industry: {n} 只（有行业名 {named} · 申万一级，无概念）")
    except Exception as exc:
        print(f"ashare_industry 失败（日 K 继续）: {exc}")

    for index_id in INDEX_IDS:
        try:
            members = source.fetch_index_constituents(index_id)
            n = repo.save_index_constituents(index_id, members)
            print(f"index {index_id}: {n} 只")
        except Exception as exc:
            print(f"index {index_id} 失败（日 K 继续）: {exc}")

    return basics


def _bars_to_rows(code: str, df: pd.DataFrame) -> list[dict]:
    rows = []
    for d, r in df.iterrows():
        rows.append(
            {
                "code": code,
                "trade_date": d.strftime("%Y-%m-%d"),
                "open": _sql_float(r["open"]),
                "high": _sql_float(r["high"]),
                "low": _sql_float(r["low"]),
                "close": _sql_float(r["close"]),
                "volume": _sql_float(r["volume"]),
                "amount": _sql_float(r["amount"]),
                "turn": _sql_float(r["turn"]),
                "tradestatus": _sql_int(r["tradestatus"]),
                "pct_chg": _sql_float(r["pct_chg"]),
                "is_st": _sql_int(r["is_st"]),
                "pe_ttm": _sql_float(r["pe_ttm"]),
                "pb_mrq": _sql_float(r["pb_mrq"]),
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
    parser.add_argument(
        "--skip-refill",
        action="store_true",
        help="Do not re-fetch bars that are missing extra fields",
    )
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
            try:
                _refresh_static(source, repo)
            except Exception as exc:
                print(f"静态快照失败（--codes 日 K 继续）: {exc}")
        else:
            print("查询 A 股基本资料 / 行业 / 指数成分…")
            try:
                basics = _refresh_static(source, repo)
                codes = [
                    (r["code"], r.get("name") or "")
                    for r in basics
                    if r.get("status") == "1"
                ]
            except Exception as exc:
                print(f"stock_basic 失败，用库内上市名单: {exc}")
                codes = repo.listed_universe()
                if not codes:
                    raise
            print(f"日 K 标的: {len(codes)} 只（上市中）")

        start_floor = _parse_yyyymmdd(args.start) if args.start else DEFAULT_START

        last_dates = repo.daily_last_dates()
        extras_gaps = {} if (args.skip_refill or fixed) else repo.daily_extras_gaps()
        if extras_gaps:
            print(f"需补齐扩展字段: {len(extras_gaps)} 只（旧行 tradestatus 为空）")

        total_new = 0
        refill_n = 0
        for idx, (code, _name) in enumerate(codes, start=1):
            if fixed:
                start, end = win_from, win_to
            else:
                last = last_dates.get(code)
                gap = extras_gaps.get(code)
                if gap:
                    start = date.fromisoformat(gap)
                    refill_n += 1
                elif last:
                    start = _next_day(datetime.strptime(last, "%Y-%m-%d").date())
                else:
                    start = start_floor
                end = today
                if start > end:
                    continue

            df = source.fetch_daily(code, start, end)
            if df.empty:
                continue

            total_new += repo.save_daily_bars(_bars_to_rows(code, df))

            if idx % 200 == 0:
                print(f"  进度 {idx}/{len(codes)} · 写入 bar {total_new} · 补字段 {refill_n}")

        print(f"完成：{len(codes)} 只，写入 {total_new} 行（其中补字段 {refill_n} 只）")
    finally:
        source.logout()
        repo.close()


if __name__ == "__main__":
    main()
