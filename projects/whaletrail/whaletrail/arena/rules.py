"""Fixed arena rules. The model fills JSON; these functions accept or reject it.

Nothing here reads a price feed. Entry dates come from the post clock and the
trading calendar. Fills are applied later, against a completed daily bar.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
LOCK = time(9, 25)  # post clock cutoff: created_at <= this → same-day open
SETTLE_CUTOFF = time(15, 30)  # lock grace: must lock before this on entry day

INITIAL_CASH = Decimal("100000")
SLOT_CNY = Decimal("20000")
LOT = 100
COMMISSION_RATE = Decimal("0.00025")
MIN_COMMISSION = Decimal("5")
STAMP_TAX = Decimal("0.0005")
SLIPPAGE = Decimal("0.001")
STOP_PCT = Decimal("0.06")
SHRINK_PRIOR = 8
MAIN_BOARD_MIN_FILLS = 10
MAIN_BOARD_MAX_UNFILLED = Decimal("0.40")
MAX_NAMES = 3
DAILY_NEW_BUYS = 3
MAX_ACTIVE = 100
STALE_TRADING_DAYS = 10
NO_POST_REMOVE_TRADING_DAYS = 5  # 入赛满 5 个交易日一帖未发：移出（removed），不是出局
BUST_EQUITY = SLOT_CNY
GROK_HANDLE = "grok"
GROK_MIN_CHARS = 40
HS300 = "sh.000300"

STANCES = {"forward", "recap", "list", "exit", "ambiguous"}
_CODE_RE = re.compile(r"^(?:(sh|sz|bj))?(\d{6})$")
_BSE_TWO = ("83", "87", "43", "82", "92")


def D(value) -> Decimal:
    return Decimal(str(float(value)))


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_cn_time(raw: str) -> datetime:
    text = raw.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN)
    return dt.astimezone(CN)


def canonical_code(token: str) -> tuple[str, str]:
    """Return ``(baostock_code, 'ok'|'bse'|'bad')``.

    North-exchange names are recognized and refused. This book has no BJ bars.
    """
    raw = re.sub(r"[^0-9A-Za-z]", "", token or "").lower()
    raw = raw.removeprefix("sse").removeprefix("szse")
    match = _CODE_RE.fullmatch(raw)
    if not match:
        return "", "bad"
    prefix, digits = match.group(1), match.group(2)
    # 688/689 are STAR on Shanghai. Check them before the Beijing "8…" bucket.
    if digits.startswith(("688", "689")) and prefix in (None, "sh"):
        return f"sh.{digits}", "ok"
    if prefix == "bj" or digits.startswith(_BSE_TWO) or digits[0] in {"8", "4"}:
        return "", "bse"
    if prefix is None:
        prefix = "sh" if digits[0] in {"6", "9"} else "sz"
    return f"{prefix}.{digits}", "ok"


def limit_ratio(code: str, is_st: bool) -> Decimal:
    digits = code.split(".")[-1]
    if is_st:
        return Decimal("0.05")
    if digits.startswith(("300", "301", "688", "689")):
        return Decimal("0.20")
    return Decimal("0.10")


def limit_prices(prev_close: Decimal, code: str, is_st: bool) -> tuple[Decimal, Decimal]:
    ratio = limit_ratio(code, is_st)
    return q2(prev_close * (1 + ratio)), q2(prev_close * (1 - ratio))


def is_one_price(bar_low, bar_high) -> bool:
    if bar_low is None or bar_high is None:
        return False
    return abs(D(bar_low) - D(bar_high)) <= Decimal("0.001")


def at_limit(price, limit_px: Decimal) -> bool:
    if price is None:
        return False
    return abs(D(price) - limit_px) <= Decimal("0.011")


def commission(notional: Decimal) -> Decimal:
    return max(q2(notional * COMMISSION_RATE), MIN_COMMISSION)


def buy_fill(open_px: Decimal) -> Decimal:
    return q2(open_px * (1 + SLIPPAGE))


def sell_fill(raw_px: Decimal) -> Decimal:
    return q2(raw_px * (1 - SLIPPAGE))


def slot_qty(open_px: Decimal) -> int:
    fill = buy_fill(open_px)
    if fill <= 0:
        return 0
    return int(SLOT_CNY / fill / LOT) * LOT


def stop_from(open_px: Decimal) -> Decimal:
    return q2(open_px * (1 - STOP_PCT))


def next_trading_day(day: date, is_trading) -> date:
    cur = day
    for _ in range(20):
        cur += timedelta(days=1)
        if is_trading(cur):
            return cur
    raise RuntimeError(f"no trading day within 20 days after {day}")


def target_entry(created: datetime, is_trading) -> date:
    """Session whose open this post may trade, before the capture check.

    Cutoff is the post clock (created_at), not when arena-lock runs.
    Anything after 09:25, including lunch and the afternoon session, waits
    for the next trading day's open. Daily bars have no midday price.
    """
    created = created.astimezone(CN)
    if is_trading(created.date()) and created.time() <= LOCK:
        return created.date()
    return next_trading_day(created.date(), is_trading)


def capture_deadline(entry: date) -> datetime:
    """Latest wall clock when a sheet for ``entry`` may still be locked.

    Offline catch-up may lock after 09:25; fills still use that session open.
    After SETTLE_CUTOFF the day is closed for new locks (settle needs them).
    """
    return datetime.combine(entry, SETTLE_CUTOFF, tzinfo=CN)


def trading_days_since(start: date, end: date, is_trading) -> int:
    """Trading days strictly after ``start`` and on or before ``end``."""
    if end <= start:
        return 0
    count = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if is_trading(cur):
            count += 1
    return count


def shrinkage(mean: float, n: int, prior: int = SHRINK_PRIOR) -> float | None:
    if n <= 0:
        return None
    return mean * n / (n + prior)


def evidence_ok(evidence: str, text: str, ocr_text: str) -> bool:
    snippet = (evidence or "").strip()
    if not snippet:
        return False
    blob = f"{text or ''}\n{ocr_text or ''}"
    return snippet in blob
