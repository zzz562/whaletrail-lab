#!/usr/bin/env python3
"""WhaleTrail Paper Live — multi-strategy intraday scan + Telegram.

Scans on **intraday bars** (default 5m; ``INTERVAL`` below, ``10m`` is
resampled from 5m).  Signals come from the last **completed** bar — the
still-forming bar is excluded — and paper fills are booked at the current
price (≈ next bar's open), mirroring the backtester's
"signal at bar N close → fill at bar N+1 open" rule.  The same parameters
on the same bars can thus be validated with
``run-backtest.py ... --interval 5m``.

Scans are gated on NYSE regular hours (whaletrail/engine/session.py), so
weekends/holidays/off-hours never produce signals or paper trades.

Usage:
  python scripts/paper-live.py tick
  python scripts/paper-live.py loop --interval 600
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Config ───────────────────────────────────────────────────────
# Primary: gold multi-strategy panel
# Hedge: SPY context (fewer strategies)
SCAN_JOBS = [
    {
        "symbol": "GLD",
        "label": "🥇 黄金",
        "role": "primary",
        "market": "us",
        "strategies": [
            "gold_sma",
            "gold_sma_v2",
            "bollinger",
            "momentum",
            "turtle",
        ],
    },
    {
        "symbol": "SPY",
        "label": "📊 标普",
        "role": "hedge",
        "market": "us",
        "strategies": ["ma_cross", "momentum"],
    },
]

STATE_FILE = ROOT / "results" / "paper_live_state.json"
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "5102138680")
# Telegram pushes retired 2026-09-29: the GLD/SPY observation signals were not
# acted on.  The scan itself keeps running because results/paper_live_state.json
# feeds the dashboard's gold paper page.  Re-enable with WT_TG_PUSH=1.
PUSH_ENABLED = os.environ.get("WT_TG_PUSH", "1").strip().lower() not in {"0", "false", "no"}
# Proxy config: WT_PROXY_URL → HTTPS_PROXY → default. See docs/ENVIRONMENT.md.
PROXY = os.environ.get("WT_PROXY_URL") or os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7892"

os.environ.setdefault("HTTPS_PROXY", PROXY)
os.environ.setdefault("HTTP_PROXY", PROXY)

# Scan timeframe.  10m bars are resampled from 5m by the intraday module.
INTERVAL = "5m"
INTERVAL_MIN = int(INTERVAL[:-1]) if INTERVAL.endswith("m") else 60
# Observation-only mode (SCOPE decision 14, 2026-08-18): the 5m grid sweep
# showed no positive-expectancy SMA-cross parameters (0/35 beat B&H, median
# Sharpe -1.24).  Signals are pushed tagged as observation — not entries.
OBSERVATION_ONLY = True
# GLD share ≈ 0.0905 oz of spot gold (decays slowly with the expense ratio).
GLD_SPOT_FACTOR = 11.05
LOOKBACK_DAYS = 10
MIN_BARS = 260  # covers SMA-200 strategies with room to spare
# Reject the series when one completed bar moves more than this vs the
# previous close — on GLD/SPY that means a bad tick, not a trade.
MAX_BAR_MOVE = 0.25

# ── Data ─────────────────────────────────────────────────────────
from whaletrail.data.intraday import fetch_bars  # noqa: E402


def series(df: Any, col: str) -> list[float]:
    return [float(x) for x in df[col].dropna().tolist()]


def validate_bars(df: Any, symbol: str) -> Optional[str]:
    """Data-quality gate.  Returns a rejection reason, or None when clean.

    Signals are only as good as their input: refuse to trade (even paper)
    on bars that fail basic sanity checks.
    """
    if len(df) < MIN_BARS:
        return f"only {len(df)} bars (< {MIN_BARS})"
    closes = df["close"]
    if closes.isna().any():
        return "NaN close in series"
    if (closes <= 0).any():
        return "non-positive close in series"
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
    move = abs(last / prev - 1.0)
    if move > MAX_BAR_MOVE:
        return (
            f"last completed bar moved {move:.1%} vs prior close — "
            "bad tick or corrupt feed?"
        )
    return None


def split_forming(df: Any) -> tuple[Any, Optional[float]]:
    """Split off the still-forming bar.

    Returns (completed_bars_df, live_price).  live_price is the forming
    bar's current close, or the last completed close when the final bar is
    already complete.
    """
    last_ts = df.index[-1]
    now_et = datetime.now(US_TZ).replace(tzinfo=None)
    bucket = now_et.replace(second=0, microsecond=0)
    bucket = bucket.replace(minute=bucket.minute - bucket.minute % INTERVAL_MIN)
    if last_ts == bucket:
        return df.iloc[:-1], float(df["close"].iloc[-1])
    return df, float(df["close"].iloc[-1])


def last_bar_is_today(df: Any) -> bool:
    """Freshness guard: the newest bar must belong to today's US session.

    Covers holidays and stale data: yfinance returns the previous session's
    bars when the market is closed.
    """
    last = df.index[-1]
    last_date = last.date() if last.tzinfo is None else last.astimezone(US_TZ).date()
    return last_date == datetime.now(US_TZ).date()


# ── Signal dispatch (single source: strategy registry) ──────────
from whaletrail.engine.session import US_TZ, us_session  # noqa: E402
from whaletrail.strategy.base import position_key  # noqa: E402
from whaletrail.strategy.registry import _build_signal_registry  # noqa: E402

SIGNAL_FNS = _build_signal_registry()


# ── State / Telegram ─────────────────────────────────────────────
def load_state() -> dict:
    state: dict = {"positions": {}, "last_signals": {}, "last_snapshot": {}}
    if STATE_FILE.exists():
        try:
            state.update(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    # Migration (2026-08-18): position slots are per (symbol, strategy).
    # Legacy keys ("GLD", "GLD_bb", "GLD_turtle") mixed strategies'
    # bookkeeping and are dropped; atr_stops from that era go with them.
    positions = state.get("positions", {})
    legacy = [k for k in positions if "|" not in k]
    if legacy:
        print(f"  ⚠️ dropping legacy position keys: {sorted(legacy)}")
        for k in legacy:
            positions.pop(k, None)
    stops = state.get("atr_stops", {})
    if any("|" not in k for k in stops):
        print("  ⚠️ dropping legacy atr_stops (rebuilt on next BUY)")
        state["atr_stops"] = {k: v for k, v in stops.items() if "|" in k}
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, default=str, indent=2))


def send_telegram(text: str) -> bool:
    if not PUSH_ENABLED:
        # Report success so signal dedup and paper bookkeeping stay unchanged.
        print(f"  · push disabled (WT_TG_PUSH=0)：{text.splitlines()[0][:70]}")
        return True
    if not TG_TOKEN:
        print("  ⚠️ TG_BOT_TOKEN not set")
        return False
    import urllib.request

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = json.dumps(
        {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    ).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"  ⚠️ Telegram: {e}")
        return False


def update_pos_after_signal(
    state: dict, key: str, signal: str, fill_price: float, signal_bar: str
) -> None:
    if signal == "BUY":
        state.setdefault("positions", {})[key] = {
            "side": "LONG",
            "entry_price": fill_price,
            "entry_date": date.today().isoformat(),
            "signal_bar": signal_bar,
        }
    else:
        state.setdefault("positions", {}).pop(key, None)


# ── Scan ─────────────────────────────────────────────────────────
def tick() -> Optional[str]:
    """Run one scan pass; return a skip note when every job was skipped
    (used by loop() to avoid repeating the same message)."""
    state = load_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    day = date.today().isoformat()
    all_lines: list[str] = []
    skip_reasons: list[str] = []

    # ── Pass 1: fetch + validate ─────────────────────────────────
    prepared: dict[str, dict] = {}
    for job in SCAN_JOBS:
        symbol = job["symbol"]
        if job.get("market", "us") == "us" and not us_session():
            skip_reasons.append("session")
            continue
        end = date.today()
        df = fetch_bars(symbol, INTERVAL, end - timedelta(days=LOOKBACK_DAYS), end + timedelta(days=1))
        if df is None or df.empty:
            skip_reasons.append("no_data")
            print(f"\n[{now}] {job['label']} {symbol}\n  ⚠️ no data")
            continue
        if not last_bar_is_today(df):
            skip_reasons.append("stale")
            continue
        completed, live_price = split_forming(df)
        if live_price <= 0:
            skip_reasons.append("invalid")
            print(f"\n[{now}] {job['label']} {symbol}\n  ⚠️ data rejected: non-positive price")
            continue
        problem = validate_bars(completed, symbol)
        if problem is not None:
            skip_reasons.append("invalid")
            print(f"\n[{now}] {job['label']} {symbol}\n  ⚠️ data rejected: {problem}")
            continue
        prepared[symbol] = {"completed": completed, "price": live_price, "job": job}

    # Cross-instrument corruption guard: two different symbols must never
    # print the exact same prices (observed in a broken feed).
    fps: dict[tuple[float, float], str] = {}
    for symbol, pack in prepared.items():
        fp = (round(pack["price"], 4),
              round(float(pack["completed"]["close"].iloc[-1]), 4))
        other = fps.get(fp)
        if other is not None:
            print(
                f"  ⚠️ {symbol} and {other} report identical prices "
                f"{fp} — feed corrupt, skipping both"
            )
            prepared.pop(symbol, None)
            prepared.pop(other, None)
        else:
            fps[fp] = symbol

    # ── Pass 2: signals on completed bars, fills at current price ──
    for symbol, pack in prepared.items():
        job, completed, price = pack["job"], pack["completed"], pack["price"]
        label = job["label"]
        signal_bar = str(completed.index[-1])

        closes = series(completed, "close")
        highs = series(completed, "high")
        lows = series(completed, "low")
        if not closes:
            print(f"  ⚠️ empty closes for {symbol}")
            continue
        last_close = closes[-1]
        print(
            f"\n[{now}] {label} {symbol}  ${price:.2f}  "
            f"bars={len(closes)} ({INTERVAL})"
        )

        votes: dict[str, str] = {}
        for name in job["strategies"]:
            fn = SIGNAL_FNS.get(name)
            if not fn:
                continue
            try:
                sig = fn(closes, highs, lows, state, symbol)
            except Exception as e:
                print(f"  ⚠️ {name}: {e}")
                sig = None
            if sig is None:
                print(f"  · {name}: —")
                continue
            print(f"  · {name}: {sig}")
            votes[name] = sig

            # Dedup push per strategy/day
            last_key = f"{symbol}|{name}|{sig}"
            if state.get("last_signals", {}).get(last_key) == day:
                continue

            emoji = "🟢" if sig == "BUY" else "🔴"
            obs = "🔎 观察信号（无正期望，勿跟单）\n" if OBSERVATION_ONLY else ""
            spot = (
                f"≈ 现货 ${price * GLD_SPOT_FACTOR:,.0f}/oz\n"
                if symbol == "GLD" else ""
            )
            msg = (
                f"{obs}{emoji} *{label} {symbol}* `{name}` → *{sig}*\n"
                f"信号 bar {signal_bar}（{INTERVAL}）· 按现价 ${price:.2f} 记账\n"
                f"{spot}"
                f"{now} · WhaleTrail Live"
            )
            if send_telegram(msg):
                state.setdefault("last_signals", {})[last_key] = day
                update_pos_after_signal(
                    state, position_key(symbol, name), sig, price, signal_bar
                )

        if job["role"] == "primary" and votes:
            buys = sum(1 for v in votes.values() if v == "BUY")
            sells = sum(1 for v in votes.values() if v == "SELL")
            n = len(votes)
            consensus = "BUY" if buys > sells and buys >= 2 else (
                "SELL" if sells > buys and sells >= 2 else "MIXED"
            )
            gold_votes = [f"{k}:{v}" for k, v in votes.items()]
            snap_key = f"{symbol}|consensus|{consensus}"
            if consensus != "MIXED" and state.get("last_signals", {}).get(snap_key) != day:
                obs = "🔎 观察模式（无正期望，勿跟单）\n" if OBSERVATION_ONLY else ""
                body = (
                    f"{obs}📡 *GLD 策略面板共识 → {consensus}*\n"
                    f"现价 ${price:.2f}（≈ 现货 ${price * GLD_SPOT_FACTOR:,.0f}/oz）"
                    f" | 票数 BUY {buys}/{n} SELL {sells}/{n}\n"
                    f"{', '.join(gold_votes)}\n"
                    f"{now}"
                )
                if send_telegram(body):
                    state.setdefault("last_signals", {})[snap_key] = day
            all_lines.append(
                f"{symbol} ${price:.2f} consensus={consensus} {gold_votes}"
            )
        elif votes:
            all_lines.append(f"{symbol} ${price:.2f} {votes}")

        state.setdefault("last_snapshot", {})[symbol] = {
            "price": price,
            "prev_bar_close": last_close,
            "signal_bar": signal_bar,
            "interval": INTERVAL,
            "mode": "observation" if OBSERVATION_ONLY else "paper",
            "votes": votes,
            "ts": now,
        }

    save_state(state)
    if all_lines:
        print("\n=== snapshot ===")
        for line in all_lines:
            print(line)

    n_jobs = len(SCAN_JOBS)
    if prepared:
        return None
    if len(skip_reasons) >= n_jobs:
        if skip_reasons and all(r == "stale" for r in skip_reasons):
            return "⏸ 无今日 K 线（节假日或数据未更新），跳过本次扫描"
        markets = sorted({job.get("market", "us") for job in SCAN_JOBS})
        return f"⏸ 非交易时段（{'/'.join(markets)}），跳过本次扫描"
    return "⏸ 无有效数据（拉取失败或质检拦截），跳过本次扫描"


_last_skip_note: Optional[str] = None


def loop(interval: int = 600) -> None:
    global _last_skip_note
    n_strat = sum(len(j["strategies"]) for j in SCAN_JOBS)
    print(
        f"🔄 Paper Live intraday | {INTERVAL} bars | interval={interval}s | "
        f"jobs={len(SCAN_JOBS)} strategies={n_strat}"
    )
    print(f"   Telegram: {'✅' if TG_TOKEN else '❌ set TG_BOT_TOKEN'}")
    print("   Sessions: 美股 Mon–Fri 09:30–16:00 ET（周末/节假日自动跳过）")
    print("   Signals: 已完成 bar · 按现价记账（对位回测 --interval 规则）")
    while True:
        try:
            note = tick()
            if note is not None:
                if note != _last_skip_note:
                    print(note)
                    _last_skip_note = note
            else:
                _last_skip_note = None
        except Exception as e:
            print(f"  ⚠️ scan error: {e}")
        print(f"\n⏳ next in {interval}s …")
        time.sleep(interval)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("tick")
    lp = sp.add_parser("loop")
    lp.add_argument("--interval", type=int, default=600)
    args = p.parse_args()
    if args.cmd == "loop":
        loop(args.interval)
    else:
        note = tick()
        if note:
            print(note)
