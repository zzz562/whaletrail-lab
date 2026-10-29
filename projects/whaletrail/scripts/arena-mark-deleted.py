#!/usr/bin/env python3
"""Mark archived posts deleted. The original text stays; the fill stays.

  python scripts/arena-mark-deleted.py 2110766439993135507
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.arena.ledger import connect  # noqa: E402
from whaletrail.arena.paths import db_path  # noqa: E402
from whaletrail.arena.rules import CN  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("post_ids", nargs="+")
    args = parser.parse_args()
    now = datetime.now(CN).isoformat(timespec="seconds")
    conn = connect(db_path())
    for post_id in args.post_ids:
        cur = conn.execute(
            """
            UPDATE arena_posts SET deleted_at = ?
            WHERE post_id = ? AND deleted_at IS NULL
            """,
            (now, post_id),
        )
        print(f"{post_id} {'已标删除' if cur.rowcount else '没有这条或本来就标过'}")
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
