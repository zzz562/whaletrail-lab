#!/usr/bin/env python3
"""Parameter-sensitivity sweep for the SMA-cross family (gold_sma).

Answers the trader's question: is 20/50 a robust plateau or a lucky peak?
Runs the strategy over a fast×slow grid on one symbol and prints a table
next to a buy & hold benchmark.  Read it like this:

  - Broad plateau (most neighbours of 20/50 also profitable, beating B&H)
    → parameters are robust; keep them.
  - Lone peak (only 20/50 works) → overfit; distrust the backtest.

Default window starts 2011 so the 2011–2015 gold bear is in sample —
trend systems earn their keep (or die) in the bear, not the bull.
Sweep results are NOT persisted to the runs table (no SQLite pollution).

Usage:
  python scripts/param-sweep.py [symbol] [start] [end] [cash] [--interval 1d]
  python scripts/param-sweep.py GLD 2011-01-01 2026-08-12
  python scripts/param-sweep.py GLD 2026-06-20 2026-08-18 --interval 5m

--interval runs the same grid on intraday bars (yfinance 5m window ≈ 60
days; cache accumulates), which is how the 5m live-scan parameters get
their robustness check.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.layer import DataLayer
from whaletrail.data.symbols import parse_symbol
from whaletrail.engine.backtester import Backtester
from whaletrail.metrics.performance import calculate_metrics, compute_trade_pnl
from whaletrail.strategy.strategies.gold_sma import GoldSMAStrategy

# Proxy config: WT_PROXY_URL → HTTPS_PROXY → default. See docs/ENVIRONMENT.md.
PROXY = os.environ.get("WT_PROXY_URL") or os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7892"
os.environ.setdefault("HTTPS_PROXY", PROXY)
os.environ.setdefault("HTTP_PROXY", PROXY)

FAST_GRID = [10, 15, 20, 25, 30]
SLOW_GRID = [40, 50, 60, 75, 100, 150, 200]
MIN_GAP = 10  # require slow >= fast + MIN_GAP


class _StaticSource:
    """In-memory DataSource over a pre-fetched DataFrame (fetch once)."""

    def __init__(self, df):
        self._df = df

    def get_daily(self, symbol, start, end):
        return self._df


def _buy_and_hold(df, cash: float, ppy: int = 252) -> dict:
    closes = [float(x) for x in df["close"].tolist()]
    equity = [cash * c / closes[0] for c in closes]
    metrics = calculate_metrics([], equity, cash, periods_per_year=ppy)
    return {
        "params": "buy&hold",
        "total_return": metrics["total_return"],
        "annual_return": metrics["annual_return"],
        "sharpe": metrics["sharpe_ratio"],
        "max_dd": metrics["max_drawdown"],
        "trades": 1,
    }


def main() -> None:
    args = sys.argv[1:]
    interval = "1d"
    if "--interval" in args:
        i = args.index("--interval")
        try:
            interval = args[i + 1]
        except IndexError:
            raise SystemExit("--interval requires a value (1d/5m/10m/15m/30m/1h)")
        del args[i : i + 2]

    symbol = args[0] if len(args) > 0 else "GLD"
    start_str = args[1] if len(args) > 1 else "2011-01-01"
    end_str = args[2] if len(args) > 2 else date.today().isoformat()
    cash = float(args[3]) if len(args) > 3 else 100_000.0

    ticker = parse_symbol(symbol).ticker
    if interval == "1d":
        df = DataLayer().get_daily(ticker, date.fromisoformat(start_str),
                                   date.fromisoformat(end_str))
        ppy = 252
    else:
        from whaletrail.data.intraday import get_bars
        df = get_bars(ticker, interval,
                      date.fromisoformat(start_str), date.fromisoformat(end_str))
        bars_per_day = {"1h": 6.5, "30m": 13, "15m": 26, "10m": 39, "5m": 78, "1m": 390}
        ppy = int(252 * bars_per_day.get(interval, 1))
    if df is None or df.empty:
        print(json.dumps({"error": f"no {interval} data for {ticker}"}, ensure_ascii=False))
        sys.exit(1)
    print(f"  {len(df)} {interval} bars: {df.index[0]} → {df.index[-1]}", file=sys.stderr)
    src = _StaticSource(df)

    rows: list[dict] = []
    for fast in FAST_GRID:
        for slow in SLOW_GRID:
            if slow < fast + MIN_GAP:
                continue
            bt = Backtester(
                symbols=[ticker],
                strategy=GoldSMAStrategy(fast=fast, slow=slow),
                data_source=src,
                start=date.fromisoformat(start_str),
                end=date.fromisoformat(end_str),
                initial_cash=cash,
            )
            res = bt.run()
            trades = compute_trade_pnl(res["trades"])
            m = calculate_metrics(
                trades, [p["equity"] for p in res["equity_curve"]], cash,
                periods_per_year=ppy,
            )
            rows.append(
                {
                    "params": f"{fast}/{slow}",
                    "fast": fast,
                    "slow": slow,
                    "total_return": m["total_return"],
                    "annual_return": m["annual_return"],
                    "sharpe": m["sharpe_ratio"],
                    "max_dd": m["max_drawdown"],
                    "trades": m["total_trades"],
                }
            )
            print(f"  done {fast}/{slow}", file=sys.stderr)

    bh = _buy_and_hold(df, cash, ppy)

    print(f"\n## {ticker} gold_sma 参数敏感性 | {interval} | {start_str} → {end_str} | ${cash:,.0f}\n")
    print("| 参数 | 总收益% | 年化% | Sharpe | 最大回撤% | 交易数 |")
    print("|---|---|---|---|---|---|")
    print(
        f"| **B&H 基准** | {bh['total_return']:.1f} | {bh['annual_return']:.1f} "
        f"| {bh['sharpe']:.2f} | {bh['max_dd']:.1f} | 1 |"
    )
    for r in sorted(rows, key=lambda x: (x["fast"], x["slow"])):
        mark = " ← 现役" if r["params"] == "20/50" else ""
        print(
            f"| {r['params']} | {r['total_return']:.1f} | {r['annual_return']:.1f} "
            f"| {r['sharpe']:.2f} | {r['max_dd']:.1f} | {r['trades']} |{mark}"
        )

    beat = sum(1 for r in rows if r["sharpe"] > bh["sharpe"])
    sharpes = sorted(r["sharpe"] for r in rows)
    median = sharpes[len(sharpes) // 2]
    print(
        f"\n网格中位 Sharpe {median:.2f}；{beat}/{len(rows)} 组参数跑赢 B&H"
        f"（Sharpe {bh['sharpe']:.2f}）。"
    )

    interval_tag = "" if interval == "1d" else f"_{interval}"
    out = ROOT / "results" / f"param_sweep_{ticker}{interval_tag}_{date.today():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"symbol": ticker, "interval": interval, "start": start_str,
         "end": end_str, "cash": cash, "buy_and_hold": bh, "grid": rows},
        ensure_ascii=False, indent=2,
    ))
    print(f"saved → {out}")


if __name__ == "__main__":
    main()
