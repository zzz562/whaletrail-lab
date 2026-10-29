#!/usr/bin/env python3
"""Settle locked arena orders against completed daily bars.

Default end date is the latest 沪深300 bar in ``index_kline``. Missing stock
bars stay locked and are retried on the next run.

  python scripts/arena-settle.py
  python scripts/arena-settle.py --through 2026-10-20
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.arena.ledger import open_db_and_roster, settle_through  # noqa: E402
from whaletrail.arena.paths import db_path, roster_path  # noqa: E402
from whaletrail.arena.rules import CN, HS300  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", help="结算到这个交易日 YYYY-MM-DD，缺省取沪深300最新日")
    args = parser.parse_args()
    now = datetime.now(CN)
    conn = open_db_and_roster(db_path(), roster_path(), now)
    if args.through:
        through = date.fromisoformat(args.through)
    else:
        row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM index_kline WHERE code = ?", (HS300,)
        ).fetchone()
        if row["d"] is None:
            print("没有沪深300日 K，跳过结算")
            conn.close()
            return 0
        through = date.fromisoformat(row["d"])
    reports = settle_through(conn, through, TradingCalendar().is_trading_day)
    for item in reports:
        if item.get("skipped"):
            continue
        print(f"{item['date']} 成交 {item['fills']} 卖出 {item['exits']}")
    if not reports:
        print(f"没有待结算的单，截止 {through.isoformat()}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
