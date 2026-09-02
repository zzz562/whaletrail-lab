#!/usr/bin/env python3
"""Generate a Markdown report from saved WhaleTrail watchlist snapshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.reporting.watchlist import render_watchlist_report
from whaletrail.storage.repository import Repository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(ROOT / "results" / "whaletrail.db"),
        help="SQLite database path.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "results" / "watchlist_report.md"),
        help="Markdown report output path.",
    )
    args = parser.parse_args()

    repo = Repository(args.db)
    snapshots = repo.latest_quote_snapshots()
    repo.close()

    markdown = render_watchlist_report(snapshots)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out} with {len(snapshots)} latest snapshots.")


if __name__ == "__main__":
    main()
