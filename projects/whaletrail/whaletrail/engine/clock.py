"""Trading clock for the WhaleTrail backtesting engine.

Iterates over a list of bar timestamps, driving the main backtest loop
one bar at a time (timeframe-agnostic: daily sessions or intraday bars).
"""

from __future__ import annotations

from typing import Iterator


class TradingClock:
    """A simple trading clock that yields bar timestamps one at a time.

    Usage::

        clock = TradingClock(dates=[date(2024, 1, 2), date(2024, 1, 3)])
        for today in clock:
            print(today)
    """

    def __init__(self, dates: list) -> None:
        """Initialise the clock with an ordered list of bar timestamps
        (``date`` for daily data, ``pd.Timestamp`` for intraday).
        """
        self.dates: list[date] = list(dates)
        self._index: int = 0
        self._length: int = len(self.dates)

    def __iter__(self) -> Iterator[date]:
        self._index = 0
        return self

    def __next__(self) -> date:
        if self._index >= self._length:
            raise StopIteration
        d = self.dates[self._index]
        self._index += 1
        return d

    def __len__(self) -> int:
        return self._length

    def __repr__(self) -> str:
        if not self.dates:
            return "TradingClock(empty)"
        return f"TradingClock({self.dates[0]} → {self.dates[-1]}, {self._length} days)"
