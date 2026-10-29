"""Ingest locked call sheets and settle them against completed daily bars.

``ingest`` never looks at a price. ``settle_day`` is the only place cash moves,
and it is safe to run again for the same date.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

import yaml

from whaletrail.arena.rules import (
    BUST_EQUITY,
    DAILY_NEW_BUYS,
    GROK_HANDLE,
    GROK_MIN_CHARS,
    HS300,
    INITIAL_CASH,
    MAX_ACTIVE,
    MAX_NAMES,
    NO_POST_REMOVE_TRADING_DAYS,
    STALE_TRADING_DAYS,
    STANCES,
    D,
    STAMP_TAX,
    buy_fill,
    capture_deadline,
    commission,
    evidence_ok,
    canonical_code,
    is_one_price,
    at_limit,
    limit_prices,
    next_trading_day,
    parse_cn_time,
    q2,
    sell_fill,
    slot_qty,
    stop_from,
    target_entry,
    trading_days_since,
)

IsTrading = Callable[[date], bool]


def connect(db_path) -> sqlite3.Connection:
    from whaletrail.storage.schema import create_tables

    conn = create_tables(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_roster(path) -> list[dict]:
    data = yaml.safe_load(Path_read(path))
    people = []
    for row in data.get("participants") or []:
        people.append(
            {
                "handle": str(row["handle"]).lstrip("@").strip().lower(),
                "display_name": str(row.get("name") or row["handle"]),
                "kind": str(row.get("kind") or "kol"),
                "joined_on": str(row.get("joined_on") or data["joined_on"]),
            }
        )
    return people


def Path_read(path) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


def ensure_participants(conn: sqlite3.Connection, roster: list[dict], now: datetime) -> None:
    stamp = now.astimezone().isoformat(timespec="seconds")
    for person in roster:
        conn.execute(
            """
            INSERT INTO arena_participants
                (handle, display_name, kind, joined_on, cash, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(handle) DO UPDATE SET
                display_name = excluded.display_name,
                kind = excluded.kind
            """,
            (
                person["handle"],
                person["display_name"],
                person["kind"],
                person["joined_on"],
                float(INITIAL_CASH),
                stamp,
            ),
        )
    conn.commit()


def load_universe(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    by_code: dict[str, str] = {}
    buckets: dict[str, list[str]] = {}
    try:
        rows = conn.execute("SELECT code, name FROM ashare_universe").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        code = str(row["code"])
        name = str(row["name"] or "").strip()
        by_code[code] = name
        if name:
            buckets.setdefault(name, []).append(code)
    by_name = {name: codes[0] for name, codes in buckets.items() if len(codes) == 1}
    return by_code, by_name


def _participant(conn, handle: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM arena_participants WHERE handle = ?", (handle,)
    ).fetchone()


def _insert_post(conn, payload: dict, stance: str, late: bool, captured: datetime) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO arena_posts (
            post_id, handle, created_at, captured_at, text, ocr_text,
            permalink, raw_json, stance, late_capture
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["post_id"],
            payload["handle"],
            payload["created_at"],
            captured.isoformat(timespec="seconds"),
            payload["text"],
            payload.get("ocr_text") or "",
            payload.get("permalink"),
            json.dumps(payload.get("raw"), ensure_ascii=False) if payload.get("raw") is not None else None,
            stance,
            1 if late else 0,
        ),
    )
    return cur.rowcount == 1


def _add_claim(conn, post_id, handle, stance, entry, code, name, evidence, reason) -> int:
    cur = conn.execute(
        """
        INSERT INTO arena_claims (
            post_id, handle, code, name, stance, entry_date, evidence, reject_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id,
            handle,
            code,
            name,
            stance,
            entry.isoformat() if entry else None,
            evidence,
            reason,
        ),
    )
    return int(cur.lastrowid)


def _buy_count(conn, handle: str, entry: date) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM arena_orders
        WHERE handle = ? AND entry_date = ? AND side = 'buy'
        """,
        (handle, entry.isoformat()),
    ).fetchone()
    return int(row["n"])


def _active_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM arena_participants WHERE COALESCE(status, 'active') = 'active'"
    ).fetchone()
    return int(row["n"])


def _enroll(conn, handle: str, display_name: str, joined_on: date, now: datetime) -> None:
    conn.execute(
        """
        INSERT INTO arena_participants
            (handle, display_name, kind, joined_on, cash, created_at, status)
        VALUES (?, ?, 'kol', ?, ?, ?, 'active')
        """,
        (
            handle,
            display_name or handle,
            joined_on.isoformat(),
            float(INITIAL_CASH),
            now.isoformat(timespec="seconds"),
        ),
    )


def _reactivate(conn, handle: str, display_name: str, joined_on: date) -> None:
    """Bring a removed (never posted) account back as a fresh entrant.

    Removed accounts never traded, so they restart with the standard cash and a
    new ``joined_on``. Old equity rows are kept as they are.
    """
    conn.execute(
        """
        UPDATE arena_participants
        SET status = 'active', joined_on = ?, cash = ?, display_name = ?,
            eliminated_on = NULL, eliminated_reason = NULL
        WHERE handle = ? AND status = 'removed'
        """,
        (joined_on.isoformat(), float(INITIAL_CASH), display_name or handle, handle),
    )


def _resolve_name(item: dict, by_code: dict[str, str], by_name: dict[str, str]) -> tuple[str, str, str]:
    """Return ``(code, display_name, reason)``. ``reason`` empty means accepted."""
    token = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    if token:
        code, status = canonical_code(token)
        if status == "bse":
            return "", name, "bse"
        if status != "ok":
            return "", name, "bad_code"
        if code not in by_code:
            return code, name, "unknown_code"
        return code, by_code[code] or name, ""
    if not name:
        return "", "", "no_name"
    code = by_name.get(name)
    if not code:
        return "", name, "ambiguous_name"
    return code, name, ""


def ingest(
    conn: sqlite3.Connection,
    payload: dict,
    now: datetime,
    by_code: dict[str, str],
    by_name: dict[str, str],
    is_trading: IsTrading,
) -> dict:
    """Archive one call sheet and lock orders. Prices are not consulted."""
    handle = str(payload.get("handle") or "").lstrip("@").strip().lower()
    post_id = str(payload.get("post_id") or "").strip()
    text = str(payload.get("text") or "")
    ocr = str(payload.get("ocr_text") or "")
    stance = str(payload.get("stance") or "").strip().lower()
    names = payload.get("names") or []
    if not isinstance(names, list):
        return {"ok": False, "error": "names 不是数组"}
    if not handle or not post_id or not text:
        return {"ok": False, "error": "缺少 handle / post_id / text"}
    if stance not in STANCES:
        return {"ok": False, "error": f"stance 无法识别：{stance or '空'}"}
    try:
        created = parse_cn_time(str(payload.get("created_at") or ""))
    except (TypeError, ValueError):
        return {"ok": False, "error": "created_at 不是时间"}
    captured = now.astimezone(created.tzinfo)
    if created > captured + timedelta(minutes=5):
        return {"ok": False, "error": "created_at 晚于锁定时间"}

    entry = target_entry(created, is_trading)
    # late_capture vs entry-day SETTLE_CUTOFF (15:30), not post LOCK (09:25).
    # Grok and KOL share this path — no stricter lock deadline for Grok.
    late = captured > capture_deadline(entry)
    person = _participant(conn, handle)
    if person is not None and (person["status"] or "active") == "eliminated":
        return {"ok": False, "error": f"已出局，不再入赛：{handle}"}
    removed = person is not None and person["status"] == "removed"
    if person is None or removed:
        if handle == GROK_HANDLE:
            return {"ok": False, "error": "Grok 不在名册"}
        if stance != "forward" or not (1 <= len(names) <= MAX_NAMES):
            return {"ok": False, "error": "新人要有 1 到 3 只向前喊单才入赛"}
        if late:
            return {"ok": False, "error": "新人的第一份喊单过了截单时间"}
        live = 0
        for item in names:
            if not isinstance(item, dict):
                continue
            _code, _name, why = _resolve_name(item, by_code, by_name)
            if why or not evidence_ok(str(item.get("evidence") or ""), text, ocr):
                continue
            live += 1
        if live == 0:
            return {"ok": False, "error": "新人的喊单对不上代码或原文"}
        if _active_count(conn) >= MAX_ACTIVE:
            return {"ok": False, "error": "在赛已满 100 人"}
        display = str(payload.get("display_name") or payload.get("name") or handle)
        if removed:
            _reactivate(conn, handle, display, entry)
        else:
            _enroll(conn, handle, display, entry, captured)
        person = _participant(conn, handle)

    joined = date.fromisoformat(person["joined_on"])
    kind = person["kind"]

    if kind == GROK_HANDLE or handle == GROK_HANDLE:
        if post_id != f"grok:{entry.isoformat()}":
            return {"ok": False, "error": f"Grok 的 post_id 必须是 grok:{entry.isoformat()}"}
        if len(text.strip()) < GROK_MIN_CHARS:
            return {"ok": False, "error": "Grok 参赛文不足 40 字"}
        if stance != "forward":
            return {"ok": False, "error": "Grok 参赛单的 stance 只能是 forward"}

    if len(names) > MAX_NAMES:
        stance = "list"

    if not _insert_post(conn, {**payload, "handle": handle, "post_id": post_id, "text": text, "ocr_text": ocr}, stance, late, captured):
        conn.commit()
        return {"ok": True, "duplicate": True, "post_id": post_id, "orders": 0}

    orders = 0
    reasons: list[str] = []

    def reject(code, name, evidence, reason):
        reasons.append(reason)
        _add_claim(conn, post_id, handle, stance, entry if not late else None, code or None, name or None, evidence, reason)

    if late:
        reject(None, None, None, "late_capture")
    elif entry < joined:
        reject(None, None, None, "not_yet_joined")
    elif stance in {"recap", "list", "ambiguous"}:
        reason = "too_many_names" if len(names) > MAX_NAMES else stance
        reject(None, None, None, reason)
    elif stance == "forward" and not names:
        reject(None, None, None, "no_names")
    elif stance == "exit":
        if len(names) != 1:
            reject(None, None, None, "exit_needs_one")
        else:
            orders += _lock_exit(conn, post_id, handle, names[0], entry, text, ocr, by_code, by_name, reasons)
    else:
        for item in names:
            if not isinstance(item, dict):
                reject(None, None, None, "bad_item")
                continue
            evidence = str(item.get("evidence") or "")
            code, name, why = _resolve_name(item, by_code, by_name)
            if why:
                reject(code, name, evidence, why)
                continue
            if not evidence_ok(evidence, text, ocr):
                reject(code, name, evidence, "evidence")
                continue
            if _buy_count(conn, handle, entry) >= DAILY_NEW_BUYS:
                reject(code, name, evidence, "daily_cap")
                continue
            taken = conn.execute(
                """
                SELECT 1 FROM arena_orders
                WHERE handle = ? AND code = ? AND entry_date = ? AND side = 'buy'
                """,
                (handle, code, entry.isoformat()),
            ).fetchone()
            if taken:
                reject(code, name, evidence, "duplicate_code")
                continue
            claim_id = _add_claim(conn, post_id, handle, "forward", entry, code, name, evidence, None)
            conn.execute(
                """
                INSERT INTO arena_orders (claim_id, handle, code, side, entry_date, status)
                VALUES (?, ?, ?, 'buy', ?, 'locked')
                """,
                (claim_id, handle, code, entry.isoformat()),
            )
            orders += 1

    conn.commit()
    return {
        "ok": True,
        "duplicate": False,
        "post_id": post_id,
        "handle": handle,
        "entry_date": None if late else entry.isoformat(),
        "stance": stance,
        "late": late,
        "orders": orders,
        "rejects": reasons,
    }


def _lock_exit(conn, post_id, handle, item, entry, text, ocr, by_code, by_name, reasons) -> int:
    evidence = str(item.get("evidence") or "")
    code, name, why = _resolve_name(item, by_code, by_name)
    if why:
        reasons.append(why)
        _add_claim(conn, post_id, handle, "exit", entry, code or None, name or None, evidence, why)
        return 0
    if not evidence_ok(evidence, text, ocr):
        reasons.append("evidence")
        _add_claim(conn, post_id, handle, "exit", entry, code, name, evidence, "evidence")
        return 0
    claim_id = _add_claim(conn, post_id, handle, "exit", entry, code, name, evidence, None)
    conn.execute(
        """
        INSERT OR IGNORE INTO arena_orders (claim_id, handle, code, side, entry_date, status)
        VALUES (?, ?, ?, 'sell', ?, 'locked')
        """,
        (claim_id, handle, code, entry.isoformat()),
    )
    return 1


def _bar(conn, code: str, day: date):
    return conn.execute(
        """
        SELECT trade_date, open, high, low, close, tradestatus, is_st
        FROM daily_kline WHERE code = ? AND trade_date = ?
        """,
        (code, day.isoformat()),
    ).fetchone()


def _prev_bar(conn, code: str, day: date):
    return conn.execute(
        """
        SELECT trade_date, close, is_st FROM daily_kline
        WHERE code = ? AND trade_date < ? AND close IS NOT NULL
        ORDER BY trade_date DESC LIMIT 1
        """,
        (code, day.isoformat()),
    ).fetchone()


def _index_bar(conn, day: date):
    return conn.execute(
        "SELECT open, close FROM index_kline WHERE code = ? AND trade_date = ?",
        (HS300, day.isoformat()),
    ).fetchone()


def _cash(conn, handle: str) -> Decimal:
    row = conn.execute("SELECT cash FROM arena_participants WHERE handle = ?", (handle,)).fetchone()
    return D(row["cash"])


def _set_cash(conn, handle: str, cash: Decimal) -> None:
    conn.execute(
        "UPDATE arena_participants SET cash = ? WHERE handle = ?",
        (float(q2(cash)), handle),
    )


def _holding(conn, handle: str, code: str):
    return conn.execute(
        """
        SELECT * FROM arena_orders
        WHERE handle = ? AND code = ? AND side = 'buy' AND status = 'filled' AND exit_date IS NULL
        ORDER BY entry_date LIMIT 1
        """,
        (handle, code),
    ).fetchone()


def _has_exit_call(conn, handle: str, code: str, day: date) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM arena_orders
        WHERE handle = ? AND code = ? AND side = 'sell' AND entry_date = ? AND status = 'locked'
        """,
        (handle, code, day.isoformat()),
    ).fetchone()
    return row is not None


def _mark_exit_calls(conn, handle: str, code: str, day: date, status: str) -> None:
    conn.execute(
        """
        UPDATE arena_orders SET status = ?
        WHERE handle = ? AND code = ? AND side = 'sell' AND entry_date = ? AND status = 'locked'
        """,
        (status, handle, code, day.isoformat()),
    )


def settle_day(conn: sqlite3.Connection, day: date, is_trading: IsTrading) -> dict:
    """Fill today's locked buys and exit positions that are already sellable."""
    if not is_trading(day):
        return {"date": day.isoformat(), "fills": 0, "exits": 0, "skipped": True}
    fills = 0
    exits = 0
    locked = conn.execute(
        """
        SELECT * FROM arena_orders
        WHERE side = 'buy' AND status = 'locked' AND entry_date = ?
        ORDER BY id
        """,
        (day.isoformat(),),
    ).fetchall()
    for order in locked:
        if _try_fill(conn, order, day):
            fills += 1
    held = conn.execute(
        """
        SELECT * FROM arena_orders
        WHERE side = 'buy' AND status = 'filled' AND exit_date IS NULL AND entry_date < ?
        ORDER BY entry_date, id
        """,
        (day.isoformat(),),
    ).fetchall()
    for order in held:
        if _try_exit(conn, order, day, is_trading):
            exits += 1
    pending = conn.execute(
        "SELECT * FROM arena_orders WHERE status = 'filled' AND ret_pred IS NULL"
    ).fetchall()
    for order in pending:
        _prediction_return(conn, order["id"], order["code"], date.fromisoformat(order["entry_date"]), is_trading)
    _mark_equity(conn, day)
    _retire(conn, day, is_trading)
    conn.commit()
    return {"date": day.isoformat(), "fills": fills, "exits": exits, "skipped": False}


def settle_through(conn: sqlite3.Connection, through: date, is_trading: IsTrading) -> list[dict]:
    row = conn.execute(
        """
        SELECT MIN(entry_date) AS d FROM arena_orders
        WHERE status = 'locked' OR (status = 'filled' AND exit_date IS NULL)
           OR (status = 'filled' AND ret_pred IS NULL)
        """
    ).fetchone()
    if row["d"] is None:
        if is_trading(through):
            _mark_equity(conn, through)
            _retire(conn, through, is_trading)
        conn.commit()
        return []
    start = date.fromisoformat(row["d"])
    reports = []
    cur = start
    while cur <= through:
        if is_trading(cur):
            reports.append(settle_day(conn, cur, is_trading))
        cur += timedelta(days=1)
    return reports


def _try_fill(conn, order, day: date) -> bool:
    handle, code = order["handle"], order["code"]
    if _holding(conn, handle, code) is not None:
        _skip(conn, order["id"], "holding")
        return False
    bar = _bar(conn, code, day)
    if bar is None or bar["open"] is None:
        return False
    if bar["tradestatus"] == 0:
        _skip(conn, order["id"], "suspended")
        return False
    prev = _prev_bar(conn, code, day)
    if prev is None or prev["close"] is None:
        return False
    is_st = bool(bar["is_st"] if bar["is_st"] is not None else prev["is_st"])
    up, _down = limit_prices(D(prev["close"]), code, is_st)
    if is_one_price(bar["low"], bar["high"]) and at_limit(bar["close"], up):
        _skip(conn, order["id"], "limit_up")
        return False
    open_px = D(bar["open"])
    qty = slot_qty(open_px)
    if qty < 100:
        _skip(conn, order["id"], "lot_too_expensive")
        return False
    fill = buy_fill(open_px)
    notional = q2(fill * qty)
    fee = commission(notional)
    cash = _cash(conn, handle)
    if cash < notional + fee:
        _skip(conn, order["id"], "cash_short")
        return False
    _set_cash(conn, handle, cash - notional - fee)
    conn.execute(
        """
        UPDATE arena_orders
        SET status = 'filled', qty = ?, entry_open = ?, entry_fill = ?, entry_cost = ?, stop_price = ?
        WHERE id = ?
        """,
        (qty, float(q2(open_px)), float(fill), float(fee), float(stop_from(open_px)), order["id"]),
    )
    return True


def _skip(conn, order_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE arena_orders SET status = 'skipped', skip_reason = ? WHERE id = ?",
        (reason, order_id),
    )


def _prediction_return(conn, order_id: int, code: str, entry: date, is_trading: IsTrading) -> None:
    row = conn.execute("SELECT * FROM arena_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None or row["ret_pred"] is not None or row["status"] != "filled":
        return
    pred_day = next_trading_day(entry, is_trading)
    bar = _bar(conn, code, pred_day)
    if bar is None or bar["close"] is None or bar["tradestatus"] == 0:
        return
    qty = int(row["qty"])
    raw_close = D(bar["close"])
    fill = sell_fill(raw_close)
    notional = q2(fill * qty)
    fee = commission(notional)
    stamp = q2(notional * STAMP_TAX)
    proceeds = notional - fee - stamp
    cost = q2(D(row["entry_fill"]) * qty) + D(row["entry_cost"])
    pnl = proceeds - cost
    ret = float(pnl / cost) if cost else None
    excess = None
    idx_in = _index_bar(conn, entry)
    idx_out = _index_bar(conn, pred_day)
    if ret is not None and idx_in and idx_out and idx_in["open"] and idx_out["close"] and D(idx_in["open"]) > 0:
        base = float(D(idx_out["close"]) / D(idx_in["open"]) - 1)
        excess = ret - base
    conn.execute(
        "UPDATE arena_orders SET ret_pred = ?, excess = ?, pred_date = ? WHERE id = ?",
        (ret, excess, pred_day.isoformat(), order_id),
    )


def _try_exit(conn, order, day: date, is_trading: IsTrading) -> bool:
    entry = date.fromisoformat(order["entry_date"])
    _prediction_return(conn, order["id"], order["code"], entry, is_trading)
    bar = _bar(conn, order["code"], day)
    if bar is None or bar["open"] is None:
        return False
    if bar["tradestatus"] == 0:
        return False
    prev = _prev_bar(conn, order["code"], day)
    is_st = bool(bar["is_st"] if bar["is_st"] is not None else (prev["is_st"] if prev else 0))
    _up, down = limit_prices(D(prev["close"]), order["code"], is_st) if prev and prev["close"] is not None else (None, None)
    if down is not None and is_one_price(bar["low"], bar["high"]) and at_limit(bar["close"], down):
        return False
    open_px = D(bar["open"])
    low_px = D(bar["low"])
    close_px = D(bar["close"])
    stop = D(order["stop_price"])
    if _has_exit_call(conn, order["handle"], order["code"], day):
        raw, reason = open_px, "exit_call"
        _mark_exit_calls(conn, order["handle"], order["code"], day, "closed")
    elif open_px <= stop:
        raw, reason = open_px, "stop_gap"
    elif low_px <= stop:
        raw, reason = stop, "stop_touch"
    else:
        raw, reason = close_px, "time"
    _book_exit(conn, order, day, raw, reason)
    return True


def _book_exit(conn, order, day: date, raw: Decimal, reason: str) -> None:
    qty = int(order["qty"])
    fill = sell_fill(raw)
    notional = q2(fill * qty)
    fee = commission(notional)
    stamp = q2(notional * STAMP_TAX)
    proceeds = notional - fee - stamp
    cost = q2(D(order["entry_fill"]) * qty) + D(order["entry_cost"])
    pnl = proceeds - cost
    ret = float(pnl / cost) if cost else None
    _set_cash(conn, order["handle"], _cash(conn, order["handle"]) + proceeds)
    conn.execute(
        """
        UPDATE arena_orders
        SET exit_date = ?, exit_reason = ?, exit_raw = ?, exit_fill = ?,
            exit_cost = ?, stamp = ?, pnl = ?, ret_acct = ?
        WHERE id = ?
        """,
        (
            day.isoformat(),
            reason,
            float(q2(raw)),
            float(fill),
            float(fee),
            float(stamp),
            float(q2(pnl)),
            ret,
            order["id"],
        ),
    )


def _mark_equity(conn, day: date) -> None:
    people = conn.execute("SELECT handle, cash, joined_on FROM arena_participants").fetchall()
    for person in people:
        if date.fromisoformat(person["joined_on"]) > day:
            continue
        mv = Decimal("0")
        held = conn.execute(
            """
            SELECT code, qty FROM arena_orders
            WHERE handle = ? AND side = 'buy' AND status = 'filled' AND exit_date IS NULL
            """,
            (person["handle"],),
        ).fetchall()
        for pos in held:
            bar = _bar(conn, pos["code"], day)
            price = D(bar["close"]) if bar and bar["close"] is not None else None
            if price is None:
                last = _prev_bar(conn, pos["code"], day)
                price = D(last["close"]) if last and last["close"] is not None else Decimal("0")
            mv += q2(price * int(pos["qty"]))
        cash = D(person["cash"])
        conn.execute(
            """
            INSERT INTO arena_equity (handle, trade_date, cash, market_value, equity)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(handle, trade_date) DO UPDATE SET
                cash = excluded.cash, market_value = excluded.market_value, equity = excluded.equity
            """,
            (person["handle"], day.isoformat(), float(q2(cash)), float(q2(mv)), float(q2(cash + mv))),
        )


def _eliminate(conn, handle: str, day: date, reason: str) -> None:
    conn.execute(
        """
        UPDATE arena_participants
        SET status = 'eliminated', eliminated_on = ?, eliminated_reason = ?
        WHERE handle = ? AND COALESCE(status, 'active') = 'active'
        """,
        (day.isoformat(), reason, handle),
    )


def _remove(conn, handle: str, day: date, reason: str) -> None:
    """Mark a never-posted account as removed. Nothing is deleted; it may rejoin."""
    conn.execute(
        """
        UPDATE arena_participants
        SET status = 'removed', eliminated_on = ?, eliminated_reason = ?
        WHERE handle = ? AND COALESCE(status, 'active') = 'active'
        """,
        (day.isoformat(), reason, handle),
    )


def _retire(conn, day: date, is_trading: IsTrading) -> None:
    """Bench flat accounts: removed if no post in 5 sessions since joining,
    eliminated if broke or silent for 10 sessions."""
    people = conn.execute(
        """
        SELECT handle, joined_on FROM arena_participants
        WHERE COALESCE(status, 'active') = 'active'
        """
    ).fetchall()
    for person in people:
        if person["handle"] == GROK_HANDLE:
            continue  # 白名单：Grok 不会被移出或出局
        joined = date.fromisoformat(person["joined_on"])
        if joined > day:
            continue
        held = conn.execute(
            """
            SELECT 1 FROM arena_orders
            WHERE handle = ? AND side = 'buy' AND status = 'filled' AND exit_date IS NULL
            """,
            (person["handle"],),
        ).fetchone()
        if held:
            continue
        eq = conn.execute(
            "SELECT equity FROM arena_equity WHERE handle = ? AND trade_date = ?",
            (person["handle"], day.isoformat()),
        ).fetchone()
        if eq is not None and D(eq["equity"]) < BUST_EQUITY:
            _eliminate(conn, person["handle"], day, "bust")
            continue
        last = conn.execute(
            "SELECT MAX(created_at) AS t FROM arena_posts WHERE handle = ?",
            (person["handle"],),
        ).fetchone()
        if not (last and last["t"]):
            any_order = conn.execute(
                "SELECT 1 FROM arena_orders WHERE handle = ? LIMIT 1", (person["handle"],)
            ).fetchone()
            if any_order is None and trading_days_since(joined, day, is_trading) >= NO_POST_REMOVE_TRADING_DAYS:
                _remove(conn, person["handle"], day, "no_posts")
                continue
        anchor = joined
        if last and last["t"]:
            anchor = parse_cn_time(str(last["t"])).date()
        if trading_days_since(anchor, day, is_trading) >= STALE_TRADING_DAYS:
            _eliminate(conn, person["handle"], day, "stale")


def open_db_and_roster(db_path, roster_file, now: datetime) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_participants(conn, load_roster(roster_file), now)
    return conn
