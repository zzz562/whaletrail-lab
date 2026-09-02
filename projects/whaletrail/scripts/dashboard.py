#!/usr/bin/env python3
"""WhaleTrail Dashboard — read-only four-tab monitor.

Tabs: Paper / 相似选股 / KOL 评测 / 跟庄复盘.
"""
from __future__ import annotations

import json, subprocess, sys, urllib.request
from datetime import date, datetime
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
from whaletrail.similarity import normalize, rank_similar
from whaletrail.storage.repository import Repository

st.set_page_config(page_title="WhaleTrail", layout="wide")
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "whaletrail.db"
DATA_CACHE_DIR = ROOT / "data_cache"
WATCHLIST_PATH = ROOT / "config" / "watchlist.yaml"
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
html,body,.stApp { background:var(--wt-bg); color:var(--wt-text); font-family:'Inter',sans-serif; }
.block-container { padding:1rem 1.6rem 1.8rem; max-width:1440px; }
#MainMenu, footer { visibility:hidden; height:0; }
header[data-testid="stHeader"] { background:transparent; }
.stAppDeployButton, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { display:none !important; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,var(--wt-surface2),#0b101a); border-right:1px solid var(--wt-border); }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] { display:none !important; }
[data-testid="stRadioOption"] { background:transparent !important; border:1px solid transparent !important; border-radius:8px !important; padding:6px 10px !important; }
[data-testid="stRadioOption"] p { color:var(--wt-muted) !important; font-size:.9rem !important; margin:0 !important; }
[data-testid="stRadioOption"][data-selected="true"] p { color:var(--wt-gold) !important; font-weight:600 !important; }
[data-testid="stRadioOption"] > div > div > div:first-child { display:none !important; }
.kicker { font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--wt-gold); font-weight:700; }
.page-title { font-size:1.35rem; font-weight:800; letter-spacing:-.02em; margin:0 0 2px; }
.page-sub { color:var(--wt-muted); font-size:.82rem; margin-bottom:12px; }
.m-card { background:linear-gradient(180deg,var(--wt-surface),#0f1622); border:1px solid var(--wt-border); border-radius:10px; padding:11px 14px 10px; position:relative; overflow:hidden; height:100%; }
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
.sec-label { font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--wt-gold); font-weight:700; margin:18px 0 8px; }
.brand { padding:4px 0 14px; border-bottom:1px solid var(--wt-border); margin-bottom:12px; }
.brand-title { font-weight:800; font-size:1.05rem; }
.brand-sub { font-size:10px; letter-spacing:.18em; color:var(--wt-gold); font-weight:700; margin-top:2px; }
[data-testid="stDataFrame"] { border:1px solid var(--wt-border); border-radius:10px; overflow:hidden; }
[data-testid="stVegaLiteChart"] { border:1px solid var(--wt-border); border-radius:10px; padding:6px; background:var(--wt-surface); }
.stButton > button { background:var(--wt-surface); border:1px solid var(--wt-border); color:var(--wt-text); border-radius:8px; font-weight:600; }
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

@st.cache_data(ttl=3600, show_spinner=False)
def load_cached_close(symbol: str) -> Optional[pd.DataFrame]:
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

def _scaled_close_series(symbol: str, start: str, end: str, initial_cash: float, name: str) -> Optional[pd.Series]:
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
    for name in files:
        try:
            d = load_backtest(name)
        except Exception:
            continue
        if str(d.get("strategy", "")).lower().startswith("gold_sma") and str(d.get("symbol", "")).upper() == "GLD":
            return name
    for name in files:
        low = name.lower()
        if "gold_sma" in low and "gld" in low:
            return name
    return None

def _page_header(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="kicker">WHALETRAIL</div><div class="page-title">{title}</div><div class="page-sub">{sub}</div>', unsafe_allow_html=True)

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
            .configure_legend(labelColor="#8b98a9", titleColor="#8b98a9"))

def _style_base(s: Any) -> Any:
    return (s.set_properties(**{"color": "#e6edf3", "font-family": "var(--wt-mono)", "font-size": "12.5px", "padding": "6px 12px", "border-bottom": "1px solid #182231", "text-align": "right"})
            .set_table_styles([{"selector": "th", "props": [("background-color", "#0d1320"), ("color", "#8b98a9"), ("font-size", "10.5px"), ("text-transform", "uppercase"), ("letter-spacing", ".1em"), ("padding", "8px 12px"), ("border-bottom", "1px solid #1e2a3a")]}]))

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
    _sec("黄金账 · GLD gold_sma vs 买入持有 vs SPY")
    _note("GLD / GC=F 仅作监控/对照，不是银行牌价，不是纸黄金账。本页不含银行纸黄金。gold_sma 弱于买入持有，价值在压回撤。")
    files = list_backtest_files()
    gold_name = _pick_gold_sma(files)
    if not gold_name:
        st.info("没有 GLD gold_sma 回测结果。缺则空。运行 scripts/run-backtest.py gold_sma GLD。")
        return
    data = load_backtest(gold_name)
    enriched, metrics, initial_cash = _backtest_metrics(data)
    bh = _benchmark_series(data, initial_cash)
    spy = None
    start, end = data.get("start"), data.get("end")
    if start and end:
        spy = _scaled_close_series("SPY", start, end, initial_cash, "SPY")
    ret = metrics.get("total_return"); dd = metrics.get("max_drawdown")
    bh_ret = _series_return(bh); spy_ret = _series_return(spy); bh_dd = _series_drawdown(bh)
    _card_row([
        {"label": "gold_sma", "value": _fmt_pct(ret), "delta": f"最大回撤 {dd:.2f}%" if dd is not None else "—", "delta_color": _num_color(ret or 0), "sub": f"{data.get('start','?')} → {data.get('end','?')}", "accent": "#e6b450"},
        {"label": "买入持有 GLD", "value": _fmt_pct(bh_ret), "delta": f"最大回撤 {bh_dd:.2f}%" if bh_dd is not None else "本地缓存缺失则空", "delta_color": _num_color(bh_ret or 0) if bh_ret is not None else "#8b98a9", "sub": "data_cache", "accent": "#38bdf8"},
        {"label": "SPY 对照", "value": _fmt_pct(spy_ret), "delta": "监控/对照 · 不是纸黄金账", "delta_color": _num_color(spy_ret or 0) if spy_ret is not None else "#8b98a9", "sub": "data_cache" if spy is not None else "缓存缺失", "accent": "#38bdf8"},
        {"label": "Sharpe", "value": f"{metrics.get('sharpe_ratio', 0):.2f}" if metrics else "—", "delta": f"交易 {len(enriched)} 次", "delta_color": "#8b98a9", "sub": gold_name, "accent": "#a78bfa"},
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
        eq = df_eq.reset_index()
        value_cols = [c for c in ("gold_sma", "买入持有", "SPY") if c in eq.columns]
        long = eq.melt(id_vars=["date"], value_vars=value_cols, var_name="系列", value_name="权益")
        rng = ["#e6b450", "#38bdf8", "#a78bfa"][: len(value_cols)]
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
    _note("A 股 paper 按日收盘记账。黄金矿股的跟庄标签在「跟庄复盘」，不进黄金账。")
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

def page_paper() -> None:
    _page_header("Paper", "只读监控 · 黄金账 + A股 15:30 paper · 不是下单台")
    _gold_book_section(); _backtest_section()
    _sec("5m / live 扫描 · 观察"); _note("5m 与 live 扫描仅观察，不作进场依据。"); _live_panel(); _ashare_paper_section()

@st.cache_data(ttl=3600, show_spinner=False)
def _similarity_universe() -> tuple[dict[str, list[float]], dict[str, str], str]:
    try:
        repo = Repository(DB_PATH)
        start = (date.today() - pd.Timedelta(days=300)).strftime("%Y-%m-%d")
        closes = repo.daily_closes(start=start); names = repo.universe_names(); repo.close()
        if closes:
            try:
                for item in load_watchlist(WATCHLIST_PATH):
                    if item.market == "china":
                        names.setdefault(to_baostock_code(item.tv_symbol), item.name)
            except Exception:
                pass
            return closes, names, f"全市场 {len(closes)} 只 · baostock daily_kline"
    except Exception:
        pass
    try:
        items = [i for i in load_watchlist(WATCHLIST_PATH) if i.market == "china"]
    except Exception:
        items = []
    closes, names = {}, {}
    for item in items:
        hist = build_daily_history(DB_PATH, item.tv_symbol)
        if hist.empty:
            continue
        closes[item.tv_symbol] = [float(x) for x in hist["close"].tolist()]; names[item.tv_symbol] = item.name
    return closes, names, f"A股 watchlist {len(closes)} 只 · tvscreener 快照积累"

def page_similar() -> None:
    _page_header("相似选股", "DTW 形态观察 · 不是交易账 · 不扩可交易名单")
    _note("观察工具。形态相近不等于可交易，不产出买卖指令。")
    closes, names, source_label = _similarity_universe()
    if not closes:
        st.info("暂无 A 股历史数据。运行 scripts/fetch-baostock-universe.py 或先积累 tvscreener 快照。")
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
    if not st.button("运行相似度扫描", width="stretch"):
        return
    with st.spinner(f"正在扫描 {len(closes)} 只标的…"):
        ranked = rank_similar(closes[ref], closes, window=int(window))
    ranked = [r for r in ranked if r[0] != ref][: top_n]
    if not ranked:
        st.caption("无有效候选"); return
    rows = [{"排名": i, "代码": code, "名称": names.get(code, ""), "DTW 距离": round(dist, 4)} for i, (code, dist) in enumerate(ranked, start=1)]
    _show(_style_base(pd.DataFrame(rows).style.hide(axis="index")), width="stretch")
    _sec("归一化走势叠加")
    chart_rows = []
    for code in [ref] + [r[0] for r in ranked[:5]]:
        series = normalize(closes[code][-int(window):])
        chart_rows.extend({"t": t, "value": float(v), "series": names.get(code) or code} for t, v in enumerate(series))
    line = alt.Chart(pd.DataFrame(chart_rows)).mark_line(strokeWidth=2).encode(
        x=alt.X("t:Q", title="窗口内第 N 个交易日"), y=alt.Y("value:Q", title="归一化收盘 (0–1)"),
        color=alt.Color("series:N", legend=alt.Legend(title=None, orient="top")), tooltip=["series", "t", "value"],
    ).properties(height=300, width=980)
    st.altair_chart(_alt_dark(line), width="stretch")

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

st.sidebar.markdown('<div class="brand"><div class="brand-title">WhaleTrail</div><div class="brand-sub">READ-ONLY</div></div>', unsafe_allow_html=True)
PAGES = {"Paper": page_paper, "相似选股": page_similar, "KOL 评测": page_kol, "跟庄复盘": page_genzhuang}
page = st.sidebar.radio("导航", list(PAGES), label_visibility="collapsed")
if st.sidebar.button("刷新", width="stretch"):
    st.cache_data.clear(); st.rerun()
st.sidebar.caption("只读监控 · 四页")
PAGES[page]()
_health_strip()
