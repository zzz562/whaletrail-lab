"""Account management for the WhaleTrail backtesting engine.

Tracks cash, positions, commission costs, and computes total equity
at any point in time.  Supports both long and short positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Fill


@dataclass
class Position:
    """A holding in a single symbol.

    Attributes:
        symbol: The ticker symbol.
        quantity: Number of shares held.  Positive = long, negative = short.
        avg_cost: Volume-weighted average entry price.
    """

    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


@dataclass
class Account:
    """Tracks cash, positions, and total commission paid.

    Args:
        initial_cash: Starting cash balance.

    Usage::

        acct = Account(initial_cash=1_000_000.0)
        acct.apply_fill(fill)
        print(acct.total_equity({"000001.SZ": 12.50}))
    """

    initial_cash: float
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    total_commission: float = field(default=0.0, init=False)
    # Last known market price per symbol; guarantees every open position
    # can be marked even when the caller's price map lacks the symbol.
    last_prices: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash

    # ------------------------------------------------------------------
    #  Position helpers
    # ------------------------------------------------------------------

    def get_position(self, symbol: str) -> Position:
        """Return the Position for *symbol*, creating it if absent."""
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def position_value(self, symbol: str, price: float) -> float:
        """Market value of the position in *symbol* at *price*."""
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        return pos.quantity * price

    # ------------------------------------------------------------------
    #  Equity
    # ------------------------------------------------------------------

    def mark_prices(self, prices: dict[str, float]) -> None:
        """Record the latest known market prices (marks the book)."""
        for sym, price in prices.items():
            if price is not None and price > 0:
                self.last_prices[sym] = price

    def total_equity(self, latest_prices: dict[str, float] | None = None) -> float:
        """Total account equity = cash + sum(position_value for each symbol).

        Each position is marked at *latest_prices* when available, else at
        the most recent price seen via :meth:`mark_prices`/:meth:`apply_fill`.
        A position without any known price is marked at its avg cost rather
        than silently dropped from equity.
        """
        prices = dict(self.last_prices)
        if latest_prices:
            for sym, price in latest_prices.items():
                if price is None:
                    continue
                try:
                    px = float(price)
                except (TypeError, ValueError):
                    continue
                if px == px and px > 0:
                    prices[sym] = px
        positions_value = 0.0
        for sym, pos in self.positions.items():
            if pos.is_flat:
                continue
            price = prices.get(sym, pos.avg_cost)
            positions_value += pos.quantity * price
        return self.cash + positions_value

    # ------------------------------------------------------------------
    #  Fill application
    # ------------------------------------------------------------------

    def apply_fill(self, fill: "Fill") -> None:  # noqa: F821
        """Update cash and positions to reflect a filled order.

        Args:
            fill: A Fill object with symbol, quantity, price, commission.
        """
        pos = self.get_position(fill.symbol)

        # Update average cost basis
        old_qty = pos.quantity
        old_cost = pos.avg_cost
        fill_qty = fill.quantity

        new_qty = old_qty + fill_qty

        if new_qty == 0:
            # Position closed — reset avg_cost
            pos.quantity = 0.0
            pos.avg_cost = 0.0
        elif (old_qty > 0 and fill_qty > 0) or (old_qty < 0 and fill_qty < 0):
            # Adding to existing position (same side)
            total_cost = abs(old_qty) * old_cost + abs(fill_qty) * fill.price
            pos.quantity = new_qty
            pos.avg_cost = total_cost / abs(new_qty) if new_qty != 0 else 0.0
        else:
            # Reducing or flipping — if crossing zero, reset avg_cost
            if (old_qty > 0 and new_qty < 0) or (old_qty < 0 and new_qty > 0):
                # Position flipped — new side gets fill price as cost basis
                pos.quantity = new_qty
                pos.avg_cost = fill.price
            else:
                # Partial reduction — avg_cost unchanged
                pos.quantity = new_qty

        # Cash impact: buying costs cash, selling adds cash
        self.cash -= fill_qty * fill.price + fill.commission
        self.total_commission += fill.commission
        if fill.price > 0:
            self.last_prices[fill.symbol] = fill.price

    # ------------------------------------------------------------------
    #  Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        n_positions = sum(1 for p in self.positions.values() if not p.is_flat)
        return (
            f"Account(cash={self.cash:,.2f}, "
            f"positions={n_positions}, "
            f"commission={self.total_commission:.2f})"
        )
