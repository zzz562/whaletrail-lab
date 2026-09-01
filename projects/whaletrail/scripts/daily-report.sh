#!/usr/bin/env bash
# WhaleTrail daily report: backtest -> B&H/SPY contrast -> markdown output
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$ROOT/scripts"
PY="$ROOT/.venv/bin/python3"

STRAT="${1:-gold_sma}"
SYM="${2:-GLD}"
START="${3:-2018-01-01}"
END="${4:-$(date +%Y-%m-%d)}"
CASH="${5:-100000}"

# Proxy config: WT_PROXY_URL → HTTPS_PROXY → default. See docs/ENVIRONMENT.md.
PROXY="${WT_PROXY_URL:-${HTTPS_PROXY:-http://127.0.0.1:7890}}"
if curl -s --connect-timeout 2 --max-time 3 -x "$PROXY" https://www.google.com > /dev/null 2>&1; then
    export HTTPS_PROXY="$PROXY"
else
    unset HTTPS_PROXY
fi

echo "🥇 **WhaleTrail 日报**"
echo ""

echo "⏳ 回测中..."
BT_JSON=$("$PY" "$SCRIPTS/run-backtest.py" "$STRAT" "$SYM" "$START" "$END" "$CASH")

FINAL_EQ=$(echo "$BT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['final_equity'])")
RETURN_PCT=$(echo "$BT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['total_return_pct'])")
TRADE_N=$(echo "$BT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['trades'])")
DD=$(echo "$BT_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin).get('max_drawdown_pct'); print('n/a' if d is None else d)")

echo "📊 ${SYM} ${STRAT}  ${START} -> ${END}  (黄金主线)"
echo "💰 策略权益: \$${FINAL_EQ}  |  收益: ${RETURN_PCT}%  |  交易: ${TRADE_N}次  |  回撤: ${DD}%"
echo ""

echo "📌 买入持有对照（同一区间，无交易）："
WT_START="$START" WT_END="$END" WT_ROOT="$ROOT" "$PY" - <<'PY'
import os, sys
from datetime import date

sys.path.insert(0, os.environ["WT_ROOT"])
from whaletrail.data.layer import DataLayer

start = date.fromisoformat(os.environ["WT_START"])
end = date.fromisoformat(os.environ["WT_END"])
src = DataLayer()
for sym in ("GLD", "SPY"):
    df = src.get_daily(sym, start, end)
    if df is None or df.empty:
        print(f"   {sym}: 无数据")
        continue
    closes = df["close"].dropna()
    if closes.empty:
        print(f"   {sym}: 无有效收盘价")
        continue
    ret = float(closes.iloc[-1] / closes.iloc[0] - 1.0) * 100.0
    print(
        f"   {sym} 买入持有: {ret:+.2f}%  "
        f"({closes.index[0].date()} → {closes.index[-1].date()})"
    )
PY

echo ""
echo "📊 回测摘要..."
"$PY" "$SCRIPTS/analyze.py"

echo ""
echo "_自动生成 · WhaleTrail · $(date '+%Y-%m-%d %H:%M')_"
