#!/usr/bin/env python3
"""Seed A-share daily history from TradingView via tvdatafeed.

Usage:
  python scripts/seed-ashare-history.py [--bars 5000]

Requires the (non-PyPI) tvdatafeed package:
  .venv/bin/pip install "git+https://github.com/rongardF/tvdatafeed.git"

Backfills ``quote_snapshots`` with daily OHLCV bars so that
``build_daily_history`` (used by ashare-paper.py) has history immediately,
without waiting months for snapshot accumulation.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Proxy config: WT_PROXY_URL → HTTPS_PROXY → default. See docs/ENVIRONMENT.md.
PROXY = (
    os.environ.get("WT_PROXY_URL")
    or os.environ.get("HTTPS_PROXY")
    or "http://127.0.0.1:7892"
)
os.environ.setdefault("HTTPS_PROXY", PROXY)
os.environ.setdefault("HTTP_PROXY", PROXY)

from tvDatafeed import Interval, TvDatafeed  # noqa: E402

from whaletrail.data.watchlist import load_watchlist  # noqa: E402
from whaletrail.storage.repository import Repository  # noqa: E402

DB_PATH = ROOT / "results" / "whaletrail.db"
WATCHLIST = ROOT / "config" / "watchlist.yaml"
SEED_SOURCE = "tvdatafeed"


def tv_symbol_parts(tv_symbol: str) -> tuple[str, str]:
    exchange, _, symbol = tv_symbol.partition(":")
    if not symbol:
        raise ValueError(f"expected EXCHANGE:SYMBOL, got {tv_symbol!r}")
    return exchange, symbol


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=5000)
    args = parser.parse_args()

    items = load_watchlist(WATCHLIST)
    a_items = [i for i in items if i.market == "china" and i.tradable]
    if not a_items:
        print("No A-share watchlist items found.")
        return

    tv = TvDatafeed()
    repo = Repository(DB_PATH)
    total = 0

    for item in a_items:
        exchange, symbol = tv_symbol_parts(item.tv_symbol)
        print(f"{item.name} ({item.tv_symbol}) …", end=" ", flush=True)
        try:
            df = tv.get_hist(
                symbol=symbol,
                exchange=exchange,
                interval=Interval.in_daily,
                n_bars=args.bars,
            )
        except Exception as exc:
            print(f"fetch failed: {exc}")
            continue

        if df is None or df.empty:
            print("no data")
            continue

        # Idempotent re-runs: replace previous seed rows for this symbol.
        repo.conn.execute(
            "DELETE FROM quote_snapshots WHERE tv_symbol = ? AND source = ?",
            (item.tv_symbol, SEED_SOURCE),
        )
        repo.conn.commit()

        rows = []
        for ts, row in df.iterrows():
            rows.append(
                {
                    "tv_symbol": item.tv_symbol,
                    "local_name": item.name,
                    "yahoo_symbol": item.yahoo_symbol,
                    "asset_class": item.asset_class,
                    "exchange": item.exchange,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "source": SEED_SOURCE,
                    "endpoint": "history",
                    "timestamp": ts.to_pydatetime().isoformat(),
                }
            )

        n = repo.save_quote_snapshots(rows)
        total += n
        print(f"{n} rows")

    repo.close()
    print(f"done: {total} rows for {len(a_items)} symbols")


if __name__ == "__main__":
    main()
