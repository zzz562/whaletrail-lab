#!/usr/bin/env python3
"""Nightly A-share data completeness check for the baostock pipeline.

Cron runs this after the last fetch of the day (21:40 on weekdays).  It compares
the most recent *expected* trading days (SZSE calendar, ``data/trading_calendar.py``)
against what ``daily_kline`` / ``index_kline`` actually hold, and pushes a
Telegram alert when a day is missing or thin — the case a silently failing or
hanging fetch leaves behind.

Exit code 0 = healthy, 1 = problems found (alert attempted).

Usage:
  python scripts/check-ashare-data.py
  python scripts/check-ashare-data.py --days 5 --coverage 0.9
  python scripts/check-ashare-data.py --db /tmp/other.db --no-alert
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import BENCH_INDEXES  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402
from whaletrail.reporting.telegram import send as tg_send  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB_PATH = ROOT / "results" / "whaletrail.db"
CN_TZ = ZoneInfo("Asia/Shanghai")


def recent_trading_days(cal: TradingCalendar, count: int, today: date) -> list[date]:
    """The *count* most recent trading days on or before *today*, oldest first."""
    days: list[date] = []
    d = today
    while len(days) < count and (today - d).days <= 45:
        if cal.is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path")
    parser.add_argument("--days", type=int, default=3, help="trading days to verify (default 3)")
    parser.add_argument(
        "--coverage", type=float, default=0.95, help="min stored/universe ratio (default 0.95)"
    )
    parser.add_argument("--no-alert", action="store_true", help="print only, never push")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    days = recent_trading_days(TradingCalendar(), args.days, now.date())
    if not days:
        print("⚠️ 无法确定最近交易日（交易日历不可用），跳过检查")
        return 0
    since = days[0].strftime("%Y-%m-%d")

    repo = Repository(args.db)
    try:
        listed = len(repo.listed_universe())
        stock_rows = {
            r["trade_date"]: r["n"]
            for r in repo.conn.execute(
                "SELECT trade_date, COUNT(*) AS n FROM daily_kline"
                " WHERE trade_date >= ? GROUP BY trade_date",
                (since,),
            ).fetchall()
        }
        index_rows = {
            r["trade_date"]: r["n"]
            for r in repo.conn.execute(
                "SELECT trade_date, COUNT(DISTINCT code) AS n FROM index_kline"
                " WHERE trade_date >= ? GROUP BY trade_date",
                (since,),
            ).fetchall()
        }
        stock_max = repo.conn.execute(
            "SELECT COALESCE(MAX(trade_date), '-') FROM daily_kline"
        ).fetchone()[0]
        index_max = repo.conn.execute(
            "SELECT COALESCE(MAX(trade_date), '-') FROM index_kline"
        ).fetchone()[0]
    finally:
        repo.close()

    problems: list[str] = []
    if listed == 0:
        problems.append("ashare_universe 为空（库未初始化或被清空）")
    else:
        floor = int(listed * args.coverage)
        for d in days:
            key = d.strftime("%Y-%m-%d")
            n = stock_rows.get(key, 0)
            if n < floor:
                problems.append(f"日线 {key}：{n}/{listed} 只（低于 {args.coverage:.0%} 门槛）")
            m = index_rows.get(key, 0)
            if m < len(BENCH_INDEXES):
                problems.append(f"基准指数 {key}：{m}/{len(BENCH_INDEXES)} 条缺失")

    if problems:
        text = "\n".join(
            [
                f"⚠️ A股数据不完整（{now:%Y-%m-%d %H:%M}）",
                *[f"• {p}" for p in problems],
                f"最近写入：daily_kline {stock_max} / index_kline {index_max}",
                "排查：tail -50 logs/fetch-baostock.log",
            ]
        )
        print(text)
        if args.no_alert:
            print("（--no-alert：未推送）")
        elif tg_send(text, quiet=True):
            print("Telegram 告警已发送")
        else:
            print("⚠️ Telegram 告警发送失败（见上）")
        return 1

    print(
        f"OK：{days[0].strftime('%Y-%m-%d')} → {days[-1].strftime('%Y-%m-%d')} "
        f"{len(days)} 个交易日日线与基准指数完整（{listed} 只上市）· "
        f"最近写入 daily_kline {stock_max} / index_kline {index_max}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
