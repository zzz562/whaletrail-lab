"""Performance metrics for a completed backtest run."""

from __future__ import annotations

import math
from typing import Any


def compute_trade_pnl(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich trades with realised P&L using FIFO cost-basis matching.

    Walks the trades in chronological order, pairing each sell against
    the oldest open buy lot.  Closing (sell) trades get ``pnl`` (total
    realised profit/loss) and ``pnl_per_share`` added; buy trades are
    passed through unchanged.  Original dicts are not mutated.

    Works with either ``"buy"/"sell"`` or ``"BUY"/"SELL"`` sides.
    """
    out: list[dict[str, Any]] = []
    lots: list[dict[str, float]] = []  # open buy lots: {qty, price}

    for trade in trades:
        rec = dict(trade)
        side = str(trade.get("side", "")).lower()
        qty = float(trade.get("quantity", 0.0) or 0.0)
        price = float(trade.get("price", 0.0) or 0.0)

        if side == "buy":
            if qty > 0:
                lots.append({"qty": qty, "price": price})
        elif side == "sell":
            remaining = qty
            pnl = 0.0
            while remaining > 1e-9 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                pnl += (price - lot["price"]) * take
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] <= 1e-9:
                    lots.pop(0)
            rec["pnl"] = round(pnl, 6)
            rec["pnl_per_share"] = round(pnl / qty, 6) if qty else 0.0
        out.append(rec)

    return out


def calculate_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[float],
    initial_cash: float,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Compute a standard suite of performance metrics.

    Parameters
    ----------
    trades : list[dict]
        List of trade records.  Each dict must contain at least ``pnl``
        (realised profit/loss) and ``side`` (``"buy"`` or ``"sell"``).
    equity_curve : list[float]
        Daily or per-bar equity values in chronological order.  The first
        element should equal *initial_cash* before any trades.
    initial_cash : float
        Starting portfolio cash.
    periods_per_year : int
        Annualisation factor: 252 for daily bars; for intraday use
        252 × bars-per-session (e.g. 5m ≈ 19,656).

    Returns
    -------
    dict
        Keys:
        - ``total_return`` (float): percentage return (e.g. 12.5 for 12.5 %).
        - ``annual_return`` (float): annualised percentage return.
        - ``sharpe_ratio`` (float): annualised Sharpe ratio (risk-free = 2 %).
        - ``max_drawdown`` (float): maximum drawdown as a negative percentage.
        - ``win_rate`` (float): fraction of winning **closing** trades (0–1).
        - ``profit_factor`` (float): gross profit / gross loss.
        - ``total_trades`` (int): total number of executed trades.
        - ``total_return_abs`` (float): final equity minus initial cash.
        - ``volatility`` (float): annualised daily return volatility.
    """
    result: dict[str, Any] = {
        "total_return": 0.0,
        "annual_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "total_trades": len(trades),
        "total_return_abs": 0.0,
        "volatility": 0.0,
    }

    if not equity_curve or len(equity_curve) < 2:
        return result

    # ---- Total return ---------------------------------------------------
    final_equity = equity_curve[-1]
    total_return_abs = final_equity - initial_cash
    total_return_pct = (final_equity / initial_cash - 1.0) * 100.0

    result["total_return"] = round(total_return_pct, 4)
    result["total_return_abs"] = round(total_return_abs, 4)

    # ---- Annualised return ----------------------------------------------
    n_periods = len(equity_curve) - 1
    if n_periods > 0:
        annual_return_pct = (
            (final_equity / initial_cash) ** (float(periods_per_year) / n_periods) - 1.0
        ) * 100.0
    else:
        annual_return_pct = 0.0

    result["annual_return"] = round(annual_return_pct, 4)

    # ---- Daily (period) returns -----------------------------------------
    daily_returns: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            daily_returns.append(equity_curve[i] / prev - 1.0)
        else:
            daily_returns.append(0.0)

    # ---- Max drawdown ---------------------------------------------------
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (val - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    result["max_drawdown"] = round(max_dd * 100.0, 4)

    # ---- Sharpe ratio ---------------------------------------------------
    # Risk-free rate = 2 % per year, scaled to per-bar by periods_per_year.
    rf_per_bar = 0.02 / float(periods_per_year)
    sqrt_ppy = math.sqrt(float(periods_per_year))
    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        excess = mean_ret - rf_per_bar
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (
            len(daily_returns) - 1
        )
        std_daily = math.sqrt(variance) if variance > 0 else 0.0
        result["volatility"] = round(std_daily * sqrt_ppy * 100.0, 4)

        if std_daily > 0:
            sharpe = (excess / std_daily) * sqrt_ppy
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    result["sharpe_ratio"] = round(sharpe, 4)

    # ---- Win rate & profit factor ---------------------------------------
    # Consider *sell* trades (closing trades) for realised P&L.
    closing_trades = [t for t in trades if str(t.get("side", "")).lower() == "sell"]
    if closing_trades:
        wins = [t for t in closing_trades if (t.get("pnl") or 0.0) > 0]
        losses = [t for t in closing_trades if (t.get("pnl") or 0.0) < 0]
        win_rate = len(wins) / len(closing_trades)
        gross_profit = sum(t.get("pnl", 0.0) for t in wins)
        gross_loss = abs(sum(t.get("pnl", 0.0) for t in losses))

        result["win_rate"] = round(win_rate, 4)
        result["profit_factor"] = (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")
        )
    else:
        result["win_rate"] = 0.0
        result["profit_factor"] = 0.0

    return result
