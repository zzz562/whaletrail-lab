#!/usr/bin/env python3
"""One-command status probe for the A-share fetch layer (ops bot friendly).

Prints JSON to stdout; exit code 0 = healthy, 1 = problems.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.trading_calendar import TradingCalendar
from whaletrail.storage.repository import Repository

DB = ROOT / "results" / "whaletrail.db"
STATUS_PATH = ROOT / "results" / "fetch-status.json"
CN_TZ = ZoneInfo("Asia/Shanghai")


def recent_trading_days(cal, count, today):
    days = []
    d = today
    while len(days) < count and (today - d).days <= 45:
        if cal.is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--coverage", type=float, default=0.95)
    parser.add_argument("--json", action="store_true", help="print JSON (default output)")
    args = parser.parse_args()

    repo = Repository(args.db)
    cal = TradingCalendar()
    today = datetime.now(CN_TZ).date()
    days = recent_trading_days(cal, args.days, today)

    coverage = {}
    problems = []
    for d in days:
        ds = d.isoformat()
        rows = repo.conn.execute(
            "select count(*) from daily_kline where trade_date = ?", (ds,)
        ).fetchone()[0]
        idx = repo.conn.execute(
            "select count(*) from index_kline where trade_date = ?", (ds,)
        ).fetchone()[0]
        # Latest complete trading day is the universe size; use it as the target.
        coverage[ds] = {"daily_rows": rows, "index_rows": idx}
        if idx < 8:
            problems.append(f"{ds}: index {idx}/8")
    repo.close()

    if coverage:
        latest = max(coverage)
        target = coverage[latest]["daily_rows"]
        for ds, c in coverage.items():
            if target and c["daily_rows"] < target * args.coverage:
                problems.append(f"{ds}: daily {c['daily_rows']}/{target}")

    status = {}
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            status = {}

    out = {
        "healthy": not problems,
        "as_of": today.isoformat(),
        "coverage": coverage,
        "phases": status.get("phases", {}),
        "updated_at": status.get("updated_at"),
        "problems": problems,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
