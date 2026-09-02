"""Markdown reports for WhaleTrail watchlist quote snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping, Optional


def render_watchlist_report(
    snapshots: Iterable[Mapping],
    generated_at: Optional[datetime] = None,
    title: str = "WhaleTrail Watchlist Report",
) -> str:
    """Render a Markdown report from quote snapshot dictionaries."""
    rows = list(snapshots)
    generated_at = generated_at or datetime.now()

    lines = [
        f"# {title}",
        "",
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if not rows:
        lines.append("No quote snapshots available.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Symbol | Name | Asset | Close | Change % | Volume | RSI | SMA20 | SMA50 | SMA200 | Source |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {symbol} | {name} | {asset} | {close} | {change} | {volume} | {rsi} | {sma20} | {sma50} | {sma200} | {source} |".format(
                symbol=row.get("tv_symbol") or row.get("symbol") or "",
                name=row.get("local_name") or row.get("name") or row.get("description") or "",
                asset=row.get("asset_class") or "",
                close=_fmt(row.get("close")),
                change=_fmt(row.get("change_percent")),
                volume=_fmt(row.get("volume"), decimals=0),
                rsi=_fmt(row.get("rsi")),
                sma20=_fmt(row.get("sma20")),
                sma50=_fmt(row.get("sma50")),
                sma200=_fmt(row.get("sma200")),
                source=row.get("source") or "",
            )
        )

    lines.extend(["", "## Quick read", ""])
    movers = sorted(
        [row for row in rows if row.get("change_percent") is not None],
        key=lambda row: float(row.get("change_percent") or 0),
        reverse=True,
    )
    if movers:
        best = movers[0]
        worst = movers[-1]
        lines.append(
            f"- Strongest: **{best.get('local_name') or best.get('tv_symbol') or best.get('symbol')}** "
            f"({_fmt(best.get('change_percent'))}%)."
        )
        lines.append(
            f"- Weakest: **{worst.get('local_name') or worst.get('tv_symbol') or worst.get('symbol')}** "
            f"({_fmt(worst.get('change_percent'))}%)."
        )

    stretched = [
        row for row in rows
        if row.get("rsi") is not None and (float(row["rsi"]) >= 70 or float(row["rsi"]) <= 30)
    ]
    if stretched:
        labels = ", ".join(
            f"{row.get('local_name') or row.get('tv_symbol')} RSI={_fmt(row.get('rsi'))}"
            for row in stretched
        )
        lines.append(f"- RSI watch: {labels}.")
    else:
        lines.append("- RSI watch: no item is currently above 70 or below 30.")

    return "\n".join(lines) + "\n"


def _fmt(value, decimals: int = 2) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)
