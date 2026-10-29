#!/usr/bin/env python3
"""Print the arena board. ``--json`` is for the Grok bot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.arena.ledger import connect  # noqa: E402
from whaletrail.arena.paths import db_path  # noqa: E402
from whaletrail.arena.report import leaderboard, locked_orders  # noqa: E402


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    conn = connect(db_path())
    board = leaderboard(conn)
    locked = locked_orders(conn)
    if args.json:
        counts = {
            "active": sum(1 for r in board if r["status"] == "active"),
            "eliminated": [r["handle"] for r in board if r["status"] == "eliminated"],
            "removed": [r["handle"] for r in board if r["status"] == "removed"],
        }
        print(json.dumps({"board": board, "locked": locked, "counts": counts}, ensure_ascii=False, indent=2))
        conn.close()
        return 0
    if not board:
        print("名册还没有写入。先跑 arena-lock.py")
        conn.close()
        return 0
    print(f"{'席':<6}{'账号':<22}{'笔':>4}{'胜率':>8}{'超额':>9}{'排序分':>9}{'净值':>12}")
    for row in board:
        equity = "—" if row["equity"] is None else f"{row['equity']:,.0f}"
        print(
            f"{row['seat']:<6}{row['handle']:<22}{row['n']:>4}"
            f"{_pct(row['win_rate']):>8}{_pct(row['mean_excess']):>9}"
            f"{_pct(row['score']):>9}{equity:>12}"
        )
    active = [r for r in board if r["status"] == "active"]
    out = [r["handle"] for r in board if r["status"] == "eliminated"]
    gone = [r["handle"] for r in board if r["status"] == "removed"]
    print(f"在赛 {len(active)} 人；出局 {len(out)} 人；移出（零发帖）{len(gone)} 人")
    if out:
        print("  出局: " + ", ".join(out))
    if gone:
        print("  移出: " + ", ".join(gone))
    if locked:
        print("已锁定未结算:")
        for row in locked:
            print(f"  {row['entry_date']} {row['side']} {row['handle']} {row['code']} {row.get('name') or ''}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
