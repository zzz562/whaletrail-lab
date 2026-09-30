#!/usr/bin/env python3
"""WhaleTrail Dashboard — read-only monitor.

Pages: 黄金 paper / A股 paper / 相似选股 / KOL 评测 / 跟庄复盘.
Bare / renders 相似选股. Other pages: /?page=gold|ashare|kol|genzhuang.
No tab bar — the query string is the only switch.
"""
from __future__ import annotations

import json, logging, os, subprocess, sys, time, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whaletrail.data.baostock_source import to_baostock_code
from whaletrail.data.history import build_daily_history
from whaletrail.data.watchlist import load_watchlist
from whaletrail.metrics.performance import calculate_metrics, compute_trade_pnl
from whaletrail.chips import chip_histogram, distribution_stats
from whaletrail.context import (
    cap_text,
    load_index_moves,
    name_context,
    sector_moves,
)
from whaletrail.screen import (
    DEFAULT_SCREEN_RECALL,
    DEMOS,
    WEIGHT_PRESETS,
    screen_similar,
    trim_partial_session,
)
from whaletrail.similarity import (
    build_scan_pool,
    normalize,
)
from whaletrail.storage.repository import Repository

st.set_page_config(page_title="WhaleTrail", layout="wide")
CODE_ROOT = Path(__file__).resolve().parent.parent
# Prod worktree has no results/; WT_DATA_ROOT points both processes at the lab data.
DATA_ROOT = Path(os.environ.get("WT_DATA_ROOT", CODE_ROOT)).expanduser().resolve()
RESULTS_DIR = DATA_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "whaletrail.db"
DATA_CACHE_DIR = DATA_ROOT / "data_cache"
WATCHLIST_PATH = DATA_ROOT / "config" / "watchlist.yaml"
CN_TZ = ZoneInfo("Asia/Shanghai")
KOL_ROSTER = [
    "feigeshuogushi", "415254141a", "sd145157", "carla121100",
    "fighterpromoter", "deelu179242", "oldk_gillis", "jimisenlin66474",
    "todo_seguridad", "snake_w", "1044669280a", "lilratchetgurl",
    "bbloveu7777", "eliasvancequant", "archdeng007", "barber_mae68154",
    "aw3ff_", "agucdx",
]
KOL_ROSTER_SET = {a.lower() for a in KOL_ROSTER}
KOL_EVAL_FILES = ("kol_eval.json", "kol_evaluation.json", "ashare_kol_eval.json", "kol_picks.json", "kol_review.json")
GENZHUANG_LABEL_FILES = ("watchlist_labels.json", "genzhuang_labels.json", "genzhuang.json")
VALID_LABELS = {"观察", "接近", "触发"}

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root { --wt-bg:#0a0e17; --wt-surface:#111826; --wt-surface2:#0d1320; --wt-border:#1e2a3a; --wt-text:#e6edf3; --wt-muted:#8b98a9; --wt-gold:#e6b450; --wt-blue:#38bdf8; --wt-up:#4ade80; --wt-down:#f87171; --wt-mono:'JetBrains Mono',ui-monospace,monospace; }
html,body,.stApp,[data-testid="stAppViewContainer"],[data-testid="stHeader"] { background:var(--wt-bg); color:var(--wt-text); font-family:'Inter',sans-serif; }
.block-container { padding:1rem 1.6rem 1.8rem; max-width:1440px; }
#MainMenu, footer { visibility:hidden; height:0; }
header[data-testid="stHeader"] { background:transparent; }
.stAppDeployButton, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { display:none !important; }
section[data-testid="stSidebar"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display:none !important; }
.stTabs [data-baseweb="tab-list"] { gap:4px; overflow-x:auto; flex-wrap:nowrap; border-bottom:1px solid var(--wt-border); margin-bottom:8px; }
.stTabs [data-baseweb="tab"] { color:var(--wt-muted) !important; background:transparent !important; padding:8px 12px !important; font-weight:600 !important; }
.stTabs [aria-selected="true"] { color:var(--wt-gold) !important; border-bottom:2px solid var(--wt-gold) !important; }
.topbar { display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin:0 0 8px; }
.topbar .brand-title { font-weight:800; font-size:1.05rem; }
.topbar .brand-sub { font-size:10px; letter-spacing:.18em; color:var(--wt-gold); font-weight:700; }
.kicker { font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--wt-muted); font-weight:700; }
.page-title { font-size:1.35rem; font-weight:800; letter-spacing:-.02em; margin:0 0 2px; color:var(--wt-text); }
.page-sub { color:var(--wt-muted); font-size:.82rem; margin-bottom:12px; }
.m-card { background:var(--wt-surface); border:1px solid var(--wt-border); border-radius:10px; padding:11px 14px 10px; position:relative; overflow:hidden; height:100%; }
.m-card::before { content:''; position:absolute; top:0; left:0; right:0; height:2px; background:var(--m-accent,var(--wt-gold)); }
.m-label { font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--wt-muted); font-weight:600; }
.m-value { font-family:var(--wt-mono); font-size:1.45rem; font-weight:700; margin-top:6px; font-variant-numeric:tabular-nums; }
.m-delta { font-size:.8rem; margin-top:4px; font-variant-numeric:tabular-nums; }
.m-sub { font-size:.74rem; color:var(--wt-muted); margin-top:2px; }
.pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }
.pill-ok { background:rgba(74,222,128,.1); color:var(--wt-up); border:1px solid rgba(74,222,128,.35); }
.pill-err { background:rgba(248,113,113,.1); color:var(--wt-down); border:1px solid rgba(248,113,113,.35); }
.pill-warn { background:rgba(251,191,36,.1); color:#fbbf24; border:1px solid rgba(251,191,36,.35); }
.pill-mut { background:rgba(139,152,169,.08); color:var(--wt-muted); border:1px solid rgba(139,152,169,.3); }
.pill-buy { background:rgba(74,222,128,.1); color:var(--wt-up); border:1px solid rgba(74,222,128,.35); }
.pill-sell { background:rgba(248,113,113,.1); color:var(--wt-down); border:1px solid rgba(248,113,113,.35); }
.svc { display:flex; justify-content:space-between; align-items:center; padding:7px 12px; border:1px solid var(--wt-border); border-radius:8px; background:var(--wt-surface2); margin-bottom:6px; }
.note { border:1px solid var(--wt-border); border-radius:8px; padding:8px 12px; color:var(--wt-muted); font-size:.8rem; background:var(--wt-surface2); margin:0 0 12px; }
.sec-label { font-size:.92rem; letter-spacing:0; text-transform:none; color:var(--wt-text); font-weight:700; margin:16px 0 8px; }
.brand { padding:4px 0 14px; border-bottom:1px solid var(--wt-border); margin-bottom:12px; }
.brand-title { font-weight:800; font-size:1.05rem; }
.brand-sub { font-size:10px; letter-spacing:.18em; color:var(--wt-gold); font-weight:700; margin-top:2px; }
[data-testid="stDataFrame"] { border:1px solid var(--wt-border); border-radius:10px; overflow:hidden; }
[data-testid="stVegaLiteChart"] { border:1px solid var(--wt-border); border-radius:10px; padding:6px; background:var(--wt-surface); overflow:hidden; max-width:100%; }
[data-testid="stVegaLiteChart"] > div, [data-testid="stVegaLiteChart"] svg { max-width:100% !important; }
[data-testid="stHorizontalBlock"] { gap:1.25rem; align-items:flex-start; }
[data-testid="column"] { min-width:0; overflow:hidden; }
[data-testid="stElementToolbar"], [data-testid="stElementToolbarButton"] { display:none !important; }
.stButton > button { background:var(--wt-surface); border:1px solid var(--wt-border); color:var(--wt-text); border-radius:8px; font-weight:600; min-height:38px; }
button[kind="primary"],
[data-testid="stBaseButton-primary"],
[data-testid="baseButton-primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background:#e6b450 !important; color:#0a0e17 !important; border:0 !important;
  font-weight:700 !important; font-size:.95rem !important; min-height:38px !important;
  letter-spacing:0; border-radius:8px !important;
}
[data-testid="stWidgetLabel"] p { font-size:.85rem; font-weight:600; color:var(--wt-text); }
[data-testid="stVerticalBlockBorderWrapper"] { background:var(--wt-surface); border-radius:10px; }
.opt-rule { border-top:1px solid var(--wt-border); margin:2px 0 10px; padding-top:10px; font-size:.9rem; font-weight:700; color:var(--wt-text); }
.opt-rule span { color:var(--wt-muted); font-weight:500; }
.st-key-similar_scan_btn { margin-top: 10px; }
.st-key-similar_scan_btn button { width:100% !important; min-height:44px !important; font-size:1rem !important; }
</style>""", unsafe_allow_html=True)

_fragment = getattr(st, "fragment", None)
_autorun_every = None if __import__("os").environ.get("WHALETRAIL_NO_AUTOREFRESH") else 60

def _frag(run_every: Optional[int] = None):
    if _fragment is None:
        return lambda f: f
    return _fragment(run_every=_autorun_every)

def _cn_today() -> date:
    return datetime.now(CN_TZ).date()

def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

@st.cache_data(ttl=60, show_spinner=False)
def list_backtest_files() -> list[str]:
    files = sorted(RESULTS_DIR.glob("backtest_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files]

@st.cache_data(ttl=60, show_spinner=False)
def load_backtest(name: str) -> dict:
    with open(RESULTS_DIR / name, encoding="utf-8") as f:
        return json.load(f)

@st.cache_data(ttl=60, show_spinner=False)
def load_live_state() -> Optional[dict]:
    return _read_json(RESULTS_DIR / "paper_live_state.json")

@st.cache_data(ttl=60, show_spinner=False)
def load_ashare_paper() -> Optional[dict]:
    return _read_json(RESULTS_DIR / "ashare_paper_state.json")

@st.cache_data(ttl=60, show_spinner=False)
def latest_quote_ts() -> Optional[str]:
    try:
        repo = Repository(DB_PATH)
        ts = repo.latest_quote_timestamp()
        repo.close()
        return ts
    except Exception:
        return None

def _cache_parquet_path(symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace("\\", "_").replace(":", "_")
    return DATA_CACHE_DIR / f"{safe}.parquet"

@st.cache_data(ttl=3600, show_spinner=False)
def load_cached_close(symbol: str) -> Optional[pd.DataFrame]:
    path = _cache_parquet_path(symbol)
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

@st.cache_data(ttl=60, show_spinner=False)
def load_kol_eval_rows() -> list[dict]:
    blobs: list[Any] = []
    for name in KOL_EVAL_FILES:
        data = _read_json(RESULTS_DIR / name)
        if data is not None:
            blobs.append(data)
    for path in sorted(RESULTS_DIR.glob("kol_eval_*.json")):
        data = _read_json(path)
        if data is not None:
            blobs.append(data)
    rows: list[dict] = []
    for blob in blobs:
        rows.extend(_normalize_kol_blob(blob))
    try:
        repo = Repository(DB_PATH)
        tables = {r[0] for r in repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("kol_eval", "kol_picks", "ashare_kol_eval"):
            if table not in tables:
                continue
            try:
                db_rows = repo.conn.execute(f"SELECT * FROM {table}").fetchall()
            except Exception:
                continue
            for r in db_rows:
                rows.append(dict(r))
        repo.close()
    except Exception:
        pass
    return rows

def _normalize_kol_blob(blob: Any) -> list[dict]:
    if blob is None:
        return []
    if isinstance(blob, list):
        return [x for x in blob if isinstance(x, dict)]
    if not isinstance(blob, dict):
        return []
    for key in ("picks", "evaluations", "rows", "entries", "items"):
        val = blob.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    accounts = blob.get("accounts")
    if isinstance(accounts, dict):
        out = []
        for acc, items in accounts.items():
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        row = dict(it); row.setdefault("account", acc); out.append(row)
            elif isinstance(items, dict):
                row = dict(items); row.setdefault("account", acc); out.append(row)
        return out
    if any(k in blob for k in ("account", "symbol", "ticker", "code")):
        return [blob]
    return []

@st.cache_data(ttl=60, show_spinner=False)
def load_genzhuang_labels() -> dict[str, str]:
    blobs: list[Any] = []
    for name in GENZHUANG_LABEL_FILES:
        data = _read_json(RESULTS_DIR / name)
        if data is not None:
            blobs.append(data)
    out: dict[str, str] = {}
    for blob in blobs:
        out.update(_normalize_labels(blob))
    return out

def _normalize_labels(blob: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(blob, dict):
        items = blob.get("items") or blob.get("labels") or blob.get("watchlist")
        if isinstance(items, list):
            blob = items
        elif isinstance(items, dict):
            blob = items
        if isinstance(blob, dict) and not isinstance(items, list):
            for k, v in blob.items():
                if k in ("items", "labels", "watchlist"):
                    continue
                label = v.get("label") if isinstance(v, dict) else v
                if str(label) in VALID_LABELS:
                    out[str(k)] = str(label)
            return out
    if isinstance(blob, list):
        for it in blob:
            if not isinstance(it, dict):
                continue
            label = it.get("label") or it.get("状态") or it.get("tag")
            if str(label) not in VALID_LABELS:
                continue
            for key in ("tv_symbol", "yahoo_symbol", "code", "id", "name", "symbol"):
                if it.get(key):
                    out[str(it[key])] = str(label)
    return out

@st.cache_data(ttl=60, show_spinner=False)
def load_completed_watchlist_bars() -> list[dict]:
    try:
        items = [i for i in load_watchlist(WATCHLIST_PATH) if i.market == "china"]
    except Exception:
        return []
    if not items:
        return []
    today = _cn_today().isoformat()
    labels = load_genzhuang_labels()
    rows: list[dict] = []
    try:
        repo = Repository(DB_PATH)
        has_kline = repo.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_kline'").fetchone()
        for item in items:
            rec = {"名称": item.name, "代码": item.tv_symbol, "标签": _lookup_label(labels, item),
                   "交易日": None, "开": None, "高": None, "低": None, "收": None, "量": None}
            if has_kline:
                try:
                    code = to_baostock_code(item.tv_symbol)
                except ValueError:
                    code = None
                if code:
                    bar = repo.conn.execute(
                        "SELECT trade_date, open, high, low, close, volume FROM daily_kline WHERE code = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                        (code, today),
                    ).fetchone()
                    if bar:
                        rec["交易日"] = bar["trade_date"]; rec["开"] = bar["open"]; rec["高"] = bar["high"]
                        rec["低"] = bar["low"]; rec["收"] = bar["close"]; rec["量"] = bar["volume"]
            rows.append(rec)
        repo.close()
    except Exception:
        for item in items:
            rows.append({"名称": item.name, "代码": item.tv_symbol, "标签": _lookup_label(labels, item),
                         "交易日": None, "开": None, "高": None, "低": None, "收": None, "量": None})
    return rows

def _lookup_label(labels: dict[str, str], item) -> Optional[str]:
    keys = [item.tv_symbol, item.id, item.name, item.yahoo_symbol or ""]
    try:
        keys.append(to_baostock_code(item.tv_symbol))
    except ValueError:
        pass
    for k in keys:
        if k and k in labels:
            return labels[k]
    return None

def _staleness(ts_str: str) -> str:
    if not ts_str:
        return "?"
    try:
        ts = pd.to_datetime(ts_str)
        if pd.isna(ts):
            return "?"
        secs = int((datetime.now() - ts.to_pydatetime()).total_seconds())
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
    trades_raw = data.get("trades", [])
    enriched = trades_raw if trades_raw and "pnl" in (trades_raw[0] or {}) else compute_trade_pnl(trades_raw)
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

def _load_gc_contrast() -> Optional[pd.DataFrame]:
    """GC=F only. Never fall back to GLD.parquet."""
    for symbol in ("GC=F", "GC_F"):
        path = _cache_parquet_path(symbol)
        if path.name.upper().startswith("GLD"):
            continue
        df = load_cached_close(symbol)
        if df is not None:
            return df
    return None

def _scaled_close_series(symbol: str, start: str, end: str, initial_cash: float, name: str) -> Optional[pd.Series]:
    if str(symbol).upper().replace("_", "") in {"GCF", "GC=F"}:
        df = _load_gc_contrast()
    else:
        df = load_cached_close(symbol)
    if df is None:
        return None
    try:
        b = df.loc[pd.Timestamp(start): pd.Timestamp(end)]
        if b.empty or len(b) < 2 or "close" not in b.columns:
            return None
        first = float(b["close"].iloc[0])
        if first <= 0:
            return None
        s = (b["close"] / first * initial_cash).rename(name)
        s.index = pd.to_datetime(s.index)
        return s
    except Exception:
        return None

def _benchmark_series(data: dict, initial_cash: float) -> Optional[pd.Series]:
    symbol, start, end = data.get("symbol"), data.get("start"), data.get("end")
    if not symbol or not start or not end:
        return None
    return _scaled_close_series(symbol, start, end, initial_cash, "买入持有")

def _series_return(series: Optional[pd.Series]) -> Optional[float]:
    if series is None or len(series) < 2:
        return None
    start, end = float(series.iloc[0]), float(series.iloc[-1])
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0

def _series_drawdown(series: Optional[pd.Series]) -> Optional[float]:
    if series is None or series.empty:
        return None
    return float((series / series.cummax() - 1.0).min() * 100.0)

def _parse_signals(mapping: dict) -> list[dict]:
    rows = []
    for key, ts in (mapping or {}).items():
        parts = key.split("|")
        if len(parts) == 3:
            rows.append({"symbol": parts[0], "strategy": parts[1], "side": parts[2], "date": str(ts)[:10]})
    return rows

def _service_checks() -> list[dict]:
    checks = {"Dashboard": ("http://127.0.0.1:8766", 8766), "OpenClaw Gateway": ("http://127.0.0.1:18789/health", 18789), "Ollama": ("http://127.0.0.1:11434/api/tags", 11434)}
    rows = []
    for name, (url, port) in checks.items():
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                rows.append({"服务": name, "端口": str(port), "状态": "ok" if r.status < 400 else "warn"})
        except Exception:
            rows.append({"服务": name, "端口": str(port), "状态": "err"})
    return rows

def _launchd_rows() -> list[dict]:
    try:
        out = subprocess.check_output(["launchctl", "list"], text=True, timeout=5)
    except Exception:
        return []
    rows = []
    for label, display in [("ai.whaletrail-live", "Paper Live"), ("ai.whaletrail-dashboard", "Dashboard"), ("homebrew.mxcl.ollama", "Ollama"), ("ai.openclaw.gateway", "OpenClaw Gateway"), ("com.zeph.reverse-tunnel", "Reverse tunnel")]:
        rows.append({"服务": f"launchd: {display}", "端口": "-", "状态": "ok" if label in out else "err"})
    return rows

def _runs_count() -> int:
    try:
        repo = Repository(DB_PATH)
        n = len(repo.list_runs(limit=1000)); repo.close(); return n
    except Exception:
        return -1

def _pick_gold_sma(files: list[str]) -> Optional[str]:
    """Prefer daily GLD gold_sma; skip 5m observation runs for the paper book."""
    for name in files:
        low = name.lower()
        try:
            d = load_backtest(name)
        except Exception:
            d = {}
        strat = str(d.get("strategy", "")).lower()
        sym = str(d.get("symbol", "")).upper()
        interval = str(d.get("interval") or "").lower()
        is_gold = (strat.startswith("gold_sma") and sym == "GLD") or ("gold_sma" in low and "gld" in low)
        if not is_gold:
            continue
        if "5m" in low or interval in {"5m", "5min", "minute"}:
            continue
        return name
    for name in files:
        low = name.lower()
        if "gold_sma" in low and "gld" in low:
            return name
    return None

def _page_header(title: str, sub: str = "") -> None:
    sub_html = f'<div class="page-sub">{sub}</div>' if sub else ""
    st.markdown(f'<div class="page-title">{title}</div>{sub_html}', unsafe_allow_html=True)

def _sec(label: str) -> None:
    st.markdown(f'<div class="sec-label">{label}</div>', unsafe_allow_html=True)

def _note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)

def _card(label: str, value: str, delta: str = "", delta_color: str = "", sub: str = "", accent: str = "#e6b450") -> str:
    delta_html = f'<div class="m-delta" style="color:{delta_color}">{delta}</div>' if delta else ""
    sub_html = f'<div class="m-sub">{sub}</div>' if sub else ""
    return f'<div class="m-card" style="--m-accent:{accent}"><div class="m-label">{label}</div><div class="m-value">{value}</div>{delta_html}{sub_html}</div>'

def _card_row(cards: list[dict], cols: int = 4) -> None:
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
    kwargs.setdefault("width", "stretch"); kwargs.setdefault("hide_index", True)
    st.dataframe(obj, **kwargs)

def _alt_dark(chart: alt.Chart) -> alt.Chart:
    return (chart.configure(background="#111826").configure_view(strokeOpacity=0)
            .configure_axis(gridColor="#1e2a3a", domainColor="#243244", labelColor="#8b98a9", titleColor="#8b98a9", tickColor="#243244")
            .configure_legend(labelColor="#8b98a9", titleColor="#8b98a9")
            .configure_title(color="#e6edf3"))

def _style_base(s: Any) -> Any:
    return (s.set_properties(**{"color": "#e6edf3", "font-family": "var(--wt-mono)", "font-size": "12.5px", "padding": "6px 12px", "border-bottom": "1px solid #182231", "text-align": "right"})
            .set_table_styles([{"selector": "th", "props": [("background-color", "#0d1320"), ("color", "#8b98a9"), ("font-size", "12px"), ("text-transform", "none"), ("letter-spacing", "0"), ("padding", "8px 12px"), ("border-bottom", "1px solid #1e2a3a")]}]))

def _num_style(v, fmt: str = "") -> str:
    try:
        if pd.isna(v):
            return ""
        return f"color:{_num_color(float(v))};font-weight:600"
    except (TypeError, ValueError):
        return ""

def _side_style(v) -> str:
    s = str(v).upper()
    if s == "BUY":
        return "color:#4ade80;font-weight:700"
    if s == "SELL":
        return "color:#f87171;font-weight:700"
    return ""

def _label_style(v) -> str:
    s = str(v)
    if s == "触发":
        return "color:#e6b450;font-weight:700"
    if s == "接近":
        return "color:#fbbf24;font-weight:600"
    if s == "观察":
        return "color:#38bdf8;font-weight:600"
    return "color:#8b98a9"

def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.2f}%"

def _gold_book_section() -> None:
    _sec("黄金账 · GLD gold_sma · 对照 SPY / GC=F")
    _note("账本是 GLD 日线 gold_sma。GC=F 日线只作金价对照，读自己的 Parquet，不进 GLD 缓存。两行都不是银行牌价、不是境内可玩。本页无纸黄金 / AU9999。gold_sma 弱于买入持有，价值在压回撤。")
    files = list_backtest_files()
    gold_name = _pick_gold_sma(files)
    if not gold_name:
        st.info("没有 GLD gold_sma 纸上账结果。缺则空。可在 Mini 跑 scripts/run-backtest.py gold_sma GLD 生成（不改本页策略）。")
        return
    data = load_backtest(gold_name)
    enriched, metrics, initial_cash = _backtest_metrics(data)
    bh = _benchmark_series(data, initial_cash)
    spy = None
    start, end = data.get("start"), data.get("end")
    gc = None
    if start and end:
        spy = _scaled_close_series("SPY", start, end, initial_cash, "SPY")
        gc = _scaled_close_series("GC=F", start, end, initial_cash, "GC=F")
    ret = metrics.get("total_return"); dd = metrics.get("max_drawdown")
    bh_ret = _series_return(bh); spy_ret = _series_return(spy); bh_dd = _series_drawdown(bh)
    gc_ret = _series_return(gc)
    _card_row([
        {"label": "gold_sma", "value": _fmt_pct(ret), "delta": f"最大回撤 {dd:.2f}%" if dd is not None else "—", "delta_color": _num_color(ret or 0), "sub": f"{data.get('start','?')} → {data.get('end','?')}", "accent": "#e6b450"},
        {"label": "买入持有 GLD", "value": _fmt_pct(bh_ret), "delta": f"最大回撤 {bh_dd:.2f}%" if bh_dd is not None else "本地缓存缺失则空", "delta_color": _num_color(bh_ret or 0) if bh_ret is not None else "#8b98a9", "sub": "GLD.parquet", "accent": "#38bdf8"},
        {"label": "SPY 对照", "value": _fmt_pct(spy_ret), "delta": "监控/对照 · 不是境内可玩", "delta_color": _num_color(spy_ret or 0) if spy_ret is not None else "#8b98a9", "sub": "SPY.parquet" if spy is not None else "缓存缺失", "accent": "#38bdf8"},
        {"label": "GC=F 金价对照", "value": _fmt_pct(gc_ret), "delta": "不是银行牌价 · 不是境内可玩", "delta_color": _num_color(gc_ret or 0) if gc_ret is not None else "#8b98a9", "sub": "GC=F.parquet" if gc is not None else "对照缓存缺失则空", "accent": "#fbbf24"},
    ])
    if bh_ret is not None and ret is not None and ret < bh_ret:
        st.caption("本结果中 gold_sma 弱于买入持有；对照意义在回撤。")
    equity = data.get("equity_curve", [])
    if equity:
        df_eq = pd.DataFrame(equity)
        df_eq["date"] = pd.to_datetime(df_eq["date"])
        df_eq = df_eq.set_index("date").rename(columns={"equity": "gold_sma"})
        if bh is not None:
            df_eq = df_eq.join(bh, how="left")
        if spy is not None:
            df_eq = df_eq.join(spy, how="left")
        if gc is not None:
            df_eq = df_eq.join(gc, how="left")
        eq = df_eq.reset_index()
        value_cols = [c for c in ("gold_sma", "买入持有", "SPY", "GC=F") if c in eq.columns]
        long = eq.melt(id_vars=["date"], value_vars=value_cols, var_name="系列", value_name="权益")
        rng = ["#e6b450", "#38bdf8", "#a78bfa", "#fbbf24"][: len(value_cols)]
        ch = alt.Chart(long.dropna(subset=["权益"])).mark_line(strokeWidth=2).encode(
            x=alt.X("date:T", title=None), y=alt.Y("权益:Q", title=None),
            color=alt.Color("系列:N", scale=alt.Scale(domain=value_cols, range=rng), legend=alt.Legend(title=None, orient="top")),
        ).properties(height=280, width=980)
        st.altair_chart(_alt_dark(ch), width="stretch")

def _backtest_section() -> None:
    _sec("回测结果")
    files = list_backtest_files()
    if not files:
        st.info("还没有回测结果。运行 scripts/run-backtest.py 生成。")
        return
    selected = st.selectbox("选择回测", files)
    data = load_backtest(selected)
    enriched, metrics, initial_cash = _backtest_metrics(data)
    fe = float(data.get("final_equity", 0) or 0); ret = metrics.get("total_return", 0.0); dd = metrics.get("max_drawdown", 0.0)
    _card_row([
        {"label": "最终权益", "value": f"${fe:,.0f}", "delta": f"初始 ${initial_cash:,.0f}", "delta_color": "#8b98a9", "sub": f"手续费 ${data.get('total_commission', 0):.2f}", "accent": "#e6b450"},
        {"label": "总收益率", "value": f"{ret:+.2f}%", "delta": "策略累计", "delta_color": _num_color(ret), "sub": "", "accent": _num_color(ret)},
        {"label": "年化收益率", "value": f"{metrics.get('annual_return', 0):+.2f}%", "delta": "252 交易日", "delta_color": _num_color(metrics.get("annual_return", 0)), "sub": "", "accent": "#38bdf8"},
        {"label": "最大回撤", "value": f"{dd:.2f}%", "delta": "峰值回撤", "delta_color": "#f87171", "sub": "", "accent": "#f87171"},
    ])
    st.caption(f"策略: {data.get('strategy', '?')} · 标的: {data.get('symbol', '?')} · {data.get('start', '?')} → {data.get('end', '?')} · 交易 {len(enriched)} 次")
    equity = data.get("equity_curve", [])
    if equity:
        df_eq = pd.DataFrame(equity); df_eq["date"] = pd.to_datetime(df_eq["date"]); df_eq = df_eq.set_index("date")
        bench = _benchmark_series(data, initial_cash)
        if bench is not None:
            df_eq = df_eq.join(bench, how="left")
        eq = df_eq.reset_index()
        value_cols = [c for c in ("equity", "买入持有") if c in eq.columns]
        long = eq.melt(id_vars=["date"], value_vars=value_cols, var_name="系列", value_name="权益")
        eq_ch = alt.Chart(long.dropna(subset=["权益"])).mark_line(strokeWidth=2).encode(
            x=alt.X("date:T", title=None), y=alt.Y("权益:Q", title=None),
            color=alt.Color("系列:N", scale=alt.Scale(domain=["equity", "买入持有"], range=["#e6b450", "#38bdf8"]), legend=alt.Legend(title=None, orient="top")),
        ).properties(height=280, width=980)
        st.altair_chart(_alt_dark(eq_ch), width="stretch")
        dd_df = df_eq.reset_index(); dd_df["回撤%"] = (df_eq["equity"] / df_eq["equity"].cummax() - 1).to_numpy() * 100
        dd_ch = alt.Chart(dd_df).mark_area(color="#f87171", opacity=0.45).encode(x=alt.X("date:T", title=None), y=alt.Y("回撤%:Q", title=None)).properties(height=180, width=980)
        st.altair_chart(_alt_dark(dd_ch), width="stretch")
    if enriched:
        _sec("交易记录")
        df_t = pd.DataFrame(enriched)
        if "date" in df_t.columns:
            df_t["date"] = pd.to_datetime(df_t["date"]).dt.date
        cols = [c for c in ("date", "symbol", "side", "quantity", "price", "commission", "pnl") if c in df_t.columns]
        styled = (_style_base(df_t[cols].style.hide(axis="index")).format({"quantity": "{:,.2f}", "price": "${:,.2f}", "commission": "${:,.2f}", "pnl": "${:,.2f}"}, na_rep="—").map(_side_style, subset=["side"]).map(lambda v: _num_style(v, ""), subset=["pnl"]))
        _show(styled, width="stretch")

@_frag(run_every=60)
def _live_panel() -> None:
    live = load_live_state()
    if not live:
        st.info("还没有实时扫描数据。运行 scripts/paper-live.py tick。5m / live 仅观察，不作进场依据。")
        return
    snap = live.get("last_snapshot") or {}
    if snap:
        cards = []
        for sym, info in snap.items():
            price = info.get("price"); stal = _staleness(info.get("ts", ""))
            accent = "#e6b450" if str(sym).upper() in ("GLD", "GC=F") else "#38bdf8"
            bits = ["观察"]
            if str(sym).upper() in ("GLD", "GC=F"):
                bits.append("监控/对照 · 不是银行牌价 · 不是纸黄金账")
            if info.get("ts"):
                bits.append(str(info.get("ts")))
            cards.append({"label": f"{sym} 扫描价", "value": f"${price:,.2f}" if price else "—", "delta": stal, "delta_color": _staleness_color(stal) if stal else "#8b98a9", "sub": " · ".join(bits), "accent": accent})
        _card_row(cards, cols=max(1, min(4, len(cards))))
    signals = _parse_signals(live.get("last_signals") or {})
    if signals:
        df_s = pd.DataFrame(signals); today = date.today().isoformat()
        df_s["标记"] = "观察"
        df_s = df_s.rename(columns={"symbol": "标的", "strategy": "策略", "side": "方向", "date": "日期"})
        _sec(f"策略信号 · {len(df_s)} 条 · 全部观察")
        _show(_style_base(df_s[["标的", "策略", "方向", "日期", "标记"]].style.hide(axis="index")).map(_side_style, subset=["方向"]))
    else:
        st.info("暂无信号记录")
    positions = live.get("positions") or {}
    if positions:
        _sec("当前持仓")
        _show(_style_base((pd.DataFrame(positions).T if isinstance(positions, dict) else pd.DataFrame(positions)).style.hide(axis="index")), width="stretch")
    else:
        st.caption("当前无持仓")

def _ashare_paper_section() -> None:
    _sec("A股 paper · 15:30 日频")
    _note("A 股 15:30 paper 账。观察 / 接近 / 触发只在「跟庄复盘」。黄金矿股的跟庄标签也在那页，不进黄金 Paper。")
    state = load_ashare_paper()
    if not state:
        st.info("没有 A 股 paper 状态（results/ashare_paper_state.json）。缺则空。")
        return
    positions = state.get("positions") or {}; pending = state.get("pending") or {}; trades = state.get("trades") or []
    _card_row([
        {"label": "持仓", "value": str(len(positions)), "delta": "LONG", "delta_color": "#8b98a9", "sub": "", "accent": "#e6b450"},
        {"label": "挂单", "value": str(len(pending)), "delta": "待成交", "delta_color": "#8b98a9", "sub": "", "accent": "#fbbf24"},
        {"label": "已平仓", "value": str(len(trades)), "delta": "trades", "delta_color": "#8b98a9", "sub": "", "accent": "#38bdf8"},
    ], cols=3)
    if positions:
        df_p = pd.DataFrame(positions).T.reset_index().rename(columns={"index": "代码"})
        _show(_style_base(df_p.style.hide(axis="index")), width="stretch")
    else:
        st.caption("无持仓")
    if pending:
        df_pend = pd.DataFrame(pending).T.reset_index().rename(columns={"index": "代码"})
        _show(_style_base(df_pend.style.hide(axis="index")), width="stretch")
    if trades:
        _show(_style_base(pd.DataFrame(trades).style.hide(axis="index")), width="stretch")

def _gold_ledger_tables(data: dict, enriched: list[dict]) -> None:
    """持仓 / 成交 / 权益 — human ledger, not a strategy menu."""
    _sec("权益")
    equity = data.get("equity_curve", [])
    if equity:
        df_e = pd.DataFrame(equity)
        if "date" in df_e.columns:
            df_e["date"] = pd.to_datetime(df_e["date"]).dt.date
        cols = [c for c in ("date", "equity") if c in df_e.columns]
        _show(_style_base(df_e[cols].tail(30).style.hide(axis="index")).format({"equity": "${:,.2f}"}, na_rep="—"), width="stretch")
        st.caption("上表最近 30 个权益点；完整曲线见上方。")
    else:
        st.caption("无权益曲线")

    _sec("成交")
    if enriched:
        df_t = pd.DataFrame(enriched)
        if "date" in df_t.columns:
            df_t["date"] = pd.to_datetime(df_t["date"]).dt.date
        cols = [c for c in ("date", "symbol", "side", "quantity", "price", "commission", "pnl") if c in df_t.columns]
        styled = (
            _style_base(df_t[cols].style.hide(axis="index"))
            .format({"quantity": "{:,.2f}", "price": "${:,.2f}", "commission": "${:,.2f}", "pnl": "${:,.2f}"}, na_rep="—")
            .map(_side_style, subset=["side"])
        )
        if "pnl" in cols:
            styled = styled.map(lambda v: _num_style(v, ""), subset=["pnl"])
        _show(styled, width="stretch")
    else:
        st.caption("无成交记录")

    _sec("持仓")
    qty = 0.0
    last_px = None
    for tr in enriched:
        side = str(tr.get("side", "")).upper()
        q = float(tr.get("quantity") or 0)
        px = tr.get("price")
        if side in ("BUY", "LONG"):
            qty += q
            last_px = px
        elif side in ("SELL", "SHORT"):
            qty -= q
            last_px = px
    if abs(qty) > 1e-9:
        row = {"标的": "GLD", "数量": round(qty, 4), "最近成交价": last_px, "说明": "由成交净额推算 · 只读"}
        _show(_style_base(pd.DataFrame([row]).style.hide(axis="index")).format({"最近成交价": "${:,.2f}"}, na_rep="—"), width="stretch")
    else:
        st.caption("当前无持仓（净仓为 0）")

def page_gold_paper() -> None:
    _page_header("黄金 paper", "GLD gold_sma 一本账 · 持仓/成交/权益 · GC=F 只对照 · 不是下单台")
    _gold_book_section()
    files = list_backtest_files()
    gold_name = _pick_gold_sma(files)
    if gold_name:
        data = load_backtest(gold_name)
        enriched, _metrics, _initial_cash = _backtest_metrics(data)
        st.caption(f"账本文件: {gold_name} · 策略 gold_sma · 标的 GLD · 多策略清单未决，本页不展开")
        _gold_ledger_tables(data, enriched)
    _sec("5m / live 扫描 · 观察")
    _note("5m 与 live 扫描仅观察，不作进场依据。纸黄金 / AU9999 不上板。")
    _live_panel()

def page_ashare_paper() -> None:
    _page_header("A股 paper", "空壳 · 策略未决 · 金矿股归本页 · 不是跟庄 · 不是下单台")
    _note("这轮只拆壳。A 股 paper 策略未决；观察/接近/触发只在「跟庄复盘」。紫金等金矿股将来进本页，不进黄金 paper。")
    st.info("A 股 paper 内容待拍。现网 ashare_paper_state 先不展示，避免和跟庄三字混读。")

@st.cache_data(ttl=3600, show_spinner=False)
def _similarity_universe(
    start: str | None = None,
    end: str | None = None,
) -> tuple[dict[str, dict[str, list]], dict[str, str], str, str | None]:
    """Aligned daily bars for the screen. Prefer baostock; never invent OHLC.

    The fourth value is a dropped partial session date, or None.
    """
    try:
        repo = Repository(DB_PATH)
        if start is None and end is None:
            start = (date.today() - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
        bars = repo.daily_bars(start=start, end=end)
        names = repo.universe_names()
        repo.close()
        if bars:
            bars, dropped = trim_partial_session(bars)
            try:
                for item in load_watchlist(WATCHLIST_PATH):
                    if item.market == "china":
                        names.setdefault(to_baostock_code(item.tv_symbol), item.name)
            except Exception:
                pass
            rng = f"{start or '?'}→{end or '最新'}"
            return bars, names, f"全市场 {len(bars)} 只 · baostock daily_kline · {rng}", dropped
    except Exception:
        pass
    if start is not None or end is not None:
        return {}, {}, "无 baostock daily_kline · 固定窗不可用（不拿 tvscreener 冒充日 K）", None
    try:
        items = [i for i in load_watchlist(WATCHLIST_PATH) if i.market == "china"]
    except Exception:
        items = []
    bars, names = {}, {}
    for item in items:
        hist = build_daily_history(DB_PATH, item.tv_symbol)
        if hist.empty:
            continue
        bars[item.tv_symbol] = {
            "trade_date": [d.strftime("%Y-%m-%d") for d in hist.index],
            "open": [float(x) for x in hist["open"].tolist()],
            "high": [float(x) for x in hist["high"].tolist()],
            "low": [float(x) for x in hist["low"].tolist()],
            "close": [float(x) for x in hist["close"].tolist()],
            "volume": [float(x) for x in hist["volume"].tolist()],
        }
        names[item.tv_symbol] = item.name
    return bars, names, f"A股 watchlist {len(bars)} 只 · tvscreener 快照积累（仅 trailing，无换手/筹码）", None


@st.cache_data(ttl=3600, show_spinner=False)
def _market_frame() -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """CSRC industry and benchmark pct_chg. Empty when the analysis DB is absent."""
    try:
        repo = Repository(DB_PATH)
        industry = repo.industry_map()
        moves = load_index_moves(repo.conn)
        repo.close()
        return industry, moves
    except Exception:
        return {}, {}

_PAIR_SCALE = alt.Scale(range=["#e6b450", "#38bdf8"])
_CHART_W = 980


def _ohlc_from_bars(bars: dict, slice_n: int) -> pd.DataFrame:
    """Last *slice_n* bars as an OHLC frame. Empty if the slice is unusable."""
    n = int(slice_n)
    dates = bars.get("trade_date") or []
    if len(dates) < 2:
        return pd.DataFrame()
    def _tail(key: str) -> list:
        xs = bars.get(key) or []
        return xs[-n:]
    data = {
        "trade_date": pd.to_datetime(_tail("trade_date")),
        "open": pd.to_numeric(_tail("open"), errors="coerce"),
        "high": pd.to_numeric(_tail("high"), errors="coerce"),
        "low": pd.to_numeric(_tail("low"), errors="coerce"),
        "close": pd.to_numeric(_tail("close"), errors="coerce"),
        "volume": pd.to_numeric(_tail("volume"), errors="coerce"),
    }
    if bars.get("turn"):
        data["turn"] = pd.to_numeric(_tail("turn"), errors="coerce")
    df = pd.DataFrame(data)
    return df.dropna(subset=["open", "high", "low", "close"])


def _render_kline_panel(
    df: pd.DataFrame,
    title: str,
    width: int = _CHART_W,
    mark_start: date | None = None,
    mark_end: date | None = None,
) -> None:
    """Daily K + volume. Own price axis. Optional yellow band = 圈定区间."""
    if df.empty:
        st.info("这段没有日 K。")
        return
    plot = df.copy()
    plot["up"] = plot["close"] >= plot["open"]
    tips = ["trade_date:T", "open:Q", "high:Q", "low:Q", "close:Q"]
    if "volume" in plot.columns:
        tips.append("volume:Q")
    if "turn" in plot.columns:
        tips.append("turn:Q")
    rules = alt.Chart(plot).mark_rule().encode(
        x="trade_date:T", y="low:Q", y2="high:Q",
        color=alt.condition("datum.up", alt.value("#f87171"), alt.value("#4ade80")),
    )
    candles = alt.Chart(plot).mark_bar(size=5).encode(
        x=alt.X("trade_date:T", title=None),
        y=alt.Y("open:Q", title=None),
        y2="close:Q",
        color=alt.condition("datum.up", alt.value("#f87171"), alt.value("#4ade80")),
        tooltip=tips,
    )
    layers = rules + candles
    if mark_start is not None and mark_end is not None:
        band = alt.Chart(pd.DataFrame({
            "x": [pd.Timestamp(mark_start)],
            "x2": [pd.Timestamp(mark_end)],
        })).mark_rect(opacity=0.18, color="#e6b450").encode(x="x:T", x2="x2:T")
        layers = band + layers
    kline = layers.properties(height=220, width=width, title=title)
    if "volume" in plot.columns and plot["volume"].notna().any():
        vol = alt.Chart(plot).mark_bar(size=5).encode(
            x=alt.X("trade_date:T", title=None),
            y=alt.Y("volume:Q", title="量"),
            color=alt.condition("datum.up", alt.value("#f87171"), alt.value("#4ade80")),
            tooltip=tips,
        ).properties(height=64, width=width)
        ch = alt.vconcat(kline, vol).resolve_scale(x="shared")
    else:
        ch = kline
    st.altair_chart(_alt_dark(ch), width="stretch")


def _chip_snapshot(bars: dict, slice_n: int) -> dict | None:
    """Chip pile on the last bar of this window, in yuan."""
    if not (bars.get("turn") and bars.get("high") and bars.get("low") and bars.get("close")):
        return None
    n = max(1, int(slice_n))
    high = bars["high"][-n:]
    low = bars["low"][-n:]
    close_s = bars["close"][-n:]
    turn = bars["turn"][-n:]
    if not close_s:
        return None
    ts = (bars.get("tradestatus") or [1] * len(close_s))[-n:]
    hist, pmin, pmax = chip_histogram(high, low, close_s, turn, ts)
    if float(sum(hist)) <= 0:
        return None
    last = close_s[-1]
    if last is None or last != last or float(last) <= 0:
        return None
    last = float(last)
    dates = [str(d)[:10] for d in (bars.get("trade_date") or [])]
    return {
        "hist": hist,
        "pmin": float(pmin),
        "pmax": float(pmax),
        "close": last,
        "day": dates[-1] if dates else "",
        "stats": distribution_stats(hist, float(pmin), float(pmax), last),
    }


def _last_chip_chart(snap: dict, title: str, width: int = 460) -> alt.Chart:
    """Last-day chip bars, close as a gold rule, winner ratio on that rule."""
    hist = snap["hist"]
    pmin, pmax = float(snap["pmin"]), float(snap["pmax"])
    close = float(snap["close"])
    n = len(hist)
    if pmax > pmin:
        step = (pmax - pmin) / n
        centers = [pmin + (i + 0.5) * step for i in range(n)]
        domain = [pmin, pmax]
        half = step / 2
    else:
        centers = [pmin] * n
        pad = max(abs(pmin) * 0.02, 0.05)
        domain = [pmin - pad, pmin + pad]
        half = pad
    stats = snap.get("stats") or {}
    winner = stats.get("mass_below_close")
    winner_txt = "—" if winner is None else f"{float(winner):.1%}"
    rows = []
    for i, mass in enumerate(hist):
        px = centers[i]
        rows.append({
            "x0": px - half,
            "x1": px + half,
            "px": px,
            "mass": float(mass),
            "side": "获利" if px <= close else "套牢",
        })
    df = pd.DataFrame(rows)
    x_scale = alt.Scale(domain=domain, nice=False, zero=False)
    bars = alt.Chart(df).mark_bar().encode(
        x=alt.X("x0:Q", scale=x_scale, title="价格"),
        x2="x1:Q",
        y=alt.Y("mass:Q", title="筹码", axis=alt.Axis(format="%")),
        color=alt.Color(
            "side:N",
            scale=alt.Scale(domain=["获利", "套牢"], range=["#f87171", "#64748b"]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=[
            alt.Tooltip("px:Q", title="价格", format=".2f"),
            alt.Tooltip("mass:Q", title="质量", format=".1%"),
            alt.Tooltip("side:N", title=""),
        ],
    )
    rule = alt.Chart(pd.DataFrame({"px": [close]})).mark_rule(
        color="#e6b450", strokeWidth=2,
    ).encode(x=alt.X("px:Q", scale=x_scale))
    align = "right" if close >= (domain[0] + domain[1]) / 2 else "left"
    label = alt.Chart(pd.DataFrame({
        "px": [close],
        "mass": [float(df["mass"].max()) if len(df) else 0.0],
        "text": [f"收盘 {close:.2f} · 获利 {winner_txt}"],
    })).mark_text(
        align=align, dx=-6 if align == "right" else 6, baseline="bottom",
        color="#e6edf3", fontSize=12, fontWeight=600, font="PingFang SC",
    ).encode(x=alt.X("px:Q", scale=x_scale), y="mass:Q", text="text:N")
    layers = bars + rule + label
    peaks = stats.get("peaks") or []
    if peaks:
        peak_rows = []
        for peak in peaks:
            i = min(range(n), key=lambda k: abs(centers[k] - float(peak["px"])))
            peak_rows.append({
                "px": centers[i],
                "mass": float(hist[i]),
                "text": f"{float(peak['px']):.2f}",
            })
        peak_df = pd.DataFrame(peak_rows)
        layers = layers + alt.Chart(peak_df).mark_point(
            shape="triangle-down", size=70, color="#e6b450", filled=True,
        ).encode(
            x=alt.X("px:Q", scale=x_scale), y="mass:Q",
            tooltip=[alt.Tooltip("text:N", title="峰")],
        )
    return layers.properties(height=220, width=width, title=title or None)


def _render_last_chip(snap: dict, title: str, width: int = 460) -> None:
    snap_stats = snap.get("stats") or {}
    peaks = snap_stats.get("peaks") or []
    winner = snap_stats.get("mass_below_close")
    winner_txt = "—" if winner is None else f"{float(winner):.1%}"
    st.altair_chart(_alt_dark(_last_chip_chart(snap, title, width)), width="stretch")
    peak_txt = "、".join(
        f"{float(p['px']):.2f}（{float(p['vs_close']):+.1%}，{float(p['mass']):.0%}）"
        for p in peaks
    ) or "没有分开的峰"
    day = snap.get("day") or "窗口最后一天"
    st.caption(f"{day} · 获利 {winner_txt} · 峰 {peak_txt}")


def _overlay_pair(
    eligible: dict,
    ref: str,
    pick: str,
    names: dict,
    key: str,
    slice_n: int,
    title: str,
    y_title: str,
    do_norm: bool,
) -> None:
    """Two-series overlay. *do_norm* min-maxes each name onto [0,1] (price shape).
    Leave it off when the unit is already comparable (换手 %)."""
    chart_rows = []
    for code in (ref, pick):
        raw = (eligible.get(code) or {}).get(key)
        if not raw:
            continue
        tail = raw[-slice_n:]
        vals = [0.0 if x is None or x != x else float(x) for x in tail]
        series = normalize(vals) if do_norm else vals
        label = names.get(code) or code
        chart_rows.extend({"t": i, "value": float(v), "series": label} for i, v in enumerate(series))
    if not chart_rows:
        st.caption(f"{title}：无序列")
        return
    line = alt.Chart(pd.DataFrame(chart_rows)).mark_line(strokeWidth=2).encode(
        x=alt.X("t:Q", title="窗口内第 N 个交易日"),
        y=alt.Y("value:Q", title=y_title),
        color=alt.Color("series:N", scale=_PAIR_SCALE, legend=alt.Legend(title=None, orient="top")),
        tooltip=["series", "t", "value"],
    ).properties(height=200, width=_CHART_W, title=title)
    st.altair_chart(_alt_dark(line), width="stretch")

def _similar_logger() -> logging.Logger:
    log = logging.getLogger("whaletrail.similar")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    log.propagate = False
    log_name = "similar-scan.stage.log" if os.environ.get("WT_ENV") == "stage" else "similar-scan.log"
    path = DATA_ROOT / "logs" / log_name
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(fh)
    return log


@st.cache_data(ttl=3600, show_spinner=False)
def _ref_deep_history(code: str, start: str) -> dict[str, dict[str, list]]:
    """The reference name's own bars from *start*, one symbol only.

    A template window can reach further back than the universe fetch; pulling
    deep history for just the template keeps that cheap.
    """
    try:
        repo = Repository(DB_PATH)
        bars = repo.daily_bars(start=start, codes=[code])
        repo.close()
        return bars
    except Exception:
        return {}


def _feat_cell(feat: dict, key: str, digits: int = 2, scale: float = 1.0):
    val = feat.get(key)
    if val is None:
        return None
    return round(float(val) * scale, digits)


def _template_choices() -> tuple[list[str], dict[str, str]]:
    labels: list[str] = []
    by_label: dict[str, str] = {}
    for key in ("yuandong", "chaosheng"):
        spec = DEMOS[key]
        label = f"{spec['title']} （{spec['start']} 至 {spec['end']}）"
        labels.append(label)
        by_label[label] = key
    labels.append("自选")
    return labels, by_label


def _coerce_date(cur: Any, default: date) -> date:
    if isinstance(cur, datetime):
        return cur.date()
    if isinstance(cur, date):
        return cur
    if isinstance(cur, str):
        try:
            return date.fromisoformat(cur.strip()[:10].replace("/", "-").replace(".", "-"))
        except ValueError:
            return default
    return default


def page_similar() -> None:
    _page_header("相似选股", "选一个模板，看现在哪些股票走势接近。")
    labels, by_label = _template_choices()
    if "similar_book_demo" not in st.session_state:
        st.session_state["similar_book_demo"] = "自选"
    book = st.selectbox("模板", labels, key="similar_book_demo")
    demo_key = by_label.get(book)
    locked = demo_key is not None
    bars, names, _source_label, dropped_session = _similarity_universe()
    all_codes = sorted(bars) if bars else []

    def _label(code: str) -> str:
        name = names.get(code) or ""
        return f"{name} ({code})" if name else code

    if locked and st.session_state.get("_similar_book_applied") != demo_key:
        spec = DEMOS[demo_key]
        st.session_state["similar_scan_start"] = date.fromisoformat(spec["start"])
        st.session_state["similar_scan_end"] = date.fromisoformat(spec["end"])
        if spec["symbol"] in all_codes:
            st.session_state["similar_ref_pick"] = spec["symbol"]
        st.session_state["_similar_book_applied"] = demo_key
    elif not locked:
        st.session_state["_similar_book_applied"] = None
    for _date_key, _date_default in (
        ("similar_scan_start", _cn_today() - timedelta(days=126)),
        ("similar_scan_end", _cn_today()),
    ):
        fixed = _coerce_date(st.session_state.get(_date_key), _date_default)
        if st.session_state.get(_date_key) != fixed:
            st.session_state[_date_key] = fixed
    pick_options = all_codes or ["—"]
    if st.session_state.get("similar_ref_pick") not in pick_options:
        fallback = DEMOS["yuandong"]["symbol"]
        st.session_state["similar_ref_pick"] = fallback if fallback in pick_options else pick_options[0]

    with st.container(border=True):
        left, right = st.columns([1.2, 1.25], gap="large")
        with left:
            ref_pick = st.selectbox(
                "代码或名称",
                pick_options,
                format_func=_label,
                key="similar_ref_pick",
                disabled=locked or not all_codes,
                filter_mode="fuzzy",
                placeholder="输入代码或名称",
            )
            d1, d2 = st.columns(2)
            with d1:
                mark_start = st.date_input(
                    "起始日期", key="similar_scan_start", disabled=locked,
                )
            with d2:
                mark_end = st.date_input(
                    "结束日期", key="similar_scan_end", disabled=locked,
                )
            clicked = st.button(
                "查询相似股票", type="primary", key="similar_scan_btn", width="stretch",
            )
        with right:
            st.markdown(
                '<div class="opt-rule">自定义选项<span>（一般不用改）</span></div>',
                unsafe_allow_html=True,
            )
            preset = st.radio(
                "打分偏好",
                tuple(WEIGHT_PRESETS.keys()),
                index=list(WEIGHT_PRESETS.keys()).index("均衡"),
                horizontal=True,
                key="similar_screen_preset",
            )
            n1, n2, n3 = st.columns([1, 1, 1])
            with n1:
                recall_n = st.number_input(
                    "召回池", min_value=20, max_value=500, value=DEFAULT_SCREEN_RECALL,
                    step=50, key="similar_screen_recall",
                )
            with n2:
                top_n = st.number_input("显示", min_value=5, max_value=80, value=40, step=5, key="similar_top_n")
            with n3:
                exclude_st = st.checkbox("排除 ST", value=True, key="similar_exclude_st")
            preview = st.radio(
                "模板预览", ("日 K", "筹码", "K + 筹码"),
                horizontal=True, key="similar_tpl_view",
            )
    weights = WEIGHT_PRESETS[preset]
    if not bars:
        st.info("暂无行情。")
        return
    if mark_end < mark_start:
        st.warning("结束日期不能早于起始日期。")
        return
    if locked:
        ref = DEMOS[demo_key]["symbol"]
    else:
        ref = ref_pick
        if ref not in bars:
            st.caption("没有这只。")
            return

    recent_start = (_cn_today() - timedelta(days=420)).isoformat()
    scan_input = bars
    deep: dict = {}
    if ref not in scan_input or mark_start.isoformat() < recent_start:
        deep = _ref_deep_history(ref, mark_start.isoformat())
        if deep.get(ref):
            scan_input = {**bars, ref: deep[ref]}
    built = build_scan_pool(scan_input, ref, mark_start.isoformat(), mark_end.isoformat())
    if built is None:
        st.caption("这段不足 10 个交易日。")
        return
    template_bars, eligible, slice_n = built
    cand_end = max(
        (str((b.get("trade_date") or [""])[-1])[:10] for b in bars.values()), default=""
    )

    if preview in ("日 K", "K + 筹码"):
        _render_kline_panel(_ohlc_from_bars(template_bars, slice_n), _label(ref))
    if preview in ("筹码", "K + 筹码"):
        snap_ref = _chip_snapshot(template_bars, slice_n)
        if snap_ref is not None:
            _render_last_chip(snap_ref, f"末日筹码 · {_label(ref)}", width=_CHART_W)
        else:
            st.caption("这段没有换手，筹码图先不画。")
    st.caption(f"模板 {slice_n} 根 · {mark_start} 至 {mark_end}")

    sig = (
        ref, mark_start.isoformat(), mark_end.isoformat(),
        int(recall_n), preset, bool(exclude_st),
    )
    if clicked:
        t0 = time.perf_counter()
        with st.spinner("正在查询…"):
            ranked, info = screen_similar(
                eligible[ref],
                eligible,
                recall_n=int(recall_n),
                weights=weights,
                exclude_st=exclude_st,
            )
        elapsed = time.perf_counter() - t0
        ranked = [m for m in ranked if m.code != ref]
        used_w = info["weights"]
        sidike = next(({"rank": i, "recall": m.recall_rank, "corr": m.close_corr, "score": m.score}
                       for i, m in enumerate(ranked, start=1) if m.code == "sz.300806"), None)
        payload = {
            "ref": ref,
            "ref_name": names.get(ref),
            "start": mark_start.isoformat(),
            "end": mark_end.isoformat(),
            "cand_end": cand_end,
            "dropped_session": dropped_session,
            "bars": slice_n,
            "eligible": len(eligible),
            "recall_n": int(recall_n),
            "top_n": int(top_n),
            "preset": preset,
            "weights": used_w,
            "exclude_st": bool(exclude_st),
            "elapsed_s": round(elapsed, 2),
            "template_stage": info["template_stage"],
            "template_features": info["template_features"],
            "top20": [m.code for m in ranked[:20]],
            "sz.300806": sidike,
        }
        keep = {ref} | {m.code for m in ranked}
        _similar_logger().info(json.dumps(payload, ensure_ascii=False))
        st.session_state["similar_scan"] = {
            "sig": sig, "ranked": ranked, "used_w": used_w, "slice_n": slice_n,
            "elapsed": elapsed, "payload": payload,
            "ref": ref,
            "bars": {c: eligible[c] for c in keep if c in eligible},
        }
    state = st.session_state.get("similar_scan")
    if not state:
        return
    ranked = state["ranked"]
    slice_n = state["slice_n"]
    view_bars = state.get("bars") or eligible
    view_ref = state.get("ref") or ref
    pl = state.get("payload") or {}
    stale = state.get("sig") != sig
    if pl:
        msg = (
            f"{pl.get('ref_name') or pl.get('ref')} {pl.get('start')} 至 {pl.get('end')}"
            f" · 召回 {len(ranked)} 只 · {pl.get('preset') or ''} · {pl.get('elapsed_s')} 秒"
        )
        if stale:
            st.warning("条件已改，这是上次的结果。再点查询。")
        else:
            st.caption(msg)
    if not ranked:
        st.caption("没有可对照的股票。")
        return

    shown = ranked[: int(top_n)]
    asof = (pl or {}).get("cand_end") or ""
    industry, index_by_day = _market_frame()
    sectors = sector_moves(bars, industry, asof) if asof else {}
    index_day = index_by_day.get(asof, {})
    contexts: dict[str, dict] = {}

    def _ctx(code: str) -> dict:
        cached = contexts.get(code)
        if cached is None:
            cached = name_context(
                code, bars.get(code) or {}, industry, sectors, index_day, asof,
            )
            contexts[code] = cached
        return cached

    rows = []
    for i, m in enumerate(shown, start=1):
        delta = m.delta
        feat = m.features or {}
        under = feat.get("all_under")
        ctx = _ctx(m.code)
        rows.append({
            "排序": i,
            "召回": m.recall_rank,
            "代码": m.code,
            "名称": names.get(m.code, ""),
            "属性": ctx.get("board") or "—",
            "行业": ctx.get("industry_cut") or "—",
            "流通市值": cap_text(ctx.get("float_yi")),
            "体量": ctx.get("cap_band") or "—",
            "对上证": ctx.get("vs_index") or "—",
            "对板块": ctx.get("vs_sector") or "—",
            "分数": round(m.score, 3),
            "末峰": _feat_cell(feat, "end_n_peaks", 0),
            "峰日峰": _feat_cell(feat, "peak_n_peaks", 0),
            "谷日峰": _feat_cell(feat, "trough_n_peaks", 0),
            "偏度": _feat_cell(feat, "end_skew", 2),
            "离散": _feat_cell(feat, "end_std", 1, 100),
            "前峰量比": _feat_cell(feat, "vol_climax", 2),
            "箱高": _feat_cell(feat, "box_width", 1, 100),
            "震荡": _feat_cell(feat, "chop", 1),
            "四线": None if under is None else ("全在价格下" if under == 1.0 else "未全在下"),
            "Δ": None if delta is None else (f"+{delta}" if delta > 0 else str(delta)),
        })
    df = pd.DataFrame(rows)
    pick_codes = [m.code for m in shown]
    pick = pick_codes[0]
    try:
        event = st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="similar_tbl",
        )
        sel_rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
        if sel_rows:
            idx = int(sel_rows[0])
            if 0 <= idx < len(pick_codes):
                pick = pick_codes[idx]
    except TypeError:
        styled = _style_base(df.style.hide(axis="index"))
        _show(styled, width="stretch")
        pick = st.selectbox("对照个股", pick_codes, format_func=_label)
    st.caption("点一行看对照。分数越小越接近模板。流通市值、对上证、对板块不参与排序。")

    m_pick = next((m for m in ranked if m.code == pick), shown[0])
    pick = m_pick.code
    feat = m_pick.features or {}

    def _fnum(key: str, digits: int = 2) -> str:
        val = feat.get(key)
        return "—" if val is None else f"{float(val):.{digits}f}"

    _sec(f"1v1 对照 · {_label(view_ref)}  vs  {_label(pick)}")
    _card_row(
        [
            {"label": "分数", "value": f"{m_pick.score:.3f}", "sub": "越小越接近模板"},
            {"label": "K DTW", "value": f"{m_pick.d_kline:.2f}", "sub": "召回距离"},
            {"label": "前峰量比", "value": _fnum("vol_climax"), "sub": "末日量 / 此前放量峰"},
            {"label": "末峰", "value": _fnum("end_n_peaks", 0), "sub": "末日筹码峰数"},
            {
                "label": "Δ",
                "value": "—" if m_pick.delta is None else (f"+{m_pick.delta}" if m_pick.delta > 0 else str(m_pick.delta)),
                "sub": "召回名次变化",
                "accent": "#4ade80" if (m_pick.delta or 0) > 0 else ("#f87171" if (m_pick.delta or 0) < 0 else "#e6b450"),
            },
        ],
        cols=5,
    )
    _render_kline_panel(
        _ohlc_from_bars(view_bars.get(view_ref, {}), slice_n),
        f"模板 · {_label(view_ref)} · {pl.get('start') or '?'}→{pl.get('end') or '?'}",
    )
    _render_kline_panel(
        _ohlc_from_bars(view_bars.get(pick, {}), slice_n),
        f"对照 · {_label(pick)} · 最近 {slice_n} 根（到 {pl.get('cand_end') or '—'}）",
    )
    st.caption("横轴是窗口里第几个交易日，两边的日期不对齐。")
    _overlay_pair(
        view_bars, view_ref, pick, names, "close", slice_n,
        "收盘形状", "0–1", True,
    )
    if (view_bars.get(view_ref) or {}).get("turn") and (view_bars.get(pick) or {}).get("turn"):
        _overlay_pair(
            view_bars, view_ref, pick, names, "turn", slice_n,
            "换手", "换手 %", False,
        )
    else:
        _overlay_pair(
            view_bars, view_ref, pick, names, "volume", slice_n,
            "成交量", "0–1", True,
        )
    _sec("末日筹码")
    h_ref = _chip_snapshot(view_bars.get(view_ref, {}), slice_n)
    h_pick = _chip_snapshot(view_bars.get(pick, {}), slice_n)
    chip_l, chip_r = st.columns(2)
    with chip_l:
        if h_ref is not None:
            _render_last_chip(h_ref, f"模板 · {_label(view_ref)}")
        else:
            st.caption(f"模板 {_label(view_ref)} 无换手，没有末日筹码。")
    with chip_r:
        if h_pick is not None:
            _render_last_chip(h_pick, f"对照 · {_label(pick)}")
        else:
            st.caption(f"对照 {_label(pick)} 无换手，没有末日筹码。")
    st.caption("红柱是收盘价及以下的筹码（获利），金色线是收盘价。")

def _kol_handle(acc: Any) -> str:
    return str(acc or "").lstrip("@").strip()

def page_kol() -> None:
    _page_header("KOL 评测", "A股荐股推文 vs 事后对照 · 冻结 18 账号 · 只读本地结果")
    _note("A 股荐股推文与事后对照。不调 live X API。无存储对照则空表，不编数字。")
    _sec("冻结名册 · 18")
    st.caption(" · ".join(f"@{a}" for a in KOL_ROSTER))
    _sec("荐股 / 事后对照")
    cols = ["账号", "日期", "标的", "荐股", "对照日", "事后结果"]
    rows = []
    for it in load_kol_eval_rows():
        acc = it.get("account") or it.get("账号") or it.get("handle") or it.get("user")
        if _kol_handle(acc).lower() not in KOL_ROSTER_SET:
            continue
        rows.append({"账号": f"@{_kol_handle(acc)}", "日期": it.get("date") or it.get("日期") or it.get("tweet_date") or it.get("created_at"),
                     "标的": it.get("symbol") or it.get("ticker") or it.get("code") or it.get("标的"),
                     "荐股": it.get("pick") or it.get("荐股") or it.get("summary") or it.get("text"),
                     "对照日": it.get("eval_date") or it.get("对照日") or it.get("as_of"),
                     "事后结果": it.get("outcome") or it.get("事后结果") or it.get("result") or it.get("return")})
    if not rows:
        _show(pd.DataFrame(columns=cols)); st.info("没有已存储的评测数据。空表如上，不编对照。"); return
    _show(_style_base(pd.DataFrame(rows, columns=cols).style.hide(axis="index")).format(na_rep="—"), width="stretch")
    st.caption(f"{len(rows)} 行来自 results/ 或 SQLite 已存评测，未请求 X。")

def page_genzhuang() -> None:
    _page_header("跟庄复盘", "watchlist 标签 观察 / 接近 / 触发 · 已收盘日 K · 不是当日阴")
    _note("跟庄只表示现有 watchlist 上的三个标签。日线已收盘 K（baostock），不用 tvscreener 快照冒充完成 bar，不编 OHLC，不与 KOL 混页。黄金矿股留在本页。")
    rows = load_completed_watchlist_bars()
    if not rows:
        st.info("没有 A 股 watchlist 或无法读取。不编名单。"); return
    df = pd.DataFrame(rows)
    has_bar = df["交易日"].notna().any() if "交易日" in df.columns else False
    has_label = df["标签"].notna().any() if "标签" in df.columns else False
    if not has_bar and not has_label:
        st.info("无已收盘日 K、无已存标签。空表如下，不编 OHLC，不新建 yin-right.json。")
    view_cols = [c for c in ("名称", "代码", "标签", "交易日", "开", "高", "低", "收", "量") if c in df.columns]
    fmt = {k: v for k, v in {"开": "{:,.2f}", "高": "{:,.2f}", "低": "{:,.2f}", "收": "{:,.2f}", "量": "{:,.0f}"}.items() if k in df.columns}
    styled = _style_base(df[view_cols].style.hide(axis="index")).format(fmt, na_rep="—")
    if "标签" in df.columns:
        styled = styled.map(_label_style, subset=["标签"])
    _show(styled, width="stretch")
    st.caption("标签仅 观察 / 接近 / 触发；缺失为 —。当日 bar 已排除。")

@_frag(run_every=60)
def _health_strip() -> None:
    with st.expander("健康 / 新鲜度", expanded=False):
        for r in _service_checks() + _launchd_rows():
            tone = "ok" if r["状态"] == "ok" else ("warn" if r["状态"] == "warn" else "err")
            label = "运行中" if tone == "ok" else ("异常" if tone == "warn" else "离线")
            st.markdown(f'<div class="svc"><span class="svc-name">{r["服务"]}</span><span>{_pill(str(r["端口"]), "mut")} {_pill(label, tone)}</span></div>', unsafe_allow_html=True)
        fresh = []
        live = load_live_state(); snap = (live or {}).get("last_snapshot") or {}
        lts = max((v.get("ts", "") for v in snap.values()), default="")
        fresh.append({"数据": "live 扫描", "时间": lts or "无", "距今": _staleness(lts) if lts else "—"})
        ashare = load_ashare_paper(); fresh.append({"数据": "A股 paper", "时间": "有" if ashare else "无", "距今": "—"})
        newest_bt = max(RESULTS_DIR.glob("backtest_*.json"), key=lambda p: p.stat().st_mtime, default=None)
        if newest_bt:
            ts = datetime.fromtimestamp(newest_bt.stat().st_mtime)
            fresh.append({"数据": "最新回测", "时间": ts.strftime("%Y-%m-%d %H:%M"), "距今": _staleness(ts.isoformat())})
        else:
            fresh.append({"数据": "最新回测", "时间": "无", "距今": "—"})
        qts = latest_quote_ts(); fresh.append({"数据": "tvscreener 快照", "时间": qts or "无", "距今": _staleness(qts) if qts else "—"})
        _show(_style_base(pd.DataFrame(fresh).style.hide(axis="index")).map(lambda v: f"color:{_staleness_color(str(v))};font-weight:600", subset=["距今"]), width="stretch")
        runs = _runs_count(); st.caption(f"SQLite runs: {runs}" if runs >= 0 else "SQLite 不可用")

tb1, tb2 = st.columns([7, 1])
with tb1:
    st.markdown('<div class="topbar"><div class="brand-title">WhaleTrail</div><div class="brand-sub">READ-ONLY</div></div>', unsafe_allow_html=True)
with tb2:
    if st.button("刷新"):
        st.cache_data.clear(); st.rerun()
# Deep links (same host): /?page=gold|ashare|similar|kol|genzhuang
# Bare / is 相似选股. No tab bar.
PAGE_DEFS: list[tuple[str, str, Any]] = [
    ("gold", "黄金 paper", page_gold_paper),
    ("ashare", "A股 paper", page_ashare_paper),
    ("similar", "相似选股", page_similar),
    ("kol", "KOL 评测", page_kol),
    ("genzhuang", "跟庄复盘", page_genzhuang),
]
PAGE_FN = {k: fn for k, _, fn in PAGE_DEFS}

def _page_key() -> str:
    raw = st.query_params.get("page", "similar")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else "similar"
    key = str(raw or "similar").strip().lower()
    return key if key in PAGE_FN else "similar"

PAGE_FN[_page_key()]()
_health_strip()
