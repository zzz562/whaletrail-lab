#!/usr/bin/env python3
"""WhaleTrail Dashboard — 回测 / 实时信号 / 情绪 / Watchlist / 运行状态.

Dark terminal-style UI (Bloomberg/Fortress-inspired): deep navy surfaces,
amber gold accent, tabular monospace numerics, semantic green/red.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whaletrail.data.baostock_source import to_baostock_code
from whaletrail.data.history import build_daily_history
from whaletrail.data.watchlist import load_watchlist
from whaletrail.metrics.performance import calculate_metrics, compute_trade_pnl
from whaletrail.similarity import normalize, rank_similar
from whaletrail.storage.repository import Repository

st.set_page_config(page_title="WhaleTrail", layout="wide", page_icon="🐋")

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "whaletrail.db"
DATA_CACHE_DIR = ROOT / "data_cache"
WATCHLIST_PATH = ROOT / "config" / "watchlist.yaml"

SCORE_COLORS = {"bullish": "#4ade80", "bearish": "#f87171", "neutral": "#8b98a9"}
SCORE_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": "➖"}

# ═══════════════════════════════════════════════════════════════════
#  Design system (dark terminal)
# ═══════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
  --wt-bg: #0a0e17;
  --wt-surface: #111826;
  --wt-surface2: #0d1320;
  --wt-border: #1e2a3a;
  --wt-text: #e6edf3;
  --wt-muted: #8b98a9;
  --wt-gold: #e6b450;
  --wt-blue: #38bdf8;
  --wt-up: #4ade80;
  --wt-down: #f87171;
  --wt-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;
}

html, body, .stApp { background: var(--wt-bg); color: var(--wt-text);
  font-family: 'Inter', -apple-system, 'Segoe UI', system-ui, sans-serif; }

.block-container { padding: 1.2rem 2rem 2.5rem; max-width: 1500px; }

#MainMenu, footer { visibility: hidden; height: 0; }
header[data-testid="stHeader"] { background: transparent; }
.stAppDeployButton, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"] { display: none !important; }

/* ── Sidebar ─────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, var(--wt-surface2), #0b101a);
  border-right: 1px solid var(--wt-border);
}
[data-testid="stSidebar"] * { font-family: 'Inter', sans-serif; }

.brand { display: flex; align-items: center; gap: 12px; padding: 6px 0 18px;
  border-bottom: 1px solid var(--wt-border); margin-bottom: 14px; }
.brand-logo { font-size: 1.9rem; line-height: 1; }
.brand-title { font-weight: 800; font-size: 1.15rem; letter-spacing: -.01em; }
.brand-sub { font-size: 10px; letter-spacing: .22em; color: var(--wt-gold);
  font-weight: 700; margin-top: 2px; }

/* vertical nav — hide radio pips, treat options as menu rows */
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] { display: none !important; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] { gap: 4px !important; }
[data-testid="stRadioOption"] {
  background: transparent !important; border: 1px solid transparent !important;
  border-radius: 8px !important; padding: 7px 10px !important;
  margin: 0 !important; }
[data-testid="stRadioOption"] p { color: var(--wt-muted) !important; font-size: .92rem !important; margin: 0 !important; }
[data-testid="stRadioOption"]:hover { background: var(--wt-surface) !important; }
[data-testid="stRadioOption"]:hover p { color: var(--wt-text) !important; }
[data-testid="stRadioOption"][data-selected="true"] {
  background: var(--wt-surface) !important; border-color: var(--wt-border) !important; }
[data-testid="stRadioOption"][data-selected="true"] p { color: var(--wt-gold) !important; font-weight: 600 !important; }
[data-testid="stRadioOption"] > div > div > div:first-child { display: none !important; }

/* ── Page header ─────────────────────────────────────────────── */
.kicker { font-size: 11px; letter-spacing: .2em; text-transform: uppercase;
  color: var(--wt-gold); font-weight: 700; margin-bottom: 4px; }
.page-title { font-size: 1.65rem; font-weight: 800; letter-spacing: -.02em;
  margin: 0 0 2px; }
.page-sub { color: var(--wt-muted); font-size: .85rem; margin-bottom: 16px; }

/* ── Metric cards ────────────────────────────────────────────── */
.m-card {
  background: linear-gradient(180deg, var(--wt-surface), #0f1622);
  border: 1px solid var(--wt-border); border-radius: 12px;
  padding: 13px 16px 12px; position: relative; overflow: hidden;
  height: 100%; box-sizing: border-box; }
.m-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0;
  height: 2px; background: var(--m-accent, var(--wt-gold)); opacity: .9; }
.m-label { font-size: 10.5px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--wt-muted); font-weight: 600; }
.m-value { font-family: var(--wt-mono); font-size: 1.75rem; font-weight: 700;
  margin-top: 7px; font-variant-numeric: tabular-nums; color: var(--wt-text);
  letter-spacing: -.02em; line-height: 1.1; }
.m-delta { font-size: .82rem; margin-top: 5px; font-variant-numeric: tabular-nums;
  font-weight: 500; }
.m-sub { font-size: .76rem; color: var(--wt-muted); margin-top: 2px;
  font-variant-numeric: tabular-nums; }

/* ── Pills ───────────────────────────────────────────────────── */
.pill { display: inline-block; padding: 3px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: .03em; font-variant-numeric: tabular-nums; }
.pill-ok    { background: rgba(74,222,128,.1);  color: var(--wt-up);   border: 1px solid rgba(74,222,128,.35); }
.pill-err   { background: rgba(248,113,113,.1); color: var(--wt-down); border: 1px solid rgba(248,113,113,.35); }
.pill-warn  { background: rgba(251,191,36,.1);  color: #fbbf24;       border: 1px solid rgba(251,191,36,.35); }
.pill-mut   { background: rgba(139,152,169,.08); color: var(--wt-muted); border: 1px solid rgba(139,152,169,.3); }
.pill-buy   { background: rgba(74,222,128,.1);  color: var(--wt-up);   border: 1px solid rgba(74,222,128,.35); }
.pill-sell  { background: rgba(248,113,113,.1); color: var(--wt-down); border: 1px solid rgba(248,113,113,.35); }

/* ── Service rows ────────────────────────────────────────────── */
.svc { display: flex; justify-content: space-between; align-items: center;
  padding: 9px 14px; border: 1px solid var(--wt-border); border-radius: 10px;
  background: var(--wt-surface2); margin-bottom: 8px; }
.svc-name { font-weight: 600; font-size: .9rem; }
.svc-port { font-family: var(--wt-mono); color: var(--wt-muted); font-size: .78rem; }

/* ── Ticker tape ─────────────────────────────────────────────── */
.tape { overflow: hidden; border: 1px solid var(--wt-border); border-radius: 10px;
  background: var(--wt-surface2); margin: 4px 0 18px; }
.tape-inner { display: inline-flex; gap: 30px; padding: 9px 16px;
  white-space: nowrap; animation: wt-tape 60s linear infinite; }
.tape-inner:hover { animation-play-state: paused; }
@keyframes wt-tape { from { transform: translateX(0); }
  to { transform: translateX(-50%); } }
.tape-item { font-family: var(--wt-mono); font-size: 12px; color: var(--wt-muted);
  font-variant-numeric: tabular-nums; }
.tape-item b { color: var(--wt-text); font-weight: 600; }
.tape-up { color: var(--wt-up); }
.tape-down { color: var(--wt-down); }

/* ── Sentiment cards ─────────────────────────────────────────── */
.sentiment-card {
  border-left: 4px solid var(--wt-blue); padding: .75rem 1rem; margin: .4rem 0;
  border-radius: 10px; background: var(--wt-surface);
  border-top: 1px solid var(--wt-border); border-right: 1px solid var(--wt-border);
  border-bottom: 1px solid var(--wt-border); }
.tweet-body { font-size: .92rem; line-height: 1.55; color: var(--wt-text); margin-top: 4px; }
.meta-row { color: var(--wt-muted); font-size: .8rem; font-variant-numeric: tabular-nums; }

/* ── Tables (native + pandas Styler HTML) ────────────────────── */
[data-testid="stDataFrame"] { border: 1px solid var(--wt-border); border-radius: 10px; overflow: hidden; }
table.dataframe { border-collapse: collapse; width: 100%; font-size: 12.5px;
  background: var(--wt-surface); }
table.dataframe th {
  background: var(--wt-surface2); color: var(--wt-muted); text-transform: uppercase;
  font-size: 10.5px; letter-spacing: .1em; padding: 8px 12px; text-align: right;
  border-bottom: 1px solid var(--wt-border); font-weight: 700; }
table.dataframe th:first-child { text-align: left; }
table.dataframe td { padding: 6px 12px; border-bottom: 1px solid #182231;
  font-variant-numeric: tabular-nums; color: var(--wt-text); }
table.dataframe tbody tr:hover { background: rgba(56,189,248,.04); }
table.dataframe tbody tr:last-child td { border-bottom: none; }

[data-testid="stTable"] { border: 1px solid var(--wt-border); border-radius: 10px; overflow: hidden; }

/* ── Widgets ─────────────────────────────────────────────────── */
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stSelectbox"] > div > div {
  background: var(--wt-surface) !important;
  border: 1px solid var(--wt-border) !important;
  border-radius: 8px !important; color: var(--wt-text) !important; }
.stButton > button { background: var(--wt-surface); border: 1px solid var(--wt-border);
  color: var(--wt-text); border-radius: 8px; font-weight: 600; transition: all .15s; }
.stButton > button:hover { border-color: var(--wt-gold); color: var(--wt-gold); }

[data-testid="stCaptionContainer"] p { color: var(--wt-muted); font-size: .78rem; }
[data-testid="stAlert"] { background: var(--wt-surface2); border: 1px solid var(--wt-border);
  border-radius: 10px; color: var(--wt-muted); }
[data-testid="stSubheader"] { font-size: 1.05rem; font-weight: 700; letter-spacing: -.01em;
  margin-top: 22px; }
h2[data-testid="stSubheader"] { color: var(--wt-text); }

/* section labels */
.sec-label { font-size: 11px; letter-spacing: .18em; text-transform: uppercase;
  color: var(--wt-gold); font-weight: 700; margin: 20px 0 8px; }

/* scrollbars */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: #243244; border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }

/* charts blend into the dark surface */
[data-testid="stVegaLiteChart"], [data-testid="stLineChart"], [data-testid="stAreaChart"], [data-testid="stBarChart"] {
  border: 1px solid var(--wt-border); border-radius: 10px; padding: 6px; background: var(--wt-surface); }
</style>
""", unsafe_allow_html=True)

# Fragment auto-refresh with graceful fallback for older streamlit versions.
# Set WHALETRAIL_NO_AUTOREFRESH=1 to disable the run_every timer (e.g. for
# AppTest runs, which do not simulate fragment timers reliably).
_fragment = getattr(st, "fragment", None)
_autorun_every = None if __import__("os").environ.get("WHALETRAIL_NO_AUTOREFRESH") else 60


def _frag(run_every: Optional[int] = None):
    if _fragment is None:
        return lambda f: f
    return _fragment(run_every=_autorun_every)


# ═══════════════════════════════════════════════════════════════════
#  Cached data loaders
# ═══════════════════════════════════════════════════════════════════
@st.cache_data(ttl=60, show_spinner=False)
def list_backtest_files() -> list[str]:
    files = sorted(
        RESULTS_DIR.glob("backtest_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [p.name for p in files]


@st.cache_data(ttl=60, show_spinner=False)
def load_backtest(name: str) -> dict:
    with open(RESULTS_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=60, show_spinner=False)
def load_sentiment_latest() -> Optional[dict]:
    f = RESULTS_DIR / "sentiment_latest.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@st.cache_data(ttl=60, show_spinner=False)
def load_sentiment_history() -> list[dict]:
    out = []
    for f in sorted(RESULTS_DIR.glob("sentiment_*.json")):
        if f.name in ("sentiment_latest.json", "sentiment_state.json"):
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


@st.cache_data(ttl=60, show_spinner=False)
def load_live_state() -> Optional[dict]:
    f = RESULTS_DIR / "paper_live_state.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@st.cache_data(ttl=60, show_spinner=False)
def load_quote_snapshots() -> list[dict]:
    try:
        repo = Repository(DB_PATH)
        rows = repo.latest_quote_snapshots()
        repo.close()
        return rows
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def latest_quote_ts() -> Optional[str]:
    try:
        repo = Repository(DB_PATH)
        ts = repo.latest_quote_timestamp()
        repo.close()
        return ts
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def load_cached_close(symbol: str) -> Optional[pd.DataFrame]:
    """Read a symbol's cached daily closes from data_cache (offline)."""
    safe = symbol.replace("/", "_").replace("\\", "_").replace(":", "_")
    path = DATA_CACHE_DIR / f"{safe}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty:
        return None
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ═══════════════════════════════════════════════════════════════════
#  Shared helpers
# ═══════════════════════════════════════════════════════════════════
def _staleness(ts_str: str) -> str:
    if not ts_str:
        return "?"
    try:
        ts = pd.to_datetime(ts_str)
        if pd.isna(ts):
            return "?"
        age = datetime.now() - ts.to_pydatetime()
        secs = int(age.total_seconds())
        if secs < 0:
            return "时间异常"
        if secs < 90:
            return f"{secs}s 前"
        if secs < 3600:
            return f"{secs // 60} 分钟前"
        if secs < 86400:
            return f"{secs // 3600} 小时前"
        return f"{secs // 86400} 天前"
    except Exception:
        return "?"


def _backtest_metrics(data: dict) -> tuple[list[dict], dict, float]:
    """Return (enriched_trades, metrics, initial_cash), computing on the fly
    when the JSON was written before metrics were persisted."""
    trades_raw = data.get("trades", [])
    if trades_raw and "pnl" in (trades_raw[0] or {}):
        enriched = trades_raw
    else:
        enriched = compute_trade_pnl(trades_raw)
    metrics = data.get("metrics")
    initial_cash = 100_000.0
    if not metrics:
        fe = float(data.get("final_equity", 0) or 0)
        tr_frac = float(data.get("total_return", 0) or 0)
        if 1 + tr_frac > 0:
            initial_cash = fe / (1 + tr_frac)
        equity = [p["equity"] for p in data.get("equity_curve", [])]
        metrics = calculate_metrics(enriched, equity, initial_cash)
    return enriched, metrics, initial_cash


def _benchmark_series(data: dict, initial_cash: float) -> Optional[pd.Series]:
    """Buy-and-hold series for the backtest symbol, from local cache only."""
    symbol, start, end = data.get("symbol"), data.get("start"), data.get("end")
    if not symbol or not start or not end:
        return None
    df = load_cached_close(symbol)
    if df is None:
        return None
    try:
        b = df.loc[pd.Timestamp(start): pd.Timestamp(end)]
        if b.empty or len(b) < 2:
            return None
        first = float(b["close"].iloc[0])
        if first <= 0:
            return None
        s = (b["close"] / first * initial_cash).rename("买入持有")
        s.index = pd.to_datetime(s.index)
        return s
    except Exception:
        return None


def _parse_signals(mapping: dict) -> list[dict]:
    rows = []
    for key, ts in (mapping or {}).items():
        parts = key.split("|")
        if len(parts) == 3:
            rows.append({"symbol": parts[0], "strategy": parts[1], "side": parts[2], "date": str(ts)[:10]})
    return rows


def _signal_tags(r: dict) -> str:
    tags = []
    rsi = r.get("rsi")
    if rsi is not None:
        if rsi >= 70:
            tags.append("RSI 超买")
        elif rsi <= 30:
            tags.append("RSI 超卖")
    close, s200 = r.get("close"), r.get("sma200")
    if close and s200:
        tags.append("多头" if close > s200 else "空头")
    return " · ".join(tags) if tags else "—"


def _service_checks() -> list[dict]:
    checks = {
        "OpenClaw Gateway": ("http://127.0.0.1:18789/health", 18789),
        "Dashboard": ("http://127.0.0.1:8766", 8766),
        "Ollama": ("http://127.0.0.1:11434/api/tags", 11434),
    }
    rows = []
    for name, (url, port) in checks.items():
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                rows.append({"服务": name, "端口": str(port), "状态": "✅" if r.status < 400 else "⚠️"})
        except Exception:
            rows.append({"服务": name, "端口": str(port), "状态": "❌"})
    return rows


def _launchd_rows() -> list[dict]:
    rows = []
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True, timeout=5)
    except Exception:
        return rows
    for label, display in [
        ("ai.whaletrail-live", "Paper Live"),
        ("ai.whaletrail-dashboard", "Dashboard"),
        ("homebrew.mxcl.ollama", "Ollama"),
        ("ai.openclaw.gateway", "OpenClaw Gateway"),
        ("com.zeph.reverse-tunnel", "Reverse tunnel"),
    ]:
        rows.append({"服务": f"launchd: {display}", "端口": "-", "状态": "✅" if label in out else "❌"})
    return rows


def _runs_count() -> int:
    try:
        repo = Repository(DB_PATH)
        n = len(repo.list_runs(limit=1000))
        repo.close()
        return n
    except Exception:
        return -1


# ═══════════════════════════════════════════════════════════════════
#  Presentation helpers
# ═══════════════════════════════════════════════════════════════════
def _page_header(emoji: str, title: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="kicker">WHALETRAIL · {emoji}</div>'
        f'<div class="page-title">{title}</div>'
        f'<div class="page-sub">{sub}</div>',
        unsafe_allow_html=True,
    )


def _card(label: str, value: str, delta: str = "", delta_color: str = "", sub: str = "", accent: str = "#e6b450") -> str:
    delta_html = f'<div class="m-delta" style="color:{delta_color}">{delta}</div>' if delta else ""
    sub_html = f'<div class="m-sub">{sub}</div>' if sub else ""
    return (f'<div class="m-card" style="--m-accent:{accent}">'
            f'<div class="m-label">{label}</div>'
            f'<div class="m-value">{value}</div>{delta_html}{sub_html}</div>')


def _card_row(cards: list[dict], cols: int = 4) -> None:
    """cards: list of dicts with keys label/value/delta/delta_color/sub/accent."""
    columns = st.columns(cols)
    for i, col in enumerate(columns):
        if i < len(cards):
            col.markdown(_card(**cards[i]), unsafe_allow_html=True)


def _num_color(v: float) -> str:
    return "#4ade80" if v > 0 else ("#f87171" if v < 0 else "#8b98a9")


def _staleness_color(text: str) -> str:
    if "天" in text:
        return "#f87171"
    if "小时" in text:
        return "#fbbf24"
    return "#4ade80"


def _pill(text: str, tone: str = "mut") -> str:
    return f'<span class="pill pill-{tone}">{text}</span>'


def _show(obj, **kwargs) -> None:
    """st.dataframe wrapper: hide index, stretch width, keep Styler colors."""
    kwargs.setdefault("width", "stretch")
    kwargs.setdefault("hide_index", True)
    st.dataframe(obj, **kwargs)


def _alt_dark(chart: alt.Chart) -> alt.Chart:
    return (chart
            .configure(background="#111826")
            .configure_view(strokeOpacity=0)
            .configure_axis(
                gridColor="#1e2a3a", domainColor="#243244",
                labelColor="#8b98a9", titleColor="#8b98a9", tickColor="#243244",
            )
            .configure_legend(labelColor="#8b98a9", titleColor="#8b98a9"))


def _style_base(s: Any) -> Any:
    """Common pandas Styler base for dark theme (works on pandas >= 2.1)."""
    return (s
            .set_properties(**{
                "color": "#e6edf3", "font-family": "var(--wt-mono)",
                "font-size": "12.5px", "padding": "6px 12px",
                "border-bottom": "1px solid #182231", "text-align": "right",
            })
            .set_table_styles([{
                "selector": "th", "props": [
                    ("background-color", "#0d1320"), ("color", "#8b98a9"),
                    ("font-size", "10.5px"), ("text-transform", "uppercase"),
                    ("letter-spacing", ".1em"), ("padding", "8px 12px"),
                    ("border-bottom", "1px solid #1e2a3a")],
            }]))


def _num_style(v, fmt: str = "") -> str:
    try:
        if pd.isna(v):
            return ""
        return f"color:{_num_color(float(v))};font-weight:600"
    except (TypeError, ValueError):
        return ""


def _rsi_style(v) -> str:
    if v is None or pd.isna(v):
        return ""
    if v >= 70:
        return "color:#fbbf24;font-weight:700;background:rgba(251,191,36,.08)"
    if v <= 30:
        return "color:#38bdf8;font-weight:700;background:rgba(56,189,248,.08)"
    return ""


def _side_style(v) -> str:
    s = str(v).upper()
    if s == "BUY":
        return "color:#4ade80;font-weight:700"
    if s == "SELL":
        return "color:#f87171;font-weight:700"
    return ""


def _ticker_tape(snaps: list[dict]) -> None:
    if not snaps:
        return
    items = []
    for r in snaps:
        name = r.get("local_name") or r.get("tv_symbol") or ""
        close = r.get("close")
        chg = r.get("change_percent")
        if close is None:
            continue
        cls = "tape-up" if (chg or 0) >= 0 else "tape-down"
        arrow = "▲" if (chg or 0) >= 0 else "▼"
        items.append(
            f'<span class="tape-item"><b>{name}</b> {close:,.2f} '
            f'<span class="{cls}">{arrow}{chg:+.2f}%</span></span>'
        )
    if not items:
        return
    inner = "".join(items)
    st.markdown(
        f'<div class="tape"><div class="tape-inner">{inner}{inner}</div></div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════
#  Page: 总览
# ═══════════════════════════════════════════════════════════════════
@_frag(run_every=60)
def _overview_panel() -> None:
    files = list_backtest_files()
    latest = load_backtest(files[0]) if files else None
    sent = load_sentiment_latest()
    live = load_live_state()
    snaps = load_quote_snapshots()

    _ticker_tape(snaps)

    cards = []
    if latest:
        enriched, metrics, _ = _backtest_metrics(latest)
        ret = metrics.get("total_return", 0.0)
        dd = metrics.get("max_drawdown", 0.0)
        cards.append({"label": "最新回测收益", "value": f"{ret:+.2f}%",
                      "delta": f"最大回撤 {dd:.2f}%", "delta_color": _num_color(ret),
                      "sub": f"{latest.get('strategy','?')} · {latest.get('symbol','?')}",
                      "accent": "#e6b450"})
        cards.append({"label": "回测交易", "value": f"{len(enriched)} 次",
                      "delta": f"结束 {latest.get('end','?')}",
                      "delta_color": "#8b98a9",
                      "sub": f"Sharpe {metrics.get('sharpe_ratio',0):.2f} · 胜率 {metrics.get('win_rate',0)*100:.0f}%",
                      "accent": "#38bdf8"})
    else:
        cards.append({"label": "最新回测收益", "value": "—",
                      "delta": "运行 scripts/run-backtest.py", "delta_color": "#8b98a9",
                      "sub": "", "accent": "#e6b450"})

    if sent:
        gsi = sent.get("gold_sentiment_index", 0.0)
        bull, bear = sent.get("bullish_count", 0), sent.get("bearish_count", 0)
        gsi_tone = "看多" if gsi > 0.15 else ("看空" if gsi < -0.15 else "中性")
        cards.append({"label": "GSI 情绪指数", "value": f"{gsi:+.3f}",
                      "delta": f"{gsi_tone} · 📈{bull} 📉{bear}",
                      "delta_color": _num_color(gsi),
                      "sub": f"{sent.get('date','?')} 扫描 {sent.get('total_scored',0)} 条",
                      "accent": "#a78bfa"})
    else:
        cards.append({"label": "GSI 情绪指数", "value": "—",
                      "delta": "等待情绪扫描", "delta_color": "#8b98a9",
                      "sub": "", "accent": "#a78bfa"})

    if live:
        snap = live.get("last_snapshot") or {}
        gld = snap.get("GLD", {})
        spy = snap.get("SPY", {})
        price = gld.get("price")
        stal = _staleness(gld.get("ts", "")) if gld else ""
        # GLD share ≈ 0.0905 oz; show the spot-gold equivalent to avoid
        # ETF-share-price vs spot-price confusion.
        sub_parts = []
        if price:
            sub_parts.append(f"≈ 现货 ${price * 11.05:,.0f}/oz")
        if spy.get("price"):
            sub_parts.append(f"SPY ${spy['price']:,.2f}")
        cards.append({"label": "GLD 实时价格", "value": f"${price:,.2f}" if price else "—",
                      "delta": stal, "delta_color": _staleness_color(stal) if stal else "#8b98a9",
                      "sub": " · ".join(sub_parts),
                      "accent": "#e6b450"})
    else:
        cards.append({"label": "GLD 实时价格", "value": "—",
                      "delta": "等待 paper-live", "delta_color": "#8b98a9",
                      "sub": "", "accent": "#e6b450"})

    _card_row(cards[:4])

    signals = _parse_signals((live or {}).get("last_signals") or {})
    today = date.today().isoformat()
    today_n = sum(1 for s in signals if s["date"] == today)
    rsi_alerts = [s for s in snaps if (s.get("rsi") is not None and (s["rsi"] >= 70 or s["rsi"] <= 30))]
    services = _service_checks()
    up_n = sum(1 for r in services if r["状态"] == "✅")
    runs = _runs_count()

    _card_row([
        {"label": "今日策略信号", "value": today_n, "delta": f"累计 {len(signals)} 条",
         "delta_color": "#4ade80" if today_n else "#8b98a9", "sub": "", "accent": "#38bdf8"},
        {"label": "Watchlist 覆盖", "value": len(snaps), "delta": f"RSI 极端 {len(rsi_alerts)} 只",
         "delta_color": "#fbbf24" if rsi_alerts else "#8b98a9", "sub": "tvscreener 快照", "accent": "#e6b450"},
        {"label": "服务健康", "value": f"{up_n}/{len(services)}", "delta": "OpenClaw · Ollama · Dashboard",
         "delta_color": "#4ade80" if up_n == len(services) else "#fbbf24", "sub": "", "accent": "#4ade80"},
        {"label": "SQLite 回测记录", "value": runs if runs >= 0 else "?",
         "delta": "runs 表持久化", "delta_color": "#8b98a9", "sub": "", "accent": "#38bdf8"},
    ])

    if signals:
        st.markdown('<div class="sec-label">最近信号</div>', unsafe_allow_html=True)
        rows_html = []
        for s in sorted(signals, key=lambda x: x["date"], reverse=True)[:8]:
            tone = "buy" if str(s["side"]).upper() == "BUY" else "sell"
            rows_html.append(
                f'<div class="svc"><span class="svc-name">{s["date"]} · {s["symbol"]} · {s["strategy"]}</span>'
                f'{_pill(s["side"], tone)}</div>'
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)


def page_overview() -> None:
    _page_header("📊", "总览", "黄金主策略 · 美股对冲 · A股跟庄 · 情绪 — 单屏总览")
    _overview_panel()
    st.caption("总览每 60s 自动刷新 · 数据源: results/*.json + results/whaletrail.db")


# ═══════════════════════════════════════════════════════════════════
#  Page: 回测结果
# ═══════════════════════════════════════════════════════════════════
def page_backtest() -> None:
    _page_header("📈", "回测结果", "策略表现 · 权益曲线 · 交易明细 · 跨回测对比")
    files = list_backtest_files()
    if not files:
        st.info("还没有回测结果。运行 scripts/run-backtest.py 生成。")
        return
    selected = st.selectbox("选择回测", files)
    data = load_backtest(selected)
    enriched, metrics, initial_cash = _backtest_metrics(data)

    fe = float(data.get("final_equity", 0) or 0)
    ret = metrics.get("total_return", 0.0)
    dd = metrics.get("max_drawdown", 0.0)
    trades_n = len(enriched)

    _card_row([
        {"label": "最终权益", "value": f"${fe:,.0f}",
         "delta": f"初始 ${initial_cash:,.0f}", "delta_color": "#8b98a9",
         "sub": f"手续费 ${data.get('total_commission', 0):.2f}", "accent": "#e6b450"},
        {"label": "总收益率", "value": f"{ret:+.2f}%", "delta": "策略累计",
         "delta_color": _num_color(ret), "sub": "", "accent": _num_color(ret)},
        {"label": "年化收益率", "value": f"{metrics.get('annual_return', 0):+.2f}%",
         "delta": "252 交易日基准", "delta_color": _num_color(metrics.get("annual_return", 0)),
         "sub": "", "accent": "#38bdf8"},
        {"label": "最大回撤", "value": f"{dd:.2f}%", "delta": "峰值回撤",
         "delta_color": "#f87171", "sub": "", "accent": "#f87171"},
    ])
    _card_row([
        {"label": "Sharpe", "value": f"{metrics.get('sharpe_ratio', 0):.2f}",
         "delta": "年化 · RF 2%", "delta_color": "#8b98a9", "sub": "", "accent": "#a78bfa"},
        {"label": "胜率", "value": f"{metrics.get('win_rate', 0) * 100:.1f}%",
         "delta": "平仓交易口径", "delta_color": "#8b98a9", "sub": "", "accent": "#a78bfa"},
        {"label": "盈亏比", "value": f"{metrics.get('profit_factor', 0):.2f}",
         "delta": "毛利 / 毛损", "delta_color": "#8b98a9", "sub": "", "accent": "#a78bfa"},
        {"label": "波动率", "value": f"{metrics.get('volatility', 0):.2f}%",
         "delta": "年化日收益 σ", "delta_color": "#8b98a9", "sub": "", "accent": "#a78bfa"},
    ])
    st.caption(f"策略: **{data.get('strategy', '?')}** · 标的: {data.get('symbol', '?')} · "
               f"{data.get('start', '?')} → {data.get('end', '?')} · 交易 {trades_n} 次")

    equity = data.get("equity_curve", [])
    if equity:
        df_eq = pd.DataFrame(equity)
        df_eq["date"] = pd.to_datetime(df_eq["date"])
        df_eq = df_eq.set_index("date")
        bench = _benchmark_series(data, initial_cash)
        if bench is not None:
            df_eq = df_eq.join(bench, how="left")
        st.markdown('<div class="sec-label">权益曲线</div>', unsafe_allow_html=True)
        eq = df_eq.reset_index()
        value_cols = [c for c in ("equity", "买入持有") if c in eq.columns]
        long = eq.melt(id_vars=["date"], value_vars=value_cols, var_name="系列", value_name="权益")
        eq_ch = alt.Chart(long.dropna(subset=["权益"])).mark_line(strokeWidth=2).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("权益:Q", title=None),
            color=alt.Color("系列:N",
                            scale=alt.Scale(domain=["equity", "买入持有"], range=["#e6b450", "#38bdf8"]),
                            legend=alt.Legend(title=None, orient="top")),
        ).properties(height=320, width=980)
        st.altair_chart(_alt_dark(eq_ch), width="stretch")
        if bench is not None:
            st.caption("蓝色线为买入持有对照（数据来自本地 data_cache 缓存）")

        st.markdown('<div class="sec-label">回撤</div>', unsafe_allow_html=True)
        dd_df = df_eq.reset_index()
        dd_df["回撤%"] = (df_eq["equity"] / df_eq["equity"].cummax() - 1).to_numpy() * 100
        dd_ch = alt.Chart(dd_df).mark_area(color="#f87171", opacity=0.45).encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("回撤%:Q", title=None),
        ).properties(height=220, width=980)
        st.altair_chart(_alt_dark(dd_ch), width="stretch")

    if enriched:
        st.markdown('<div class="sec-label">交易记录</div>', unsafe_allow_html=True)
        df_t = pd.DataFrame(enriched)
        if "date" in df_t.columns:
            df_t["date"] = pd.to_datetime(df_t["date"]).dt.date
        cols = [c for c in ("date", "symbol", "side", "quantity", "price", "commission", "pnl") if c in df_t.columns]
        styled = (_style_base(df_t[cols].style.hide(axis="index"))
                  .format({"quantity": "{:,.2f}", "price": "${:,.2f}",
                           "commission": "${:,.2f}", "pnl": "${:,.2f}"}, na_rep="—")
                  .map(_side_style, subset=["side"])
                  .map(lambda v: _num_style(v, ""), subset=["pnl"]))
        _show(styled, width="stretch")
        sells = [t for t in enriched if str(t.get("side", "")).lower() == "sell"]
        wins = sum(1 for t in sells if (t.get("pnl") or 0) > 0)
        losses = sum(1 for t in sells if (t.get("pnl") or 0) < 0)
        st.caption(f"平仓 {len(sells)} 笔 · 盈利 {wins} · 亏损 {losses}")

    # ── Cross-run comparison ─────────────────────────────────────────
    if len(files) > 1:
        st.markdown('<div class="sec-label">跨回测对比</div>', unsafe_allow_html=True)
        rows = []
        for name in files:
            d = load_backtest(name)
            m = d.get("metrics")
            rows.append({
                "文件": name,
                "策略": d.get("strategy", "?"),
                "标的": d.get("symbol", "?"),
                "结束日": d.get("end", "?"),
                "总收益%": m.get("total_return") if m else round((d.get("total_return", 0) or 0) * 100, 2),
                "最终权益": round(float(d.get("final_equity", 0) or 0), 2),
                "交易次数": len(d.get("trades", [])),
                "最大回撤%": m.get("max_drawdown") if m else None,
                "Sharpe": m.get("sharpe_ratio") if m else None,
            })
        df_cmp = pd.DataFrame(rows)
        styled_cmp = (_style_base(df_cmp.style.hide(axis="index"))
                      .format({"最终权益": "${:,.0f}", "最大回撤%": "{:.2f}%",
                               "Sharpe": "{:.2f}", "交易次数": "{:.0f}"}, na_rep="—")
                      .map(lambda v: _num_style(v, ""), subset=["总收益%"])
                      .map(lambda v: _num_style(v, ""), subset=["最大回撤%"]))
        _show(styled_cmp, width="stretch")
        cmp_chart = df_cmp.head(10).copy()
        cmp_chart["label"] = cmp_chart["策略"] + " · " + cmp_chart["标的"] + " · " + cmp_chart["结束日"].astype(str)
        st.bar_chart(cmp_chart.set_index("label")["总收益%"], height=240)


# ═══════════════════════════════════════════════════════════════════
#  Page: 实时信号
# ═══════════════════════════════════════════════════════════════════
@_frag(run_every=60)
def _live_panel() -> None:
    live = load_live_state()
    if not live:
        st.info("还没有实时扫描数据。运行 scripts/paper-live.py tick（或 loop --interval 1800）")
        return

    snap = live.get("last_snapshot") or {}
    if snap:
        cards = []
        for sym, info in snap.items():
            price = info.get("price")
            stal = _staleness(info.get("ts", ""))
            accent = "#e6b450" if sym == "GLD" else "#38bdf8"
            sub = info.get("ts", "")
            if sym == "GLD" and price:
                sub = f"≈ 现货 ${price * 11.05:,.0f}/oz · {sub}"
            if info.get("mode") == "observation":
                sub = f"🔎 观察 · {sub}"
            cards.append({"label": f"{sym} 实时价格",
                          "value": f"${price:,.2f}" if price else "—",
                          "delta": stal, "delta_color": _staleness_color(stal) if stal else "#8b98a9",
                          "sub": sub, "accent": accent})
        _card_row(cards, cols=max(1, len(cards)))

    signals = _parse_signals(live.get("last_signals") or {})
    if signals:
        df_s = pd.DataFrame(signals)
        today = date.today().isoformat()
        today_n = int((df_s["date"] == today).sum())
        df_s["标记"] = df_s["date"].map(lambda d: "今日" if d == today else "—")
        df_s = df_s.rename(columns={"symbol": "标的", "strategy": "策略", "side": "方向", "date": "日期"})
        st.markdown(
            f'<div class="sec-label">策略信号 · 共 {len(df_s)} 条 · 今日 {today_n} 条</div>',
            unsafe_allow_html=True)
        styled_s = (_style_base(df_s[["标的", "策略", "方向", "日期", "标记"]].style.hide(axis="index"))
                    .map(_side_style, subset=["方向"])
                    .map(lambda v: "color:#e6b450;font-weight:700" if v == "今日" else "", subset=["标记"]))
        _show(styled_s)
    else:
        st.info("暂无信号记录")

    positions = live.get("positions") or {}
    if positions:
        st.markdown('<div class="sec-label">当前持仓</div>', unsafe_allow_html=True)
        if isinstance(positions, dict):
            _show(_style_base(pd.DataFrame(positions).T.style.hide(axis="index")), width="stretch")
        else:
            _show(_style_base(pd.DataFrame(positions).style.hide(axis="index")), width="stretch")
    else:
        st.caption("当前无持仓")


def page_live() -> None:
    _page_header("🔴", "实时信号", "paper-live 多策略扫描 · 每 60s 自动刷新")
    _live_panel()
    st.caption("状态文件: results/paper_live_state.json")


# ═══════════════════════════════════════════════════════════════════
#  Page: 情绪监控
# ═══════════════════════════════════════════════════════════════════
def page_sentiment() -> None:
    _page_header("🐋", "Gold Sentiment Index", "18 位黄金 KOL 推文打分 · Ollama 标注")
    latest = load_sentiment_latest()
    if not latest:
        st.info("等待首次情绪扫描（cron: whaletrail-sentiment 每日 09:00）")
        return

    gsi = latest.get("gold_sentiment_index", 0.0)
    bullish = latest.get("bullish_count", 0)
    bearish = latest.get("bearish_count", 0)
    neutral = latest.get("neutral_count", 0)
    total = latest.get("total_scored", 0)
    entries = latest.get("entries", [])

    gsi_tone = "🟢 看多" if gsi > 0.15 else ("🔴 看空" if gsi < -0.15 else "🟡 中性")
    _card_row([
        {"label": "GSI 情绪指数", "value": f"{gsi:+.3f}", "delta": gsi_tone,
         "delta_color": _num_color(gsi),
         "sub": f"数据日期 {latest.get('date', '?')}", "accent": "#a78bfa"},
        {"label": "📈 看多", "value": bullish, "delta": "bullish",
         "delta_color": "#4ade80", "sub": "", "accent": "#4ade80"},
        {"label": "📉 看空", "value": bearish, "delta": "bearish",
         "delta_color": "#f87171", "sub": "", "accent": "#f87171"},
        {"label": "📊 扫描", "value": f"{total} 条", "delta": "今日推文",
         "delta_color": "#8b98a9",
         "sub": f"覆盖 KOL: {latest.get('scanned_kols') or len({e.get('account') for e in entries})} 位",
         "accent": "#38bdf8"},
    ])

    history = load_sentiment_history()
    if history:
        st.markdown('<div class="sec-label">GSI 历史趋势</div>', unsafe_allow_html=True)
        df_h = pd.DataFrame(history).sort_values("date")
        df_h["date"] = pd.to_datetime(df_h["date"])
        df_h = df_h.set_index("date")

        source = df_h.reset_index()
        source["GSI"] = source["gold_sentiment_index"]
        source["Bullish"] = source["bullish_count"]
        source["Bearish"] = source["bearish_count"]

        bar = alt.Chart(source).mark_bar(opacity=0.25).encode(
            x="date:T", y="Bullish:Q", color=alt.value("#4ade80"),
        ).properties(height=250)
        bar2 = alt.Chart(source).mark_bar(opacity=0.25).encode(
            x="date:T", y=alt.Y("Bearish:Q", scale=alt.Scale(domain=[0, source["Bearish"].max() + 1])),
            color=alt.value("#f87171"),
        )
        line = alt.Chart(source).mark_line(point=True, color="#e6b450", strokeWidth=3).encode(
            x="date:T",
            y=alt.Y("GSI:Q", scale=alt.Scale(domain=[-1, 1])),
            tooltip=["date", "GSI", "Bullish", "Bearish"],
        ).properties(height=250)

        chart = _alt_dark((bar + bar2 + line).resolve_scale(y="independent").properties(height=260, width=980))
        st.altair_chart(chart, width="stretch")

        st.caption("每日汇总")
        tbl = source[["date", "GSI", "Bullish", "Bearish", "neutral_count"]].rename(
            columns={"neutral_count": "Neutral"}
        ).tail(10)
        _show(_style_base(tbl.set_index("date").style)
                     .format({"GSI": "{:+.3f}", "Bullish": "{:.0f}", "Bearish": "{:.0f}", "Neutral": "{:.0f}"})
                     .map(lambda v: _num_style(v, ""), subset=["GSI"]), width="stretch")

    if entries:
        col_left, col_right = st.columns([1, 2])
        with col_left:
            st.markdown('<div class="sec-label">情绪分布</div>', unsafe_allow_html=True)
            df_dist = pd.DataFrame({"方向": ["📈 看多", "📉 看空", "➖ 中性"], "数量": [bullish, bearish, neutral]})
            st.bar_chart(df_dist.set_index("方向"), width="stretch")

            st.markdown('<div class="sec-label">KOL 分布</div>', unsafe_allow_html=True)
            kol_rows = []
            for acc, grp in pd.DataFrame(entries).groupby("account"):
                kol_rows.append({
                    "KOL": acc,
                    "看多": int((grp["score"] == "bullish").sum()),
                    "看空": int((grp["score"] == "bearish").sum()),
                    "中性": int((grp["score"] == "neutral").sum()),
                    "均置信": round(float(grp["confidence"].mean()), 1),
                })
            df_kol = pd.DataFrame(kol_rows)
            _show(_style_base(df_kol.style.hide(axis="index"))
                         .format({"看多": "{:.0f}", "看空": "{:.0f}", "中性": "{:.0f}", "均置信": "{:.1f}"})
                         .map(lambda v: "color:#4ade80;font-weight:600" if (isinstance(v, int) and v > 0) else "", subset=["看多"])
                         .map(lambda v: "color:#f87171;font-weight:600" if (isinstance(v, int) and v > 0) else "", subset=["看空"]),
                         width="stretch")

            kw = Counter(e.get("keyword", "") for e in entries)
            st.markdown('<div class="sec-label">关键词分布</div>', unsafe_allow_html=True)
            df_kw = pd.DataFrame(kw.items(), columns=["关键词", "数量"]).sort_values("数量", ascending=False)
            _show(_style_base(df_kw.style.hide(axis="index"))
                         .format({"数量": "{:.0f}"}), width="stretch")

        with col_right:
            st.markdown('<div class="sec-label">推文评分明细</div>', unsafe_allow_html=True)
            for e in entries:
                score = e.get("score", "neutral")
                color = SCORE_COLORS.get(score, "#8b98a9")
                emoji = SCORE_EMOJI.get(score, "")
                conf = "⭐" * e.get("confidence", 1) + "☆" * (5 - e.get("confidence", 1))
                st.markdown(f"""
                <div class="sentiment-card" style="border-left-color:{color}">
                  <div class="meta-row">{emoji} <strong>{e.get("account", "?")}</strong> · 置信 {conf}
                  &nbsp;|&nbsp; {e.get("keyword", "")} &nbsp;|&nbsp; {(e.get("created_at", "") or "")[:10]}</div>
                  <div class="tweet-body">{e.get("tweet_text", "")}</div>
                </div>
                """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
#  Page: Watchlist 跟庄
# ═══════════════════════════════════════════════════════════════════
def page_watchlist() -> None:
    _page_header("👀", "Watchlist 跟庄", "tvscreener 快照 · 黄金 / 期货 / 指数 / A股")
    ts = latest_quote_ts()
    snapshots = load_quote_snapshots()
    if not snapshots:
        st.info("还没有 watchlist 快照。运行 scripts/fetch-tvscreener-watchlist.py --save-db")
        return
    if ts:
        stal = _staleness(ts)
        st.markdown(
            f'<div class="svc"><span class="svc-name">最新快照</span>'
            f'<span>{ts} · {_pill(stal, "ok" if "分钟" in stal or "s 前" in stal else "warn")}</span></div>',
            unsafe_allow_html=True)

    df = pd.DataFrame(snapshots)
    df["信号"] = df.apply(lambda r: _signal_tags(r), axis=1)
    if {"close", "sma200"}.issubset(df.columns):
        df["SMA200 距离%"] = ((df["close"] - df["sma200"]) / df["sma200"].replace(0, float("nan")) * 100).round(2)
    rename = {
        "tv_symbol": "代码", "local_name": "名称", "asset_class": "类型",
        "close": "收盘", "change_percent": "涨跌%", "volume": "成交量",
        "rsi": "RSI", "sma20": "SMA20", "sma50": "SMA50", "sma200": "SMA200",
        "recommend_all": "评分",
    }
    cols = [c for c in (
        "tv_symbol", "local_name", "asset_class", "close", "change_percent", "volume",
        "rsi", "sma20", "sma50", "sma200", "SMA200 距离%", "recommend_all", "信号",
    ) if c in df.columns]
    view = df[cols].rename(columns=rename)

    fmt = {k: v for k, v in {
        "收盘": "{:,.2f}", "涨跌%": "{:+.2f}%", "成交量": "{:,.0f}",
        "RSI": "{:.1f}", "SMA20": "{:,.2f}", "SMA50": "{:,.2f}", "SMA200": "{:,.2f}",
        "SMA200 距离%": "{:+.2f}%", "评分": "{:+.2f}",
    }.items() if k in view.columns}
    styled = _style_base(view.style.hide(axis="index")).format(fmt, na_rep="—")
    if "涨跌%" in view.columns:
        styled = styled.map(lambda v: _num_style(v, ""), subset=["涨跌%"])
    if "SMA200 距离%" in view.columns:
        styled = styled.map(lambda v: _num_style(v, ""), subset=["SMA200 距离%"])
    if "RSI" in view.columns:
        styled = styled.map(_rsi_style, subset=["RSI"])
    if "评分" in view.columns:
        styled = styled.map(
            lambda v: "color:#fbbf24;font-weight:600" if (isinstance(v, (int, float)) and v >= 0.5) else
                      ("color:#f87171;font-weight:600" if (isinstance(v, (int, float)) and v <= -0.3) else ""),
            subset=["评分"])
    _show(styled)

    st.markdown('<div class="sec-label">涨跌幅</div>', unsafe_allow_html=True)
    df_chg = df.dropna(subset=["change_percent"]).sort_values("change_percent", ascending=False)
    if not df_chg.empty:
        col1, col2 = st.columns(2)
        top = df_chg.head(3)[["local_name", "tv_symbol", "change_percent"]].rename(
            columns={"local_name": "名称", "tv_symbol": "代码", "change_percent": "涨跌%"})
        bottom = df_chg.tail(3).iloc[::-1][["local_name", "tv_symbol", "change_percent"]].rename(
            columns={"local_name": "名称", "tv_symbol": "代码", "change_percent": "涨跌%"})
        with col1:
            st.markdown('<div class="sec-label">涨幅榜</div>', unsafe_allow_html=True)
            _show(_style_base(top.style.hide(axis="index"))
                  .format({"涨跌%": "{:+.2f}%"})
                  .map(lambda v: _num_style(v, ""), subset=["涨跌%"]))
        with col2:
            st.markdown('<div class="sec-label">跌幅榜</div>', unsafe_allow_html=True)
            _show(_style_base(bottom.style.hide(axis="index"))
                  .format({"涨跌%": "{:+.2f}%"})
                  .map(lambda v: _num_style(v, ""), subset=["涨跌%"]))
        bars = df_chg.copy()
        bars["name"] = bars["local_name"].fillna(bars["tv_symbol"])
        bars["tone"] = bars["change_percent"].map(lambda v: "up" if v >= 0 else "down")
        ch = alt.Chart(bars).mark_bar().encode(
            x=alt.X("name:N", sort="-y", title=None),
            y=alt.Y("change_percent:Q", title="涨跌%"),
            color=alt.Color("tone:N", scale=alt.Scale(domain=["up", "down"], range=["#4ade80", "#f87171"]), legend=None),
            tooltip=["name", "change_percent"],
        ).properties(height=240, width=980)
        st.altair_chart(_alt_dark(ch), width="stretch")

    alerts = df[df["信号"].str.contains("超买|超卖")]
    if not alerts.empty:
        st.markdown('<div class="sec-label">⚠️ RSI 极端提示</div>', unsafe_allow_html=True)
        _show(_style_base(alerts[["local_name", "tv_symbol", "rsi", "信号"]].style.hide(axis="index"))
                     .format({"rsi": "{:.1f}"})
                     .map(_rsi_style, subset=["rsi"]), width="stretch")
    else:
        st.caption("当前无 RSI 极端（>70 超买 / <30 超卖）标的")


# ═══════════════════════════════════════════════════════════════════
#  Page: 运行状态
# ═══════════════════════════════════════════════════════════════════
@_frag(run_every=60)
def _status_panel() -> None:
    st.markdown('<div class="sec-label">服务健康</div>', unsafe_allow_html=True)
    for r in _service_checks() + _launchd_rows():
        ok = r["状态"] == "✅"
        warn = r["状态"] == "⚠️"
        tone = "ok" if ok else ("warn" if warn else "err")
        label = "运行中" if ok else ("异常" if warn else "离线")
        st.markdown(
            f'<div class="svc"><span class="svc-name">{r["服务"]}</span>'
            f'<span>{_pill(r["端口"], "mut")} {_pill(label, tone)}</span></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sec-label">数据新鲜度</div>', unsafe_allow_html=True)
    fresh = []
    qts = latest_quote_ts()
    fresh.append({"数据": "Watchlist 快照", "时间": qts or "无", "距今": _staleness(qts) if qts else "—"})
    s = load_sentiment_latest()
    sdate = s.get("date") if s else None
    fresh.append({"数据": "情绪扫描", "时间": sdate or "无", "距今": _staleness(sdate) if sdate else "—"})
    live = load_live_state()
    snap = (live or {}).get("last_snapshot") or {}
    lts = max((v.get("ts", "") for v in snap.values()), default="")
    fresh.append({"数据": "实时扫描", "时间": lts or "无", "距今": _staleness(lts) if lts else "—"})
    newest_bt = max(RESULTS_DIR.glob("backtest_*.json"), key=lambda p: p.stat().st_mtime, default=None)
    if newest_bt:
        fresh.append({
            "数据": "最新回测",
            "时间": datetime.fromtimestamp(newest_bt.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "距今": _staleness(datetime.fromtimestamp(newest_bt.stat().st_mtime).isoformat()),
        })
    df_fresh = pd.DataFrame(fresh)
    styled_fresh = (_style_base(df_fresh.style.hide(axis="index"))
                    .map(lambda v: f"color:{_staleness_color(str(v))};font-weight:600", subset=["距今"]))
    _show(styled_fresh, width="stretch")

    runs = _runs_count()
    st.caption(f"SQLite runs 表: {runs} 条回测记录" if runs >= 0 else "SQLite 不可用")

    st.markdown('<div class="sec-label">results/ 文件</div>', unsafe_allow_html=True)
    files = sorted(RESULTS_DIR.glob("*"), reverse=True)[:30]
    file_rows = []
    for r in files:
        if r.is_file():
            file_rows.append({
                "文件": r.name,
                "大小": f"{r.stat().st_size:,} B",
                "修改时间": datetime.fromtimestamp(r.stat().st_mtime).strftime("%m-%d %H:%M"),
            })
    if file_rows:
        _show(_style_base(pd.DataFrame(file_rows).style.hide(axis="index")), width="stretch")


def page_status() -> None:
    _page_header("🏠", "运行状态", "服务 · 数据新鲜度 · 结果文件")
    _status_panel()
    st.caption(f"WhaleTrail · {date.today()} · 面板每 60s 自动刷新")


# ═══════════════════════════════════════════════════════════════════
#  Page: 相似选股
# ═══════════════════════════════════════════════════════════════════
@st.cache_data(ttl=3600, show_spinner=False)
def _similarity_universe() -> tuple[dict[str, list[float]], dict[str, str], str]:
    """Return (closes, names, source_label) for the chart-similarity scan.

    Prefers the whole-market baostock ``daily_kline`` table when populated,
    and falls back to the 8-stock A-share watchlist accumulated from
    tvscreener snapshots.  Closes are limited to the last ~300 trading days
    to bound memory; ``rank_similar`` truncates further to the chosen window.
    """
    try:
        repo = Repository(DB_PATH)
        start = (date.today() - pd.Timedelta(days=300)).strftime("%Y-%m-%d")
        closes = repo.daily_closes(start=start)
        names = repo.universe_names()
        repo.close()
        if closes:
            # Ensure the watchlist stocks show Chinese names even when
            # ashare_universe was not populated (e.g. a --codes backfill).
            try:
                for item in load_watchlist(WATCHLIST_PATH):
                    if item.market == "china":
                        names.setdefault(to_baostock_code(item.tv_symbol), item.name)
            except Exception:
                pass
            return closes, names, f"全市场 {len(closes)} 只 · baostock daily_kline"
    except Exception:
        pass

    items = [i for i in load_watchlist(WATCHLIST_PATH) if i.market == "china" and i.tradable]
    closes, names = {}, {}
    for item in items:
        hist = build_daily_history(DB_PATH, item.tv_symbol)
        if hist.empty:
            continue
        closes[item.tv_symbol] = [float(x) for x in hist["close"].tolist()]
        names[item.tv_symbol] = item.name
    return closes, names, f"A股 watchlist {len(closes)} 只 · tvscreener 快照积累"


def page_similar() -> None:
    _page_header("🔍", "相似选股", "DTW 波形相似 · 找与参考标的近期走势相近的股票")
    closes, names, source_label = _similarity_universe()
    if not closes:
        st.info("暂无 A 股历史数据。运行 scripts/fetch-baostock-universe.py（全市场）或先积累 tvscreener 快照。")
        return

    def _label(code: str) -> str:
        name = names.get(code) or ""
        return f"{name} ({code})" if name else code

    all_codes = sorted(closes.keys())
    default = next((c for c in ("sh.601899", "SSE:601899") if c in closes), all_codes[0])

    ref = st.selectbox("参考标的", all_codes, index=all_codes.index(default), format_func=_label)
    window = st.number_input("对比窗口（交易日）", min_value=10, max_value=250, value=90, step=10)
    top_n = st.slider("显示结果数", 5, 50, 20, step=5)
    st.caption(f"数据源: {source_label}")

    if not st.button("🔍 运行相似度扫描", width="stretch"):
        return

    with st.spinner(f"正在扫描 {len(closes)} 只标的…"):
        ranked = rank_similar(closes[ref], closes, window=int(window))
    ranked = [r for r in ranked if r[0] != ref][: top_n]

    if not ranked:
        st.caption("无有效候选（候选历史不足或参考标的无效）")
        return

    rows = [
        {"排名": i, "代码": code, "名称": names.get(code, ""), "DTW 距离": round(dist, 4)}
        for i, (code, dist) in enumerate(ranked, start=1)
    ]
    styled = _style_base(pd.DataFrame(rows).style.hide(axis="index"))
    _show(styled, width="stretch")

    # Normalised overlay: reference + top matches, aligned by bar index.
    st.markdown('<div class="sec-label">归一化走势叠加</div>', unsafe_allow_html=True)
    chart_rows = []
    for code in [ref] + [r[0] for r in ranked[:5]]:
        series = normalize(closes[code][-int(window):])
        chart_rows.extend(
            {"t": t, "value": float(v), "series": names.get(code) or code}
            for t, v in enumerate(series)
        )
    df_ch = pd.DataFrame(chart_rows)
    line = alt.Chart(df_ch).mark_line(strokeWidth=2).encode(
        x=alt.X("t:Q", title="窗口内第 N 个交易日"),
        y=alt.Y("value:Q", title="归一化收盘 (0–1)"),
        color=alt.Color("series:N", legend=alt.Legend(title=None, orient="top")),
        tooltip=["series", "t", "value"],
    ).properties(height=320, width=980)
    st.altair_chart(_alt_dark(line), width="stretch")


# ═══════════════════════════════════════════════════════════════════
#  Route
# ═══════════════════════════════════════════════════════════════════
st.sidebar.markdown(
    '<div class="brand"><div class="brand-logo">🐋</div>'
    '<div><div class="brand-title">WhaleTrail</div>'
    '<div class="brand-sub">GOLD · PAPER TRADING</div></div></div>',
    unsafe_allow_html=True,
)
PAGES = {
    "📊 总览": page_overview,
    "📈 回测结果": page_backtest,
    "🔴 实时信号": page_live,
    "🐋 情绪监控": page_sentiment,
    "👀 Watchlist": page_watchlist,
    "🔍 相似选股": page_similar,
    "🏠 运行状态": page_status,
}
page = st.sidebar.radio("导航", list(PAGES), label_visibility="collapsed")
if st.sidebar.button("🔄 立即刷新", width="stretch"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("总览 / 实时 / 状态页每 60s 自动刷新")
PAGES[page]()
