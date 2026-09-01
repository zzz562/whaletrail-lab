"""Yahoo Finance + Parquet cache — gold / US daily data."""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import yfinance as yf

from whaletrail.data.base import DataSource
from whaletrail.data.cache import ParquetCache
from whaletrail.data.symbols import parse_symbol

logger = logging.getLogger(__name__)

_YF_COLUMN_MAP = {"Open": "open", "High": "high", "Low": "low",
                  "Close": "close", "Volume": "volume"}


class YFinanceSource(DataSource):
    """Daily OHLCV, cached in Parquet. Cache-hit → instant. Cache-miss → fetch + save."""

    _RAW_SYMBOLS = frozenset({"GC=F", "SI=F", "HG=F"})

    def __init__(self, cache_dir: str | None = None):
        self._cache = ParquetCache(cache_dir)

    def get_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        parsed = parse_symbol(symbol)
        ticker = parsed.ticker
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)

        # Cache coverage check: a partial slice is NOT a valid hit, otherwise
        # backtests silently run on truncated data. Missing head/tail ranges
        # are fetched and merged; internal gaps are treated as market holidays.
        cached = _drop_bad_bars(self._cache.get(ticker))
        if cached is not None and not cached.empty:
            first, last = cached.index.min(), cached.index.max()
            if first <= start_ts and last >= end_ts:
                logger.debug("cache hit %s (%d rows)", ticker, len(cached))
                return cached[(cached.index >= start_ts) & (cached.index <= end_ts)]

            if first > start_ts:
                self._fetch_and_cache(ticker, start, (first - pd.Timedelta(days=1)).date())
            if last < end_ts:
                self._fetch_and_cache(ticker, (last + pd.Timedelta(days=1)).date(), end)

            cached = _drop_bad_bars(self._cache.get(ticker))
            if cached is not None and not cached.empty:
                return cached[(cached.index >= start_ts) & (cached.index <= end_ts)]
            return _empty_df()

        # Cold cache → fetch full range.
        df = self._fetch_and_cache(ticker, start, end)
        return df if df is not None and not df.empty else _empty_df()

    def _fetch_and_cache(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch one range from yfinance, normalise it, and merge into cache."""
        auto_adjust = ticker not in self._RAW_SYMBOLS
        logger.info("yfinance fetch %s %s→%s", ticker, start.isoformat(), end.isoformat())

        try:
            df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                             auto_adjust=auto_adjust, progress=False)
        except Exception:
            logger.exception("yfinance failed %s", ticker)
            return _empty_df()
        if df is None or df.empty:
            return _empty_df()

        # Normalise
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        rename = {k: v for k, v in _YF_COLUMN_MAP.items() if k in df.columns}
        df = df.rename(columns=rename)
        df = df[[c for c in _YF_COLUMN_MAP.values() if c in df.columns]]
        if isinstance(df.index, pd.MultiIndex):
            df.index = df.index.get_level_values("Date")
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        if "volume" in df.columns:
            df["volume"] = df["volume"].fillna(0).astype("int64")

        df = _drop_bad_bars(df)
        if df is None or df.empty:
            return _empty_df()
        self._cache.put(ticker, df)
        return df


def _drop_bad_bars(df: pd.DataFrame | None) -> pd.DataFrame:
    """Drop rows with missing or non-positive OHLC (partial yfinance sessions)."""
    if df is None or df.empty:
        return _empty_df() if df is None else df
    ohlc = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if not ohlc:
        return df
    nums = df[ohlc].apply(pd.to_numeric, errors="coerce")
    ok = nums.notna().all(axis=1) & (nums > 0).all(axis=1)
    if ok.all():
        return df
    dropped = int((~ok).sum())
    logger.warning("dropping %d incomplete bar(s)", dropped)
    return df.loc[ok]


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
