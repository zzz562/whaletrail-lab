"""Watchlist loading helpers for WhaleTrail."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class WatchlistItem:
    """A configured instrument in the local paper-trading watchlist."""

    id: str
    name: str
    tv_symbol: str
    yahoo_symbol: Optional[str] = None
    asset_class: Optional[str] = None
    market: Optional[str] = None
    exchange: Optional[str] = None
    tradable: bool = True
    data_source_priority: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WatchlistItem":
        return cls(
            id=str(data.get("id") or data.get("tv_symbol") or data.get("symbol")),
            name=str(data.get("name") or data.get("tv_symbol") or data.get("symbol")),
            tv_symbol=str(data.get("tv_symbol") or data.get("symbol")),
            yahoo_symbol=data.get("yahoo_symbol"),
            asset_class=data.get("asset_class"),
            market=data.get("market"),
            exchange=data.get("exchange"),
            tradable=bool(data.get("tradable", True)),
            data_source_priority=tuple(data.get("data_source_priority") or ()),
        )

    def display_name(self) -> str:
        return self.name or self.tv_symbol


def load_watchlist(path: str | Path) -> list[WatchlistItem]:
    """Load a YAML or JSON watchlist file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {path}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        if yaml is None:
            raise RuntimeError("PyYAML is required to read YAML watchlists: pip install pyyaml")
        data = yaml.safe_load(text)

    items = data.get("items", data if isinstance(data, list) else [])
    if not isinstance(items, list):
        raise ValueError("Watchlist must contain an 'items' list.")
    return [WatchlistItem.from_dict(item) for item in items if item.get("tv_symbol") or item.get("symbol")]


def tv_symbols(items: Iterable[WatchlistItem]) -> list[str]:
    """Return TradingView symbols from watchlist items, preserving order."""
    return [item.tv_symbol for item in items if item.tv_symbol]


def by_yahoo_symbol(items: Iterable[WatchlistItem]) -> dict[str, WatchlistItem]:
    """Return a lookup from Yahoo symbol to watchlist item."""
    return {
        item.yahoo_symbol.upper(): item
        for item in items
        if item.yahoo_symbol
    }


def by_tv_symbol(items: Iterable[WatchlistItem]) -> dict[str, WatchlistItem]:
    """Return a lookup from TradingView symbol to watchlist item."""
    return {item.tv_symbol: item for item in items if item.tv_symbol}
