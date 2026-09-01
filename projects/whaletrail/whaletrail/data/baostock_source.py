"""Baostock (证券宝) A-share daily-bar source.

Free, tokenless A-share daily OHLCV (SH/SZ/BJ, 1990→present).  This fills the
whole-market historical-bar gap that tvscreener cannot: the TradingView
scanner serves current snapshots only, so the DTW chart-similarity scan needs
this source to get a full universe of trailing close series.

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
_FIELDS = "date,code,open,high,low,close,volume,amount"
_ADJUST_FLAG = "3"


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


def _to_ohlcv(df: pd.DataFrame, keep_amount: bool = False) -> pd.DataFrame:
    """Normalise a baostock result frame to OHLCV(+amount) with a date index."""
    out = pd.DataFrame(
        {
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64"),
        }
    )
    if keep_amount:
        out["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    out.index = pd.to_datetime(df["date"].to_numpy())
    out.index.name = "date"
    out = out.dropna(subset=["open", "high", "low", "close"]).sort_index()
    return out


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
        self._ensure_login()
        bs = self._import()
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_stock_basic failed: {rs.error_code} {rs.error_msg}")
        df = _result_frame(rs)
        if df.empty:
            return []
        if "type" in df.columns:
            df = df[df["type"] == "1"]
        if "status" in df.columns:
            df = df[df["status"] == "1"]
        return list(zip(df["code"].astype(str).tolist(), df["code_name"].astype(str).tolist()))

    def fetch_daily(self, code: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars for one baostock ``code`` as an OHLCV DataFrame."""
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
        return _to_ohlcv(df, keep_amount=True)
