#!/usr/bin/env python3
"""Fetch A-share daily bars + static snapshots from baostock into SQLite.

Daily bars go to ``daily_kline`` (OHLCV + turn/tradestatus/pct_chg/is_st/
pe_ttm/pb_mrq).  Static snapshots: ``ashare_universe`` (ipo/status),
``ashare_industry`` (证监会行业分类), ``ashare_index_constituents``
(sz50/hs300/zz500).  Benchmark index daily bars go to ``index_kline``.

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
  python scripts/fetch-baostock-universe.py --no-alert          # no Telegram push

Every query is bounded by ``--query-timeout`` seconds — baostock's socket reader
loops on ``recv`` forever once the server half-closes the connection — and the
whole run by ``--max-minutes``.  Failures are counted and pushed to Telegram
unless ``--no-alert``; a lock file keeps two cron runs from overlapping.
"""

from __future__ import annotations

import argparse
import fcntl
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.baostock_source import BENCH_INDEXES, INDEX_IDS, BaostockSource
from whaletrail.reporting.telegram import send as tg_send
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"
LOCK_PATH = ROOT / "results" / ".fetch-baostock.lock"
DEFAULT_START = date(2015, 1, 1)  # matches ValarmClub's default history floor


class QueryTimeout(Exception):
    """A single baostock query exceeded its time budget."""


def _alarm(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
    raise QueryTimeout("single query exceeded the time budget")


def _stamp(msg: str) -> None:
    """Print *msg* with a timestamp; the cron log carries none of its own."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _acquire_lock(path: Path):
    """Take an exclusive non-blocking lock; ``None`` when another run holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _alert(
    *,
    stock_failures: list[str],
    index_failures: list[str],
    total_new: int,
    newest: str | None,
    elapsed: float,
    truncated: bool,
    args,
) -> None:
    """Push a Telegram alert when the run looks unhealthy."""
    if args.no_alert:
        return
    problems: list[str] = []
    if truncated:
        problems.append(f"超过 {args.max_minutes:g} 分钟预算被截断，剩余标的未抓")
    if len(stock_failures) > args.fail_threshold:
        problems.append(
            f"单只抓取失败 {len(stock_failures)} 只（阈值 {args.fail_threshold}）"
        )
    if index_failures:
        problems.append(
            f"基准指数失败 {len(index_failures)} 条：" + "、".join(index_failures[:5])
        )
    if not problems:
        return

    lines = [
        f"⚠️ A股 baostock 抓取异常（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
        *[f"• {p}" for p in problems],
        f"写入 {total_new} 行 · 最新日期 {newest or '-'} · 耗时 {elapsed / 60:.1f} 分钟",
    ]
    if stock_failures:
        lines.append("失败样例：" + "、".join(stock_failures[:5]))
    lines.append("排查：tail -50 logs/fetch-baostock.log")

    if tg_send("\n".join(lines), quiet=True):
        _stamp("已推送 Telegram 告警")
    else:
        _stamp("⚠️ Telegram 告警发送失败")


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
        print(f"ashare_industry: {n} 只（有行业名 {named} · 证监会分类，无概念）")
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


def _index_bars_to_rows(code: str, name: str, df: pd.DataFrame) -> list[dict]:
    rows = []
    for d, r in df.iterrows():
        rows.append(
            {
                "code": code,
                "name": name,
                "trade_date": d.strftime("%Y-%m-%d"),
                "open": _sql_float(r["open"]),
                "high": _sql_float(r["high"]),
                "low": _sql_float(r["low"]),
                "close": _sql_float(r["close"]),
                "volume": _sql_float(r["volume"]),
                "amount": _sql_float(r["amount"]),
                "pct_chg": _sql_float(r["pct_chg"]),
            }
        )
    return rows


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
    parser.add_argument(
        "--query-timeout", type=int, default=90, help="单次 baostock 查询上限秒数（默认 90）"
    )
    parser.add_argument(
        "--max-minutes", type=float, default=90.0, help="整轮抓取时间预算（分钟，默认 90）"
    )
    parser.add_argument(
        "--fail-threshold", type=int, default=300, help="单只失败超过该值时告警（默认 300）"
    )
    parser.add_argument("--no-alert", action="store_true", help="不推送 Telegram 告警")
    parser.add_argument("--no-lock", action="store_true", help="不检查并发锁")
    args = parser.parse_args()

    if (args.date_from is None) ^ (args.date_to is None):
        parser.error("--from and --to must be used together")

    signal.signal(signal.SIGALRM, _alarm)
    lock = None if args.no_lock else _acquire_lock(LOCK_PATH)
    if lock is None and not args.no_lock:
        _stamp("另一轮抓取仍在运行（锁被占用），本轮跳过")
        return

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

    started = time.monotonic()
    deadline = started + args.max_minutes * 60
    try:
        _stamp(f"抓取开始（预算 {args.max_minutes:g} 分钟，单次查询上限 {args.query_timeout}s）")
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
        failures: list[str] = []
        newest: str | None = None
        truncated = False
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

            try:
                signal.setitimer(signal.ITIMER_REAL, args.query_timeout)
                df = source.fetch_daily(code, start, end)
            except Exception as exc:  # 网络错误 / 查询超时：记下这一只，继续其余
                failures.append(f"{code}: {exc}")
                continue
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)

            if df.empty:
                continue

            last_bar = df.index[-1].strftime("%Y-%m-%d")
            if newest is None or last_bar > newest:
                newest = last_bar
            total_new += repo.save_daily_bars(_bars_to_rows(code, df))

            if idx % 200 == 0:
                print(
                    f"  进度 {idx}/{len(codes)} · 写入 bar {total_new} · 补字段 {refill_n}"
                    f" · 失败 {len(failures) + len(source.failures)}"
                )

            if time.monotonic() > deadline:
                truncated = True
                _stamp(
                    f"超出 {args.max_minutes:g} 分钟预算，提前结束（已处理 {idx}/{len(codes)}）"
                )
                break

        _stamp(
            f"完成：{len(codes)} 只，写入 {total_new} 行（其中补字段 {refill_n} 只）· "
            f"单只失败 {len(failures) + len(source.failures)} 只 · 最新 {newest or '-'}"
        )

        # Benchmark index daily bars → index_kline (same window/incremental rules).
        idx_total = 0
        idx_failures: list[str] = []
        idx_last = {} if fixed else repo.index_last_dates()
        for code, name in BENCH_INDEXES.items():
            if fixed:
                idx_start, idx_end = win_from, win_to
            else:
                last = idx_last.get(code)
                if last:
                    idx_start = _next_day(datetime.strptime(last, "%Y-%m-%d").date())
                else:
                    idx_start = start_floor
                idx_end = today
            if idx_start > idx_end:
                continue
            try:
                signal.setitimer(signal.ITIMER_REAL, args.query_timeout)
                df = source.fetch_index_daily(code, idx_start, idx_end)
            except Exception as exc:  # 单条指数失败不再中断整轮
                idx_failures.append(f"{code}: {exc}")
                continue
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)

            if df.empty:
                continue
            idx_total += repo.save_index_bars(_index_bars_to_rows(code, name, df))
        _stamp(
            f"index_kline: {idx_total} 行（{len(BENCH_INDEXES)} 条基准指数）· "
            f"失败 {len(idx_failures)} 条"
        )

        elapsed = time.monotonic() - started
        _stamp(f"抓取结束，耗时 {elapsed / 60:.1f} 分钟")
        _alert(
            stock_failures=[
                *failures,
                *(f"{f['code']} ({f['error_msg']})" for f in source.failures),
            ],
            index_failures=idx_failures,
            total_new=total_new,
            newest=newest,
            elapsed=elapsed,
            truncated=truncated,
            args=args,
        )
    except Exception as exc:  # 硬失败（登录/静态表/写库）也要告警，别只留一条 traceback
        if not args.no_alert:
            tg_send(
                f"⚠️ A股 baostock 抓取中断（{datetime.now().strftime('%Y-%m-%d %H:%M')}）\n"
                f"{type(exc).__name__}: {exc}\n"
                f"排查：tail -50 logs/fetch-baostock.log",
                quiet=True,
            )
        raise
    finally:
        source.logout()
        repo.close()


if __name__ == "__main__":
    main()
