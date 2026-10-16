#!/usr/bin/env python3
"""Backfill A-share gaps for the last N trading days (baostock).

"Day complete" = all of:
  * daily_kline distinct codes >= coverage (default 99%) of listed universe
  * no row of that day with tradestatus IS NULL (baostock extras arrived)
  * index_kline has all BENCH_INDEXES (8/8)

For each incomplete day it runs ``fetch-baostock-universe.py`` in fixed-window
mode for that single date, restricted to the missing / null-tradestatus codes
(plus the 8 benchmark indexes, which fixed mode always re-fetches).

Honors ``results/.fetch-baostock.lock``: waits (up to --wait-minutes) for a
running fetch to finish, then holds the lock itself and calls the fetcher with
``--no-lock`` so nothing else can log in to baostock meanwhile (baostock allows
one session; a second login kicks the first offline).

Usage:
  python scripts/backfill-ashare.py --check-only          # report, no fetch
  python scripts/backfill-ashare.py                        # last 5 trading days, before today
  python scripts/backfill-ashare.py --include-today        # also today (after 20:00 fallback)
  python scripts/backfill-ashare.py --days 10 --no-alert

Exit 0 = all checked days complete, 1 = still incomplete, 2 = lock busy.
Writes a ``backfill`` record into results/fetch-status.json (also on crash).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import BENCH_INDEXES  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB_PATH = ROOT / "results" / "whaletrail.db"
LOCK_PATH = ROOT / "results" / ".fetch-baostock.lock"
STATUS_PATH = ROOT / "results" / "fetch-status.json"
CN_TZ = ZoneInfo("Asia/Shanghai")


def _stamp(msg: str) -> None:
    print(f"[{datetime.now(CN_TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def write_status(ok: bool, rows: int, failures: int, elapsed: float, note: str) -> None:
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        status = {}
    now = datetime.now(CN_TZ).isoformat(timespec="seconds")
    status.setdefault("phases", {})["backfill"] = {
        "phase": "backfill",
        "ok": ok,
        "rows_written": rows,
        "failures": failures,
        "elapsed_s": round(elapsed, 1),
        "completed_at": now,
        "note": note,
    }
    status["updated_at"] = now
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def trading_days(cal: TradingCalendar, count: int, today: date, include_today: bool) -> list[date]:
    days: list[date] = []
    d = today if include_today else today - timedelta(days=1)
    while len(days) < count and (today - d).days <= 60:
        if cal.is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def assess(db: str, days: list[date], coverage: float) -> tuple[int, list[dict]]:
    """Per-day completeness → (listed_count, [report dicts])."""
    repo = Repository(db)
    try:
        listed = [c for c, _ in repo.listed_universe()]
        listed_set = set(listed)
        out = []
        for d in days:
            key = d.isoformat()
            rows = repo.conn.execute(
                "SELECT code, tradestatus FROM daily_kline WHERE trade_date = ?", (key,)
            ).fetchall()
            present = {r[0] for r in rows}
            null_ts = sorted(r[0] for r in rows if r[1] is None)
            idx = {
                r[0]
                for r in repo.conn.execute(
                    "SELECT DISTINCT code FROM index_kline WHERE trade_date = ?", (key,)
                ).fetchall()
            }
            missing = sorted(listed_set - present)
            idx_missing = sorted(set(BENCH_INDEXES) - idx)
            n = len(present)
            complete = (
                bool(listed) and n >= coverage * len(listed) and not null_ts and not idx_missing
            )
            out.append(
                {
                    "date": key,
                    "codes": n,
                    "missing": missing,
                    "null_ts": null_ts,
                    "idx": len(idx & set(BENCH_INDEXES)),
                    "idx_missing": idx_missing,
                    "complete": complete,
                }
            )
        return len(listed), out
    finally:
        repo.close()


def show(listed: int, reports: list[dict]) -> None:
    for r in reports:
        flag = "✅" if r["complete"] else "❌"
        print(
            f"  {flag} {r['date']}: 代码 {r['codes']}/{listed} · 缺 {len(r['missing'])} · "
            f"tradestatus 空 {len(r['null_ts'])} · 指数 {r['idx']}/{len(BENCH_INDEXES)}",
            flush=True,
        )


def wait_lock(minutes: float):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + minutes * 60
    while True:
        handle = LOCK_PATH.open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            handle.close()
        if time.monotonic() > deadline:
            return None
        time.sleep(30)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--days", type=int, default=5, help="trading days to check (default 5)")
    p.add_argument("--include-today", action="store_true", help="also check today")
    p.add_argument("--coverage", type=float, default=0.99)
    p.add_argument("--check-only", action="store_true", help="report only, no fetch, no lock")
    p.add_argument("--wait-minutes", type=float, default=100.0, help="max wait for the fetch lock")
    p.add_argument("--max-minutes", type=float, default=60.0, help="budget per day fetched")
    p.add_argument("--no-alert", action="store_true")
    p.add_argument("--as-of", help="pretend today is YYYY-MM-DD")
    args = p.parse_args()

    started = time.monotonic()
    today = date.fromisoformat(args.as_of) if args.as_of else datetime.now(CN_TZ).date()
    days = trading_days(TradingCalendar(), args.days, today, args.include_today)
    if not days:
        print("无法确定交易日，跳过")
        return 0

    listed, reports = assess(args.db, days, args.coverage)
    _stamp(f"补数检查（最近 {len(days)} 个交易日，门槛 {args.coverage:.0%}，上市 {listed} 只）")
    show(listed, reports)
    todo = [r for r in reports if not r["complete"]]
    if args.check_only:
        print("（--check-only：不抓取）")
        return 1 if todo else 0
    if not todo:
        write_status(True, 0, 0, time.monotonic() - started, "all complete")
        return 0

    lock = wait_lock(args.wait_minutes)
    if lock is None:
        _stamp("锁一直被占用，放弃本轮补数")
        write_status(False, 0, len(todo), time.monotonic() - started, "lock busy")
        return 2
    try:
        for r in todo:
            codes = sorted(set(r["missing"]) | set(r["null_ts"]))
            ymd = r["date"].replace("-", "")
            cmd = [
                str(ROOT / ".venv" / "bin" / "python"),
                str(ROOT / "scripts" / "fetch-baostock-universe.py"),
                "--from", ymd, "--to", ymd,
                "--no-lock", "--skip-static", "--no-alert",
                "--max-minutes", str(args.max_minutes),
            ]
            if codes:
                cmd += ["--codes", ",".join(codes)]
            else:  # only indexes missing: one cheap code keeps the fetcher off the full universe
                cmd += ["--codes", "sh.600000"]
            _stamp(f"补 {r['date']}：{len(codes)} 只 + 指数缺 {len(r['idx_missing'])} 条")
            proc = subprocess.run(cmd, cwd=ROOT)
            _stamp(f"补 {r['date']} 结束 rc={proc.returncode}")
    finally:
        lock.close()

    listed, after = assess(args.db, days, args.coverage)
    _stamp("补数后：")
    show(listed, after)
    bad = [r for r in after if not r["complete"]]
    note = "complete" if not bad else "incomplete: " + ",".join(r["date"] for r in bad)
    write_status(not bad, -1, len(bad), time.monotonic() - started, note)
    if bad and not args.no_alert:
        from whaletrail.reporting.telegram import send as tg_send

        lines = [f"⚠️ A股补数后仍不完整（{datetime.now(CN_TZ):%Y-%m-%d %H:%M}）"] + [
            f"• {r['date']}：代码 {r['codes']}/{listed} · tradestatus 空 {len(r['null_ts'])} · "
            f"指数 {r['idx']}/{len(BENCH_INDEXES)}"
            for r in bad
        ] + ["排查：tail -80 logs/backfill-ashare.log"]
        tg_send("\n".join(lines), quiet=True)
    return 1 if bad else 0


def _on_term(signum, frame):  # noqa: ARG001
    raise RuntimeError(f"killed by signal {signum}")


if __name__ == "__main__":
    import signal

    signal.signal(signal.SIGTERM, _on_term)
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # crash/kill: still leave a status record
        try:
            write_status(False, 0, 1, 0.0, f"crash: {type(exc).__name__}: {exc}"[:300])
        finally:
            raise
