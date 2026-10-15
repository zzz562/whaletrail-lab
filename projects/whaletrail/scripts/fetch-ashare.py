#!/usr/bin/env python3
"""A-share multi-source daily fetch runner.

Reads ``config/ashare_sources.yaml`` and runs one phase:

- ``fast``    Eastmoney whole-market OHLCV right after close (best-effort).
- ``extras``  baostock authoritative extras + indexes + static (the authority).
- ``fallback`` same as ``extras`` (20:00 safety net).
- ``verify``  completeness gate (delegates to check-ashare-data.py).

Each phase writes ``results/fetch-status.json`` for the ops bot.  ``fast`` is
opportunistic and never fails the job; ``verify`` and ``extras`` carry the
health signal.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.eastmoney_source import EastmoneySource
from whaletrail.data.sources_config import load_config, source_config
from whaletrail.data.trading_calendar import TradingCalendar
from whaletrail.storage.repository import Repository

DB = ROOT / "results" / "whaletrail.db"
STATUS_PATH = ROOT / "results" / "fetch-status.json"
CN_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def write_status(phase: str, ok: bool, rows: int, failures: int, elapsed: float, note: str = "") -> None:
    record = {
        "phase": phase,
        "ok": ok,
        "rows_written": rows,
        "failures": failures,
        "elapsed_s": round(elapsed, 1),
        "completed_at": _now(),
        "note": note,
    }
    status: dict = {}
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            status = {}
    status.setdefault("phases", {})[phase] = record
    status["updated_at"] = _now()
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phase_fast(cfg: dict) -> None:
    src = source_config(cfg, "eastmoney")
    started = time.monotonic()
    if not src.enabled:
        write_status("fast", True, 0, 0, time.monotonic() - started, "disabled")
        return

    repo = Repository(str(DB))
    source = EastmoneySource(
        egress=src.egress,
        page_size=src.page_size,
        max_pages=src.max_pages,
        request_delay_s=src.request_delay_s,
        retries=src.retries,
        timeout_s=src.timeout_s,
        vps_proxy=src.vps_proxy,
    )

    today = datetime.now(CN_TZ).date()
    cal = TradingCalendar()
    if not cal.is_trading_day(today):
        write_status("fast", True, 0, 0, time.monotonic() - started, "休市")
        repo.close()
        return

    df = source.fetch_market_spot()
    if df.empty:
        write_status("fast", False, 0, 0, time.monotonic() - started, "eastmoney throttled/empty")
        repo.close()
        return

    trade_date = today.isoformat()
    # Do not clobber rows whose authoritative extras already arrived.
    done = {
        row["code"]
        for row in repo.conn.execute(
            "select code from daily_kline where trade_date = ? and tradestatus is not null",
            (trade_date,),
        ).fetchall()
    }
    rows = []
    for code, r in df.iterrows():
        if code in done:
            continue
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": _float(r["open"]),
                "high": _float(r["high"]),
                "low": _float(r["low"]),
                "close": _float(r["close"]),
                "volume": _float(r["volume"]),
                "amount": _float(r["amount"]),
                "turn": _float(r["turn"]),
                "tradestatus": None,
                "pct_chg": _float(r["pct_chg"]),
                "is_st": None,
                "pe_ttm": None,
                "pb_mrq": None,
            }
        )
    n = repo.save_daily_bars(rows) if rows else 0
    write_status("fast", True, n, 0, time.monotonic() - started, f"spot rows {len(df)}")
    print(f"fast: spot={len(df)} written={n} skipped={len(df) - len(rows)}", flush=True)
    repo.close()


def _float(value) -> float | None:
    import math
    import pandas as pd
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    f = float(value)
    return None if math.isnan(f) or math.isinf(f) else f


def phase_extras(cfg: dict, label: str) -> None:
    started = time.monotonic()
    cmd = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "fetch-baostock-universe.py")]
    proc = subprocess.run(cmd, cwd=ROOT)
    ok = proc.returncode == 0
    write_status(label, ok, -1, 0 if ok else 1, time.monotonic() - started, f"rc={proc.returncode}")
    print(f"{label}: rc={proc.returncode}", flush=True)


def phase_verify(cfg: dict) -> None:
    started = time.monotonic()
    days = (cfg.get("completeness") or {}).get("days", 3)
    cmd = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / "scripts" / "check-ashare-data.py"),
        "--days", str(days),
        "--no-alert",
    ]
    proc = subprocess.run(cmd, cwd=ROOT)
    ok = proc.returncode == 0
    write_status("verify", ok, -1, 0 if ok else 1, time.monotonic() - started, f"rc={proc.returncode}")
    print(f"verify: rc={proc.returncode}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=["fast", "extras", "fallback", "verify"])
    args = parser.parse_args()
    cfg = load_config()
    if args.phase in ("extras", "fallback"):
        phase_extras(cfg, args.phase)
    elif args.phase == "fast":
        phase_fast(cfg)
    else:
        phase_verify(cfg)


if __name__ == "__main__":
    main()
