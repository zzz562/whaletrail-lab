#!/usr/bin/env python3
"""WhaleTrail backtest runner — gold-first, US hedge symbols via yfinance.

Usage:
  run-backtest.py [strategy] [symbol] [start] [end] [cash] [--interval 1d]

Defaults: gold_sma GLD 2018-01-01 <today> 100000 --interval 1d

Intraday: --interval 5m|10m|15m|30m|1h runs the same engine bar-by-bar on
yfinance intraday data (5m window ≈ last 60 days; cache accumulates across
runs).  This is how the 5m live-scan parameters get their backtest: same
bars, same no-look-ahead fill-at-next-open rule.
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
from whaletrail.storage.repository import Repository
from whaletrail.strategy.registry import get_strategy_class

# Proxy config: WT_PROXY_URL → HTTPS_PROXY → default (Mac mini BoostNet).
# See docs/ENVIRONMENT.md "配置项（环境变量）".
PROXY = os.environ.get("WT_PROXY_URL") or os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7892"
os.environ.setdefault("HTTPS_PROXY", PROXY)
os.environ.setdefault("HTTP_PROXY", PROXY)

# Annualisation factors (US regular session = 390 min).
PERIODS_PER_YEAR = {
    "1d": 252,
    "1h": 252 * 6.5,
    "30m": 252 * 13,
    "15m": 252 * 26,
    "10m": 252 * 39,
    "5m": 252 * 78,
    "1m": 252 * 390,
}


def _parse_args() -> tuple[list[str], str]:
    args = sys.argv[1:]
    interval = "1d"
    if "--interval" in args:
        i = args.index("--interval")
        try:
            interval = args[i + 1]
        except IndexError:
            raise SystemExit("--interval requires a value (1d/5m/10m/15m/30m/1h)")
        del args[i : i + 2]
    return args, interval


class _StaticSource:
    """In-memory data source over a pre-fetched DataFrame."""

    def __init__(self, df):
        self._df = df

    def get_daily(self, symbol, start, end):
        return self._df


def main() -> None:
    args, interval = _parse_args()
    strategy_name = args[0] if len(args) > 0 else "gold_sma"
    symbol = args[1] if len(args) > 1 else "GLD"
    start_str = args[2] if len(args) > 2 else "2018-01-01"
    end_str = args[3] if len(args) > 3 else date.today().isoformat()
    cash = float(args[4]) if len(args) > 4 else 100_000.0

    # Validate scope (raises on A-share / HK)
    parsed = parse_symbol(symbol)
    ticker = parsed.ticker

    try:
        StrategyClass = get_strategy_class(strategy_name)
    except KeyError:
        from whaletrail.strategy.registry import list_strategies
        print(
            json.dumps(
                {"error": f"unknown strategy {strategy_name}", "known": list_strategies()},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    strategy = StrategyClass()
    if interval == "1d":
        src = DataLayer()
    else:
        from whaletrail.data.intraday import get_bars

        df = get_bars(ticker, interval,
                      date.fromisoformat(start_str), date.fromisoformat(end_str))
        if df is None or df.empty:
            print(json.dumps(
                {"error": f"no {interval} data for {ticker} {start_str}→{end_str}"},
                ensure_ascii=False))
            sys.exit(1)
        print(f"  {interval} bars: {len(df)}  "
              f"{df.index[0]} → {df.index[-1]}", file=sys.stderr)
        src = _StaticSource(df)

    bt = Backtester(
        symbols=[ticker],
        strategy=strategy,
        data_source=src,
        start=date.fromisoformat(start_str),
        end=date.fromisoformat(end_str),
        initial_cash=cash,
    )

    results = bt.run()

    # Enrich trades with realised P&L (FIFO) and compute the metric suite.
    results["trades"] = compute_trade_pnl(results.get("trades", []))
    results["metrics"] = calculate_metrics(
        results["trades"],
        [p["equity"] for p in results.get("equity_curve", [])],
        cash,
        periods_per_year=int(PERIODS_PER_YEAR.get(interval, 252)),
    )

    results["strategy"] = strategy_name
    results["symbol"] = ticker
    results["interval"] = interval
    results["role"] = parsed.role
    results["market"] = parsed.market.value
    results["start"] = start_str
    results["end"] = end_str

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    timestamp = date.today().strftime("%Y%m%d_%H%M%S")
    interval_tag = "" if interval == "1d" else f"_{interval}"
    out_file = out_dir / f"backtest_{strategy_name}_{ticker}{interval_tag}_{timestamp}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, default=str, indent=2, ensure_ascii=False)

    # Persist run / trades / snapshots to SQLite for cross-run queries.
    try:
        repo = Repository(ROOT / "results" / "whaletrail.db")
        run_id = repo.save_run(
            strategy_name,
            [ticker],
            start_str,
            end_str,
            cash,
            results["final_equity"],
            results["metrics"],
        )
        for t in results["trades"]:
            repo.save_trade(
                run_id,
                {
                    "symbol": t.get("symbol", ""),
                    "side": str(t.get("side", "")).lower(),
                    "quantity": t.get("quantity", 0.0),
                    "price": t.get("price", 0.0),
                    "commission": t.get("commission", 0.0),
                    "timestamp": t.get("date", ""),
                    "pnl": t.get("pnl"),
                },
            )
        for p in results["equity_curve"]:
            repo.save_snapshot(run_id, p["date"], p["equity"], p["cash"], {})
        repo.close()
    except Exception as e:  # persistence must never fail the backtest
        print(f"  ⚠️ 持久化到 SQLite 失败: {e}", file=sys.stderr)

    summary = {
        "file": str(out_file),
        "strategy": strategy_name,
        "symbol": ticker,
        "interval": interval,
        "role": parsed.role,
        "final_equity": round(results["final_equity"], 2),
        "total_return_pct": round(results["total_return"] * 100, 2),
        "trades": len(results["trades"]),
        "commission": round(results["total_commission"], 2),
        "sharpe": results["metrics"].get("sharpe_ratio"),
        "max_drawdown_pct": results["metrics"].get("max_drawdown"),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
