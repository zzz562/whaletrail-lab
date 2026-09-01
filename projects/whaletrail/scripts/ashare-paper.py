#!/usr/bin/env python3
"""A-share low-frequency paper loop (tvscreener snapshot accumulation).

Usage:
  python scripts/ashare-paper.py                    # scan all A-share watchlist items
  python scripts/ashare-paper.py --symbol SSE:601899  # single symbol

Data path: tvscreener snapshot → SQLite ``quote_snapshots`` →
``build_daily_history`` → SMA 20/50 cross signal → paper position tracking
(``results/ashare_paper_state.json``).

Execution model (aligned with the 15:30 CST cron, no look-ahead):
  - Signals are computed on bars **through yesterday**; fills are booked at
    **today's close** (the last accumulated snapshot ≈ 15:00 close).
  - Costs: commission 万2.5 (¥5 floor) per side, 卖出印花税 0.05%, slippage
    0.1% per side — see the constants below.
  - 涨跌停: a close pinned at the limit blocks adverse fills (can't buy a
    limit-up seal, can't sell a limit-down seal); the intent stays in
    ``state["pending"]`` and retries next trading day.  创业板/科创板 20%,
    主板 10% (北交所 30% not handled — no watchlist exposure).
  - T+1: shares bought today cannot be sold today.

Runs are gated on A-share trading days (SZSE official calendar via
``whaletrail/data/trading_calendar.py``, covering weekends, holidays and
make-up days) and the 09:30–16:00 CST snapshot window
(``whaletrail/engine/session.py``); out-of-session runs skip without
recording snapshots or firing signals.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whaletrail.data.history import build_daily_history
from whaletrail.data.trading_calendar import TradingCalendar
from whaletrail.data.tvscreener_source import TVScreenerSource
from whaletrail.data.watchlist import by_tv_symbol, load_watchlist
from whaletrail.engine.session import CN_TZ, ashare_hours
from whaletrail.indicators import cross_signal, volume_zscore, whale_flag
from whaletrail.storage.repository import Repository

DB_PATH = ROOT / "results" / "whaletrail.db"
STATE_FILE = ROOT / "results" / "ashare_paper_state.json"
WATCHLIST = ROOT / "config" / "watchlist.yaml"

# ── A-share paper cost / sizing model ────────────────────────────
COMMISSION_RATE = 0.00025   # 万2.5, per side
MIN_COMMISSION = 5.0        # ¥5 floor per side
STAMP_TAX_SELL = 0.0005     # 印花税, sell only
SLIPPAGE = 0.001            # per side
PAPER_NOTIONAL = 50_000.0   # CNY per position
LOT = 100                   # A股整手

# Signal needs SMA-50 history; 60 completed bars + today's row.
MIN_HISTORY = 61


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str))


def _migrate_positions(state: dict) -> bool:
    """Fill qty/entry_cost on LONG rows written before sizing was persisted.

    Old state had ``side`` + ``entry_price`` only, so the daily line showed
    ``持有 0 股``. Infer shares from the same ¥5万 / 100-share lot rule as
    ``_try_buy``. Drop the row if we still cannot size it.
    """
    dirty = False
    for sym, pos in list(state.get("positions", {}).items()):
        if not isinstance(pos, dict) or pos.get("side") != "LONG":
            continue
        qty = pos.get("qty")
        if qty is not None and float(qty) > 0:
            if "entry_cost" not in pos:
                entry = float(pos.get("entry_price") or 0)
                pos["entry_cost"] = round(
                    max(float(qty) * entry * COMMISSION_RATE, MIN_COMMISSION), 2
                )
                dirty = True
            continue
        entry = float(pos.get("entry_price") or 0)
        if entry <= 0:
            print(f"⚠️ drop ghost position {sym}: no qty and no entry_price")
            state["positions"].pop(sym, None)
            dirty = True
            continue
        inferred = int(PAPER_NOTIONAL / entry / LOT) * LOT
        if inferred <= 0:
            print(f"⚠️ drop ghost position {sym}: cannot size at {entry}")
            state["positions"].pop(sym, None)
            dirty = True
            continue
        pos["qty"] = inferred
        pos.setdefault(
            "entry_cost",
            round(max(inferred * entry * COMMISSION_RATE, MIN_COMMISSION), 2),
        )
        print(
            f"⚠️ migrated {sym}: inferred qty={inferred} "
            f"from ¥{PAPER_NOTIONAL:.0f} / {entry}"
        )
        dirty = True
    return dirty


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            state.setdefault("positions", {})
            state.setdefault("pending", {})
            state.setdefault("trades", [])
            if _migrate_positions(state):
                save_state(state)
            return state
        except Exception:
            pass
    return {"positions": {}, "pending": {}, "trades": []}


def limit_pct(tv_symbol: str) -> float:
    """Daily price-limit fraction: 20% for 创业板(300/301)/科创板(68x), else 10%."""
    code = tv_symbol.split(":", 1)[-1]
    if code.startswith(("300", "301", "68")):
        return 0.20
    return 0.10


def fetch_and_save(items, repo) -> None:
    source = TVScreenerSource()
    symbols = [item.tv_symbol for item in items]
    snapshots = source.get_quotes(symbols)
    item_by_tv = by_tv_symbol(items)

    rows = []
    for snap in snapshots:
        data = snap.to_dict()
        data["tv_symbol"] = snap.symbol
        item = item_by_tv.get(snap.symbol)
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
        rows.append(data)

    if rows:
        repo.save_quote_snapshots(rows)
    print(f"fetched {len(rows)} snapshots")


def _commission(notional: float) -> float:
    return max(notional * COMMISSION_RATE, MIN_COMMISSION)


def _try_buy(sym: str, today_close: float, today_iso: str, signal_date: str,
             state: dict) -> tuple[bool, str]:
    price = today_close * (1.0 + SLIPPAGE)
    qty = int(PAPER_NOTIONAL / price / LOT) * LOT
    if qty <= 0:
        return True, f"资金不足一手（需约 {price * LOT:,.0f} 元），放弃买入"
    notional = qty * price
    cost = _commission(notional)
    state["positions"][sym] = {
        "side": "LONG",
        "qty": qty,
        "entry_price": round(price, 4),
        "entry_cost": round(cost, 2),
        "entry_date": today_iso,
        "signal_date": signal_date,
    }
    return True, f"🟢 BUY {qty} 股 @ {price:.2f}（佣金 ¥{cost:.2f}）"


def _try_sell(sym: str, today_close: float, today_iso: str,
              state: dict) -> tuple[bool, str]:
    pos = state["positions"].get(sym)
    if pos is None:
        return True, "无持仓，忽略 SELL"
    if pos.get("entry_date") == today_iso:
        return False, "T+1：今日买入不可卖出，顺延"
    price = today_close * (1.0 - SLIPPAGE)
    qty = float(pos.get("qty", 0))
    notional = qty * price
    cost = _commission(notional)
    stamp = notional * STAMP_TAX_SELL
    gross = (price - float(pos["entry_price"])) * qty
    net = gross - float(pos.get("entry_cost", 0.0)) - cost - stamp
    state["positions"].pop(sym, None)
    state["trades"].append(
        {
            "symbol": sym,
            "qty": qty,
            "entry_price": pos["entry_price"],
            "entry_date": pos["entry_date"],
            "exit_price": round(price, 4),
            "exit_date": today_iso,
            "costs": round(float(pos.get("entry_cost", 0.0)) + cost + stamp, 2),
            "pnl_net": round(net, 2),
        }
    )
    pct = (price / float(pos["entry_price"]) - 1.0) * 100.0
    return True, (
        f"🔴 SELL {int(qty)} 股 @ {price:.2f}（{pct:+.2f}%，"
        f"净盈亏 ¥{net:+,.0f}，费用 ¥{cost + stamp:.2f}）"
    )


def process_item(item, hist, state, today_iso: str) -> list[str]:
    """Run the signal / pending / fill pipeline for one watchlist item."""
    sym = item.tv_symbol
    lines: list[str] = []
    if hist.empty or len(hist) < MIN_HISTORY:
        return [f"{item.name} ({sym}): 历史不足（{len(hist)} 天），跳过"]

    sig_bars = hist.iloc[:-1]  # completed through yesterday
    today_close = float(hist["close"].iloc[-1])
    yday_close = float(hist["close"].iloc[-2])
    signal_date = str(sig_bars.index[-1].date())

    closes = [float(x) for x in sig_bars["close"].tolist()]
    sig = cross_signal(closes, 20, 50)
    pos = state["positions"].get(sym)
    holding = pos is not None and pos.get("side") == "LONG"

    limit = limit_pct(sym)
    today_chg = today_close / yday_close - 1.0 if yday_close > 0 else 0.0

    # Whale proxy (observational, computed on full history incl. today):
    # volume z-score surge + 20d closing breakout.
    full_closes = [float(x) for x in hist["close"].tolist()]
    full_vols = [float(x) for x in hist["volume"].tolist()]
    z = volume_zscore(full_vols, 20)
    whale = whale_flag(full_closes, full_vols, 20)
    state.setdefault("whale", {})[sym] = {
        "date": today_iso,
        "volume_z": round(z, 2) if z is not None else None,
        "flag": whale,
    }

    # Desired action from the fresh cross signal.
    desired: str | None = None
    if sig == "BUY" and not holding:
        desired = "BUY"
    elif sig == "SELL" and holding:
        desired = "SELL"

    # A pending intent contradicted by a fresh opposite signal is cancelled.
    pend = state["pending"].get(sym)
    if pend is not None and desired is not None and desired != pend.get("signal"):
        lines.append(f"↩️ 挂单 {pend['signal']} 被新信号 {desired} 撤销")
        state["pending"].pop(sym, None)
        pend = None

    # Pending intent takes priority; a fresh same-direction signal just
    # confirms it.
    action = pend["signal"] if pend is not None else desired
    filled_or_done = True
    if action == "BUY":
        if today_chg >= limit * 0.995:
            filled_or_done = False
            lines.append(f"🚫 涨停（{today_chg:+.1%}≈{limit:.0%} 板）无法买入，挂单待成交")
        else:
            filled_or_done, msg = _try_buy(sym, today_close, today_iso, signal_date, state)
            lines.append(msg)
    elif action == "SELL":
        if today_chg <= -limit * 0.995:
            filled_or_done = False
            lines.append(f"🚫 跌停（{today_chg:+.1%}≈-{limit:.0%} 板）无法卖出，挂单待成交")
        else:
            filled_or_done, msg = _try_sell(sym, today_close, today_iso, state)
            lines.append(msg)

    if action is not None:
        if filled_or_done:
            state["pending"].pop(sym, None)
        else:
            state["pending"][sym] = {"signal": action, "signal_date": signal_date}

    if not lines:
        if holding:
            unreal = (today_close / float(pos["entry_price"]) - 1.0) * 100.0
            status = f"持有 {int(float(pos.get('qty', 0)))} 股 {unreal:+.2f}%"
        else:
            status = "空仓"
        z_txt = f"{z:+.1f}σ" if z is not None else "—"
        lines.append(f"➖ {today_close:.2f}  {sig or '—'}  {status}  量比 {z_txt}")

    if whale:
        lines.append(f"🔥 跟庄代理：放量突破（量比 z={z:.1f}，20 日收盘新高）")

    return [f"{item.name} ({sym}) {line}" for line in lines]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Single tvscreener symbol, e.g. SSE:601899")
    args = parser.parse_args()

    today = datetime.now(CN_TZ).date()
    # Heal old state even when the session gate skips (qty-less LONG rows).
    state = load_state()
    if not ashare_hours():
        print(
            f"⏸ 非 A 股交易时段（盘外），跳过 | 当前 "
            f"{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M')} CST"
        )
        return
    if not TradingCalendar().is_trading_day(today):
        print(f"⏸ 今日非 A 股交易日（周末/节假日），跳过 | {today.isoformat()}")
        return

    items = load_watchlist(WATCHLIST)
    a_items = [i for i in items if i.market == "china" and i.tradable]
    if args.symbol:
        a_items = [i for i in a_items if i.tv_symbol == args.symbol]

    if not a_items:
        print("No A-share watchlist items found.")
        return

    repo = Repository(DB_PATH)

    print(f"\n🅰  A股低频 paper  |  {date.today().isoformat()}  "
          f"（信号=昨收 · 成交=今收 · 含费用）")
    try:
        fetch_and_save(a_items, repo)
    except Exception as exc:
        print(f"  ⚠️ 快照拉取失败: {exc}（继续用已有历史）")

    print()
    for item in a_items:
        hist = build_daily_history(DB_PATH, item.tv_symbol)
        for line in process_item(item, hist, state, date.today().isoformat()):
            print(line)

    repo.close()
    save_state(state)


if __name__ == "__main__":
    main()
