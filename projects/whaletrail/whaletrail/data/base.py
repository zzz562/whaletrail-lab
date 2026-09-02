"""Abstract base class for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    """Abstract data source providing daily OHLCV bars.

    All concrete sources **must** return a ``pd.DataFrame`` with columns:

    - ``open``
    - ``high``
    - ``low``
    - ``close``
    - ``volume``

    The index must be a ``DatetimeIndex`` of trading days sorted
    ascending.
    """

    @abstractmethod
    def get_daily(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Return daily OHLCV data for *symbol* between *start* and *end*.

        Parameters
        ----------
        symbol : str
            Raw symbol string in market-native format (e.g. ``"AAPL"``,
            ``"GLD"`` or ``"SPY"``).
        start : date
            Start of the date range (inclusive).
        end : date
            End of the date range (inclusive).

        Returns
        -------
        pd.DataFrame
            Columns: ``[open, high, low, close, volume]``.
            Index: ``DatetimeIndex`` sorted ascending, tz-naive.
            Empty DataFrame if no data is available for the range.
        """
        ...
