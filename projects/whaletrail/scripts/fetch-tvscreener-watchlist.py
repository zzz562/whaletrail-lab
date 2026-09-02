#!/usr/bin/env python3
"""Fetch the WhaleTrail TradingView/tvscreener watchlist snapshot.

Usage:
  python scripts/fetch-tvscreener-watchlist.py
  python scripts/fetch-tvscreener-watchlist.py --save-db --report results/watchlist_report.md
  python scripts/fetch-tvscreener-watchlist.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.tvscreener_source import TVScreenerSource, snapshots_to_frame
from whaletrail.data.watchlist import by_tv_symbol, load_watchlist, tv_symbols
from whaletrail.reporting.watchlist import render_watchlist_report
from whaletrail.storage.repository import Repository


def enrich_snapshot(snapshot, item_by_tv: dict) -> dict:
    """Merge a QuoteSnapshot with local watchlist metadata."""
    data = snapshot.to_dict()
    item = item_by_tv.get(snapshot.symbol)
    data["tv_symbol"] = snapshot.symbol
    if item is not None:
        data.update(
            {
                "local_name": item.name,
                "yahoo_symbol": item.yahoo_symbol,
                "asset_class": item.asset_class,
                "exchange": item.exchange or data.get("exchange"),
                "tradable": item.tradable,
            }
        )
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watchlist",
        default=str(ROOT / "config" / "watchlist.yaml"),
        help="Path to watchlist YAML/JSON file.",
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / "results" / "whaletrail.db"),
        help="SQLite database path for saving quote snapshots.",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Persist fetched snapshots into SQLite.",
    )
    parser.add_argument(
        "--report",
        help="Optional Markdown report output path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a table.",
    )
    args = parser.parse_args()

    items = load_watchlist(Path(args.watchlist))
    symbols = tv_symbols(items)
    item_by_tv = by_tv_symbol(items)

    source = TVScreenerSource()
    snapshots = source.get_quotes(symbols)
    rows = [enrich_snapshot(snapshot, item_by_tv) for snapshot in snapshots]

    if args.save_db:
        repo = Repository(args.db)
        repo.save_quote_snapshots(rows)
        repo.close()

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_watchlist_report(rows), encoding="utf-8")

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    df = snapshots_to_frame(snapshots)
    if df.empty:
        print("No snapshots returned.")
        return

    local_names = {item.tv_symbol: item.name for item in items}
    asset_classes = {item.tv_symbol: item.asset_class for item in items}
    df.insert(1, "local_name", df["symbol"].map(local_names))
    df.insert(2, "asset_class", df["symbol"].map(asset_classes))
    columns = [
        "symbol",
        "local_name",
        "asset_class",
        "description",
        "close",
        "change_percent",
        "volume",
        "rsi",
        "sma20",
        "sma50",
        "sma200",
        "endpoint",
    ]
    print(df[[column for column in columns if column in df.columns]].to_string(index=False))

    if args.save_db:
        print(f"\nSaved {len(rows)} snapshots to {args.db}")
    if args.report:
        print(f"Wrote report to {args.report}")


if __name__ == "__main__":
    main()
