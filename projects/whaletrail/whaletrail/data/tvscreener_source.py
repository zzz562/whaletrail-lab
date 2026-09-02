"""TradingView Screener snapshot data source.

This module intentionally uses TradingView's scanner HTTP endpoint directly. It
keeps the integration lightweight while matching the data shape exposed by the
`tvscreener` package. The source is designed for hourly/daily watchlist tracking
and paper trading snapshots, not for high-frequency data or full historical
backfills.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Iterable, Mapping, Optional

import pandas as pd
import requests

from whaletrail.data.base import DataSource

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

SCAN_ENDPOINTS = {
    "global": "https://scanner.tradingview.com/global/scan",
    "futures": "https://scanner.tradingview.com/futures/scan",
}

SNAPSHOT_COLUMNS = [
    "name",
    "description",
    "open",
    "high",
    "low",
    "close",
    "change",
    "volume",
    "exchange",
    "Recommend.All",
    "RSI",
    "SMA20",
    "SMA50",
    "SMA200",
]

_STD_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

_FUTURES_EXCHANGE_PREFIXES = (
    "COMEX:",
    "NYMEX:",
    "CME:",
    "CBOT:",
    "ICEUS:",
    "ICEEUR:",
)


@dataclass(frozen=True)
class QuoteSnapshot:
    """A normalised TradingView scanner quote snapshot."""

    symbol: str
    timestamp: datetime
    source: str = "tvscreener"
    endpoint: str = "global"
    name: Optional[str] = None
    description: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    exchange: Optional[str] = None
    recommend_all: Optional[float] = None
    rsi: Optional[float] = None
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


class TVScreenerSource(DataSource):
    """Current quote snapshots from TradingView Screener.

    `get_quotes()` is the primary API for watchlist tracking. `get_daily()` is a
    compatibility shim for WhaleTrail's `DataSource` protocol: it returns one
    single-row OHLCV DataFrame for the current snapshot when the snapshot date is
    inside the requested range.
    """

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def get_quotes(
        self,
        symbols: Iterable[str],
        endpoint_by_symbol: Optional[Mapping[str, str]] = None,
    ) -> list[QuoteSnapshot]:
        """Fetch current snapshots for TradingView symbols.

        Parameters
        ----------
        symbols:
            TradingView symbols such as `SSE:601899`, `AMEX:SPY`,
            `COMEX:GC1!`, or `NYMEX:CL1!`.
        endpoint_by_symbol:
            Optional explicit endpoint mapping. Values should be `global` or
            `futures`. When omitted, the endpoint is inferred from the symbol.
        """
        grouped: dict[str, list[str]] = {"global": [], "futures": []}
        for raw_symbol in symbols:
            symbol = raw_symbol.strip()
            if not symbol:
                continue
            endpoint = (
                endpoint_by_symbol.get(symbol) if endpoint_by_symbol else None
            ) or infer_endpoint(symbol)
            if endpoint not in SCAN_ENDPOINTS:
                raise ValueError(
                    f"Unsupported TradingView scanner endpoint {endpoint!r} for {symbol!r}."
                )
            grouped[endpoint].append(symbol)

        snapshots: list[QuoteSnapshot] = []
        fetched_at = datetime.now()
        for endpoint, endpoint_symbols in grouped.items():
            if not endpoint_symbols:
                continue
            snapshots.extend(
                self._scan_endpoint(endpoint, endpoint_symbols, fetched_at=fetched_at)
            )

        # Keep output stable and aligned with the caller's symbol order.
        order = {symbol: i for i, symbol in enumerate([s.strip() for s in symbols])}
        snapshots.sort(key=lambda quote: order.get(quote.symbol, 10**9))
        return snapshots

    def get_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return a single-row OHLCV DataFrame for the current snapshot.

        This is a convenience fallback for paper-live style scans. It should not
        be used as a substitute for full historical daily bars in backtests.
        """
        quotes = self.get_quotes([symbol])
        if not quotes:
            return _empty_df()

        quote = quotes[0]
        quote_date = quote.timestamp.date()
        if quote_date < start or quote_date > end:
            return _empty_df()

        row = {
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "close": quote.close,
            "volume": quote.volume if quote.volume is not None else 0,
        }
        df = pd.DataFrame([row], index=pd.DatetimeIndex([quote.timestamp], name="date"))
        return df[_STD_OHLCV_COLUMNS].sort_index()

    def _scan_endpoint(
        self,
        endpoint: str,
        symbols: list[str],
        fetched_at: datetime,
    ) -> list[QuoteSnapshot]:
        payload = {
            "filter": [],
            "options": {"lang": "en"},
            "symbols": {"tickers": symbols, "query": {"types": []}},
            "sort": {"sortBy": "name", "sortOrder": "asc"},
            "range": [0, len(symbols)],
            "columns": SNAPSHOT_COLUMNS,
        }

        logger.info("TradingView scanner request: endpoint=%s symbols=%s", endpoint, symbols)
        response = requests.post(
            SCAN_ENDPOINTS[endpoint],
            data=json.dumps(payload),
            headers=REQUEST_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()

        snapshots: list[QuoteSnapshot] = []
        for item in body.get("data", []):
            data = item.get("d", [])
            values = dict(zip(SNAPSHOT_COLUMNS, data))
            snapshots.append(
                QuoteSnapshot(
                    symbol=item.get("s", ""),
                    timestamp=fetched_at,
                    endpoint=endpoint,
                    name=values.get("name"),
                    description=values.get("description"),
                    open=_to_float(values.get("open")),
                    high=_to_float(values.get("high")),
                    low=_to_float(values.get("low")),
                    close=_to_float(values.get("close")),
                    change_percent=_to_float(values.get("change")),
                    volume=_to_float(values.get("volume")),
                    exchange=values.get("exchange"),
                    recommend_all=_to_float(values.get("Recommend.All")),
                    rsi=_to_float(values.get("RSI")),
                    sma20=_to_float(values.get("SMA20")),
                    sma50=_to_float(values.get("SMA50")),
                    sma200=_to_float(values.get("SMA200")),
                    raw={"scanner": item, "columns": SNAPSHOT_COLUMNS},
                )
            )

        returned = {quote.symbol for quote in snapshots}
        missing = [symbol for symbol in symbols if symbol not in returned]
        if missing:
            logger.warning("TradingView scanner returned no data for: %s", missing)

        return snapshots


def infer_endpoint(symbol: str) -> str:
    """Infer the TradingView scanner endpoint for a symbol."""
    upper_symbol = symbol.upper()
    if upper_symbol.startswith(_FUTURES_EXCHANGE_PREFIXES) or upper_symbol.endswith("1!"):
        return "futures"
    return "global"


def snapshots_to_frame(snapshots: Iterable[QuoteSnapshot]) -> pd.DataFrame:
    """Convert snapshots to a DataFrame for scripts and reports."""
    rows = [quote.to_dict() for quote in snapshots]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_STD_OHLCV_COLUMNS)
