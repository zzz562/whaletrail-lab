#!/usr/bin/env python3
"""Lock arena orders from JSON call sheets dropped in the inbox.

Capture time is when this script reads the file. Entry day still comes from
JSON created_at (post clock ≤ 09:25 Asia/Shanghai → that session open).
Sheets for an entry day may be locked until that day's 15:30; later reads
are archived as late_capture and not traded. Grok uses the same window.

  python scripts/arena-lock.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.arena.ledger import ingest, load_universe, open_db_and_roster  # noqa: E402
from whaletrail.arena.paths import inbox_dir, processed_dir, rejected_dir, roster_path, db_path  # noqa: E402
from whaletrail.arena.rules import CN  # noqa: E402
from whaletrail.data.trading_calendar import TradingCalendar  # noqa: E402


def _safe(post_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in post_id)


def _reject(path: Path, reason: str) -> None:
    rejected_dir().mkdir(parents=True, exist_ok=True)
    dest = rejected_dir() / path.name
    path.replace(dest)
    dest.with_suffix(dest.suffix + ".error.txt").write_text(reason + "\n", encoding="utf-8")


def main() -> int:
    now = datetime.now(CN)
    inbox = inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(inbox.glob("*.json"))
    if not files:
        print(f"inbox 空 {inbox}")
        return 0
    conn = open_db_and_roster(db_path(), roster_path(), now)
    by_code, by_name = load_universe(conn)
    is_trading = TradingCalendar().is_trading_day
    locked = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            _reject(path, f"json: {exc}")
            print(f"拒绝 {path.name} json 无法解析")
            continue
        result = ingest(conn, payload, now, by_code, by_name, is_trading)
        if not result.get("ok"):
            _reject(path, result.get("error") or "拒绝")
            print(f"拒绝 {path.name} {result.get('error')}")
            continue
        processed_dir().mkdir(parents=True, exist_ok=True)
        dest = processed_dir() / f"{_safe(result['post_id'])}.json"
        path.replace(dest)
        locked += int(result.get("orders") or 0)
        flag = "重复" if result.get("duplicate") else result.get("stance")
        print(
            f"归档 {result['post_id']} {flag} 进场 {result.get('entry_date') or '—'} "
            f"新单 {result.get('orders')} 拒绝 {','.join(result.get('rejects') or []) or '—'}"
        )
    print(f"锁定买单 {locked} 笔 @ {now.strftime('%Y-%m-%d %H:%M')}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
