#!/usr/bin/env python3
"""A-share whole-market 10-minute bars (route B + route A).

``loop`` (launchd KeepAlive): during A-share trading minutes, poll the Sina
batch spot endpoint once a minute and aggregate locally into 10-minute bars
(route B).  After the 15:00 close it calibrates the day with Sina ``scale=10``
kline (route A) and prunes to the last 5 trading days.  Alerts only on
failures.

``eod``: run the close calibration + prune once (manual / cron safety net).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.sina_minute_source import SinaMinuteSource  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402
from whaletrail.reporting.telegram import send as tg_send  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB = ROOT / "results" / "whaletrail.db"
STATUS_PATH = ROOT / "results" / "minute-status.json"
CN_TZ = ZoneInfo("Asia/Shanghai")
SCALE = 10
KEEP_DAYS = 5
BARS_PER_DAY = 24
EOD_FAIL_RATIO = 0.05
EOD_WORKERS = 3
MORNING_END = dtime(11, 30)
AFTERNOON_START = dtime(13, 0)
CLOSE = dtime(15, 0)


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _in_session(now: datetime) -> bool:
    t = now.time()
    return (dtime(9, 30) < t <= MORNING_END) or (AFTERNOON_START < t <= CLOSE)


def _bar_ts(now: datetime) -> datetime:
    """Map a sample time to its 10-minute bar end time (Sina convention).

    Morning: 09:31→09:40, 09:40→09:40, 11:30→11:30.
    Afternoon: 13:01→13:10, 15:00→15:00.
    """
    t = now.time()
    if t <= MORNING_END:
        base = now.replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        base = now.replace(hour=13, minute=0, second=0, microsecond=0)
    seconds = (now - base).total_seconds()
    steps = max(1, math.ceil(seconds / (SCALE * 60)))
    return base + timedelta(minutes=steps * SCALE)


def write_status(
    phase: str, ok: bool, rows: int, failures: int, elapsed: float, note: str = ""
) -> None:
    record = {
        "phase": phase,
        "ok": ok,
        "rows_written": rows,
        "failures": failures,
        "elapsed_s": round(elapsed, 1),
        "completed_at": _now().isoformat(timespec="seconds"),
        "note": note,
    }
    status: dict = {}
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            status = {}
    status.setdefault("phases", {})[phase] = record
    status["updated_at"] = _now().isoformat(timespec="seconds")
    STATUS_PATH.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_universe(repo: Repository) -> list[str]:
    rows = repo.conn.execute(
        "SELECT code FROM ashare_universe WHERE status = '1'"
    ).fetchall()
    return [r["code"] for r in rows if r["code"][:3] in ("sh.", "sz.")]


def flush_bar(
    repo: Repository,
    buf: dict[str, list[dict]],
    bar_ts: datetime,
    source: str = "snapshot",
) -> int:
    """Aggregate buffered samples into one 10-minute bar and upsert it."""
    ts = bar_ts.strftime("%Y-%m-%d %H:%M:%S")
    trade_date = ts[:10]
    rows = []
    for code, samples in buf.items():
        if not samples:
            continue
        prices = [s["price"] for s in samples]
        volume = samples[-1]["cum_volume"] - samples[0]["cum_volume"]
        amount = samples[-1]["cum_amount"] - samples[0]["cum_amount"]
        if volume <= 0:
            continue
        rows.append(
            {
                "code": code,
                "ts": ts,
                "trade_date": trade_date,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": volume,
                "amount": amount,
                "source": source,
            }
        )
    return repo.save_minute_bars(rows) if rows else 0


def run_eod(
    repo: Repository, source: SinaMinuteSource, codes: list[str]
) -> tuple[int, int]:
    """Calibrate the last 5 days with true ``scale=10`` kline (route A)."""
    started = time.monotonic()
    fails = 0
    bars = 0
    batch: list[dict] = []

    def one(code: str) -> tuple[str, list[dict]]:
        return code, source.fetch_kline_bars(
            code, scale=SCALE, datalen=KEEP_DAYS * BARS_PER_DAY
        )

    with ThreadPoolExecutor(max_workers=EOD_WORKERS) as pool:
        futures = {pool.submit(one, c): c for c in codes}
        for fut in as_completed(futures):
            code, rows = fut.result()
            if not rows:
                fails += 1
                continue
            bars += len(rows)
            for b in rows:
                batch.append(
                    {
                        "code": code,
                        "ts": b["ts"],
                        "trade_date": b["ts"][:10],
                        "open": b["open"],
                        "high": b["high"],
                        "low": b["low"],
                        "close": b["close"],
                        "volume": b["volume"],
                        "amount": b["amount"],
                        "source": "kline",
                    }
                )
            if len(batch) >= 20000:
                repo.save_minute_bars(batch)
                batch = []
    if batch:
        repo.save_minute_bars(batch)

    ratio = fails / len(codes) if codes else 0.0
    ok = ratio < EOD_FAIL_RATIO
    write_status(
        "eod", ok, bars, fails, time.monotonic() - started,
        f"codes={len(codes)} bars={bars} fail_ratio={ratio:.3f}",
    )
    print(f"eod: bars={bars} fails={fails} ratio={ratio:.3f}", flush=True)
    return bars, fails


def run_prune(repo: Repository, cal: TradingCalendar) -> int:
    """Delete minute bars older than the last ``KEEP_DAYS`` trading days."""
    started = time.monotonic()
    days: list[str] = []
    d = _now().date()
    while len(days) < KEEP_DAYS:
        if cal.is_trading_day(d):
            days.append(d.isoformat())
        d -= timedelta(days=1)
    cutoff = days[-1]
    n = repo.prune_minute_bars(cutoff)
    write_status("prune", True, n, 0, time.monotonic() - started, f"cutoff={cutoff}")
    print(f"prune: removed {n} rows before {cutoff}", flush=True)
    return n


def _sleep_to_next_minute() -> None:
    now = _now()
    nxt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    delay = (nxt - _now()).total_seconds()
    time.sleep(max(1.0, min(delay, 60.0)))


def loop(alert: bool) -> None:
    repo = Repository(str(DB))
    source = SinaMinuteSource()
    cal = TradingCalendar()
    codes = load_universe(repo)
    day_of_universe = _now().date()
    buf: dict[str, list[dict]] = {}
    current_bar: datetime | None = None
    calibrated = False
    poll_fail_streak = 0
    print(f"minute loop: universe={len(codes)} db={DB.name}", flush=True)

    while True:
        now = _now()
        today = now.date()

        if today != day_of_universe:
            day_of_universe = today
            calibrated = False
            codes = load_universe(repo)
            print(f"minute loop: new day, universe={len(codes)}", flush=True)

        if not cal.is_trading_day(today):
            time.sleep(60)
            continue

        if _in_session(now):
            snaps = source.fetch_snapshots(codes)
            if not snaps:
                poll_fail_streak += 1
                if poll_fail_streak == 3 and alert:
                    tg_send(
                        "⚠️ A股分钟线：连续 3 次全市场快照为空（新浪批量源疑似限流或断网）"
                    )
            else:
                poll_fail_streak = 0

            bar = _bar_ts(now)
            if current_bar is None or bar != current_bar:
                if current_bar is not None:
                    n = flush_bar(repo, buf, current_bar)
                    if n:
                        write_status("loop", True, n, 0, 0.0,
                                     f"closed {current_bar:%H:%M}")
                current_bar = bar
                buf = {}
            for code, snap in snaps.items():
                buf.setdefault(code, []).append(snap)
            _sleep_to_next_minute()
            continue

        if not calibrated and now.time() > CLOSE:
            if current_bar is not None:
                flush_bar(repo, buf, current_bar)
                current_bar = None
                buf = {}
            calibrated = True
            bars, fails = run_eod(repo, source, codes)
            run_prune(repo, cal)
            if alert:
                if fails > 0:
                    tg_send(
                        f"✅ A股分钟线收盘校准完成：{bars} 根 bar，失败 {fails} 只，"
                        f"失败率 {fails / len(codes):.1%}"
                    )
                else:
                    tg_send(f"✅ A股分钟线收盘校准完成：{bars} 根 bar，全部成功。")
        time.sleep(60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["loop", "eod"])
    parser.add_argument("--no-alert", action="store_true", help="suppress Telegram alerts")
    args = parser.parse_args()
    alert = not args.no_alert

    if args.command == "loop":
        loop(alert)
        return

    repo = Repository(str(DB))
    source = SinaMinuteSource()
    cal = TradingCalendar()
    codes = load_universe(repo)
    bars, fails = run_eod(repo, source, codes)
    run_prune(repo, cal)
    if alert:
        tg_send(f"✅ A股分钟线手动校准完成：{bars} 根 bar，失败 {fails} 只。")
    repo.close()


if __name__ == "__main__":
    main()
