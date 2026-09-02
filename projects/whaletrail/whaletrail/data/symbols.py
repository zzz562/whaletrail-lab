"""Symbol helpers — gold-first, US equities as hedge.

Supported
---------
- XAU : gold / precious metals ETFs & futures — GLD (primary), GC=F, SLV, …
- US  : US equities & index ETFs — SPY, QQQ, AAPL, …

Explicitly out of scope: A-shares, Hong Kong stocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    """Supported trading markets (narrow scope)."""

    US = "us"    # US equities / index ETFs
    XAU = "xau"  # Gold / precious metals


# Gold & metals universe (primary focus)
_GOLD_PRIMARY = frozenset({"GLD"})  # preferred for paper trading
_GOLD_RELATED = frozenset({
    "GLD", "IAU", "SGOL", "GLDM",   # gold ETFs
    "SLV", "SIVR",                   # silver
    "GC=F", "SI=F", "HG=F",         # futures (optional)
    "XAUUSD=X",
})

# Common hedge / benchmark ETFs
_US_BENCHMARKS = frozenset({"SPY", "QQQ", "IWM", "DIA", "TLT", "UUP"})


@dataclass(frozen=True)
class Symbol:
    """Normalised instrument identifier."""

    raw: str
    market: Market
    ticker: str
    role: str = "equity"  # "gold_primary" | "gold_related" | "hedge" | "equity"

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        return (
            f"Symbol(raw={self.raw!r}, market={self.market.value!r}, "
            f"ticker={self.ticker!r}, role={self.role!r})"
        )


def parse_symbol(raw: str) -> Symbol:
    """Parse a raw symbol into a normalised :class:`Symbol`.

    Parameters
    ----------
    raw :
        e.g. ``"GLD"``, ``"SPY"``, ``"AAPL"``, ``"GC=F"``.

    Raises
    ------
    ValueError
        If the symbol looks like A-share / HK, or cannot be parsed.
    """
    s = raw.strip()
    upper = s.upper()

    # Reject A-shares / HK explicitly (out of scope)
    if re.fullmatch(r"\d{6}\.(SH|SZ)", upper):
        raise ValueError(
            f"A-share symbol {raw!r} is out of scope. "
            f"WhaleTrail focuses on gold (GLD) + US equities (SPY/QQQ/…)."
        )
    if re.fullmatch(r"\d{1,5}\.HK", upper):
        raise ValueError(
            f"Hong Kong symbol {raw!r} is out of scope. "
            f"Use gold (GLD) or US equities instead."
        )

    # Gold / metals
    if upper in _GOLD_RELATED or upper.endswith("=F") and upper[:2] in ("GC", "SI", "HG"):
        role = "gold_primary" if upper in _GOLD_PRIMARY else "gold_related"
        # Keep ticker as user gave it (GLD stays GLD, GC=F stays GC=F)
        return Symbol(raw=s, market=Market.XAU, ticker=upper, role=role)

    # US equities / ETFs: 1–5 letters, optional .suffix for some tickers
    if re.fullmatch(r"[A-Z]{1,5}", upper):
        role = "hedge" if upper in _US_BENCHMARKS else "equity"
        return Symbol(raw=s, market=Market.US, ticker=upper, role=role)

    # BRK.B style
    if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", upper):
        return Symbol(raw=s, market=Market.US, ticker=upper, role="equity")

    raise ValueError(
        f"Cannot parse symbol {raw!r}. "
        f"Supported: gold 'GLD'/'GC=F', US 'SPY'/'QQQ'/'AAPL'. "
        f"A-shares and HK are not supported."
    )


def is_gold_focus(symbol: str) -> bool:
    """True if symbol is in the gold/metals primary universe."""
    try:
        p = parse_symbol(symbol)
        return p.market == Market.XAU
    except ValueError:
        return False
