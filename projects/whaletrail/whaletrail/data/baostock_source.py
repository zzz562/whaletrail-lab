"""Baostock (证券宝) A-share daily-bar source.

Free, tokenless A-share daily OHLCV + extras (SH/SZ/BJ, 1990→present).  This
fills the whole-market historical-bar gap that tvscreener cannot: the
TradingView scanner serves current snapshots only, so the DTW chart-similarity
scan needs this source to get a full universe of trailing close series.
Extra daily fields (turn, tradestatus, ST, PE/PB) and cheap snapshots
(stock basic, 申万一级, sz50/hs300/zz500) come from the same login.
baostock has no concept/theme boards.

Unlike yfinance (gold/US, Parquet cache), the bulk path here writes to the
SQLite ``daily_kline`` table because the similarity scan is cross-sectional
("all symbols in a date window"), not per-symbol.  ``get_daily`` exists only
for ``DataSource`` contract compliance.

Baostock is a direct connection to a China-hosted server — do **not** route it
through the Clash proxy (same treatment as the SZSE trading-calendar fetch).
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from whaletrail.data.base import DataSource

logger = logging.getLogger(__name__)

_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# baostock daily fields we persist.  adjustflag "3" = unadjusted (不复权),
# matching ValarmClub's Tushare-unadjusted input; see notes/ for the qfq
# trade-off and how to switch.
# Extra vs the original OHLCV+amount pull: turn / tradestatus / pctChg / isST /
# peTTM / pbMRQ — same query_history_k_data_plus call, no second source.
_FIELDS = (
    "date,code,open,high,low,close,volume,amount,"
    "turn,tradestatus,pctChg,isST,peTTM,pbMRQ"
)
_ADJUST_FLAG = "3"

# Snapshot index-constituent APIs (latest membership, not point-in-time history).
INDEX_IDS = ("sz50", "hs300", "zz500")
_INDEX_QUERY = {
    "sz50": "query_sz50_stocks",
    "hs300": "query_hs300_stocks",
    "zz500": "query_zz500_stocks",
}


def to_baostock_code(symbol: str) -> str:
    """Map a WhaleTrail symbol to a baostock code (``sh.600690``).

    Accepts TradingView (``SSE:600690``), Yahoo (``600690.SS``) and baostock
    (``sh.600690``) forms.  Raises ``ValueError`` for unknown markets.
    """
    s = symbol.strip()
    if s.lower().startswith(("sh.", "sz.", "bj.")):
        return s.lower()
    if ":" in s:
        market, code = s.split(":", 1)
        m = market.upper()
        if m in ("SSE", "SH", "SHSE"):
            return f"sh.{code}"
        if m in ("SZSE", "SZ", "SHE"):
            return f"sz.{code}"
        if m in ("BSE", "BJ"):
            return f"bj.{code}"
        raise ValueError(f"Unknown A-share market {market!r} in {symbol!r}")
    upper = s.upper()
    for suffix, prefix in ((".SS", "sh"), (".SZ", "sz"), (".BJ", "bj")):
        if upper.endswith(suffix):
            return f"{prefix}.{s[: -len(suffix)]}"
    raise ValueError(f"Cannot map {symbol!r} to a baostock code")


def from_baostock_code(code: str) -> str:
    """Map a baostock code (``sh.600690``) to a WhaleTrail tv_symbol."""
    c = code.strip().lower()
    if c.startswith("sh."):
        return f"SSE:{c[3:]}"
    if c.startswith("sz."):
        return f"SZSE:{c[3:]}"
    if c.startswith("bj."):
        return f"BSE:{c[3:]}"
    return code


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    """Coerce a baostock column to float; empty string (停牌 turn 等) → NaN."""
    if col not in df.columns:
        return pd.Series(float("nan"), index=df.index, dtype="float64")
    return pd.to_numeric(df[col].replace("", pd.NA), errors="coerce")


def _to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a baostock daily frame to OHLCV + extras, date index."""
    out = pd.DataFrame(
        {
            "open": _numeric(df, "open"),
            "high": _numeric(df, "high"),
            "low": _numeric(df, "low"),
            "close": _numeric(df, "close"),
            "volume": _numeric(df, "volume").fillna(0).astype("int64"),
            "amount": _numeric(df, "amount"),
            "turn": _numeric(df, "turn"),
            "tradestatus": _numeric(df, "tradestatus"),
            "pct_chg": _numeric(df, "pctChg"),
            "is_st": _numeric(df, "isST"),
            "pe_ttm": _numeric(df, "peTTM"),
            "pb_mrq": _numeric(df, "pbMRQ"),
        }
    )
    out.index = pd.to_datetime(df["date"].to_numpy())
    out.index.name = "date"
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_index()
    return out


def _blank(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _result_frame(rs) -> pd.DataFrame:
    """Collect all rows from a baostock ``ResultData`` (pandas >= 2.0 safe).

    baostock 0.9.x's own ``ResultData.get_data()`` calls ``DataFrame.append``,
    which was removed in pandas 2.0 and raises ``AttributeError``.  We replicate
    its pagination with ``list.extend`` instead.
    """
    rows = list(rs.data)
    if not rows:
        return pd.DataFrame(columns=rs.fields)
    rs.cur_row_num = len(rows)
    while rs.error_code == "0" and rs.next():
        rows.extend(rs.data)
        rs.cur_row_num = len(rs.data)
    return pd.DataFrame(rows, columns=rs.fields)


class BaostockSource(DataSource):
    """Daily OHLCV for A-shares from baostock (tokenless)."""

    def __init__(self) -> None:
        self._bs = None
        self._logged_in = False

    def _import(self):
        if self._bs is None:
            import baostock as bs  # lazy import: baostock is optional

            self._bs = bs
        return self._bs

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        bs = self._import()
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")
        self._logged_in = True

    def login(self) -> None:
        """Establish a baostock session (anonymous, no token)."""
        self._ensure_login()

    def logout(self) -> None:
        if self._logged_in:
            self._bs.logout()
            self._logged_in = False

    # ------------------------------------------------------------------
    #  DataSource contract
    # ------------------------------------------------------------------
    def get_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Daily OHLCV for one symbol (baostock/TV/Yahoo code forms)."""
        self._ensure_login()
        code = to_baostock_code(symbol)
        df = self.fetch_daily(code, start, end)
        cols = [c for c in _OHLCV_COLUMNS if c in df.columns]
        return df[cols] if not df.empty else pd.DataFrame(columns=_OHLCV_COLUMNS)

    # ------------------------------------------------------------------
    #  Universe / bulk
    # ------------------------------------------------------------------
    def list_universe(self) -> list[tuple[str, str]]:
        """Return ``(code, name)`` for currently listed A-share stocks.

        Uses baostock's ``query_stock_basic`` (``type=1`` 股票, ``status=1``
        上市), which carries names and needs no trading-day argument.
        """
        return [(r["code"], r["name"]) for r in self.fetch_stock_basic(listed_only=True)]

    def fetch_stock_basic(self, listed_only: bool = False) -> list[dict]:
        """Return stock rows from ``query_stock_basic`` (``type=1`` 股票).

        Includes delisted names unless *listed_only*.  Fields: ``code``,
        ``name``, ``ipo_date``, ``out_date``, ``stock_type``, ``status``.
        """
        self._ensure_login()
        bs = self._import()
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_stock_basic failed: {rs.error_code} {rs.error_msg}")
        df = _result_frame(rs)
        if df.empty:
            return []
        if "type" in df.columns:
            df = df[df["type"].astype(str) == "1"]
        if listed_only and "status" in df.columns:
            df = df[df["status"].astype(str) == "1"]
        rows: list[dict] = []
        for rec in df.to_dict(orient="records"):
            code = str(rec.get("code", "")).strip()
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": _blank(rec.get("code_name")) or "",
                    "ipo_date": _blank(rec.get("ipoDate")),
                    "out_date": _blank(rec.get("outDate")),
                    "stock_type": _blank(rec.get("type")),
                    "status": _blank(rec.get("status")),
                }
            )
        return rows

    def fetch_industry(self) -> list[dict]:
        """Latest 申万一级 industry map (``query_stock_industry``).

        baostock has no concept/theme boards — only this classification.
        Weekly refresh on the server (Monday).
        """
        self._ensure_login()
        bs = self._import()
        rs = bs.query_stock_industry()
        if rs.error_code != "0":
            raise RuntimeError(
                f"baostock query_stock_industry failed: {rs.error_code} {rs.error_msg}"
            )
        df = _result_frame(rs)
        if df.empty:
            return []
        rows: list[dict] = []
        for rec in df.to_dict(orient="records"):
            code = str(rec.get("code", "")).strip()
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": _blank(rec.get("code_name")) or "",
                    "industry": _blank(rec.get("industry")),
                    "classification": _blank(rec.get("industryClassification")),
                    "update_date": _blank(rec.get("updateDate")),
                }
            )
        return rows

    def fetch_index_constituents(self, index_id: str) -> list[dict]:
        """Latest constituents for ``sz50`` / ``hs300`` / ``zz500``."""
        method = _INDEX_QUERY.get(index_id)
        if method is None:
            raise ValueError(f"Unknown index_id {index_id!r}; expected one of {INDEX_IDS}")
        self._ensure_login()
        bs = self._import()
        fn = getattr(bs, method, None)
        if fn is None:
            raise RuntimeError(f"baostock has no {method}")
        rs = fn()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock {method} failed: {rs.error_code} {rs.error_msg}")
        df = _result_frame(rs)
        if df.empty:
            return []
        rows: list[dict] = []
        for rec in df.to_dict(orient="records"):
            code = str(rec.get("code", "")).strip()
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": _blank(rec.get("code_name")) or "",
                    "update_date": _blank(rec.get("updateDate")),
                }
            )
        return rows

    def fetch_daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars for one baostock ``code`` as an OHLCV+extras DataFrame."""
        self._ensure_login()
        bs = self._import()
        rs = bs.query_history_k_data_plus(
            code,
            _FIELDS,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag=_ADJUST_FLAG,
        )
        if rs.error_code != "0":
            logger.warning("baostock %s failed: %s %s", code, rs.error_code, rs.error_msg)
            return pd.DataFrame()
        df = _result_frame(rs)
        if df.empty:
            return pd.DataFrame()
        return _to_daily(df)
