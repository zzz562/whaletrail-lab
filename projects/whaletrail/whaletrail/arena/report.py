"""Read-only arena board. The dashboard and ``arena-status`` both use this."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from whaletrail.arena.rules import MAIN_BOARD_MAX_UNFILLED, MAIN_BOARD_MIN_FILLS, shrinkage


def _table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def leaderboard(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "arena_participants"):
        return []
    people = conn.execute(
        """
        SELECT handle, display_name, kind, joined_on, cash,
               COALESCE(status, 'active') AS status, eliminated_reason
        FROM arena_participants ORDER BY handle
        """
    ).fetchall()
    orders = conn.execute(
        "SELECT handle, status, skip_reason, ret_pred, excess, pnl FROM arena_orders WHERE side = 'buy'"
    ).fetchall() if _table(conn, "arena_orders") else []
    posts = conn.execute(
        "SELECT handle, stance, late_capture, deleted_at FROM arena_posts"
    ).fetchall() if _table(conn, "arena_posts") else []
    equity = conn.execute(
        "SELECT handle, trade_date, equity FROM arena_equity ORDER BY trade_date"
    ).fetchall() if _table(conn, "arena_equity") else []

    by_handle: dict[str, list] = defaultdict(list)
    for row in orders:
        by_handle[row["handle"]].append(row)
    post_by: dict[str, list] = defaultdict(list)
    for row in posts:
        post_by[row["handle"]].append(row)
    eq_by: dict[str, list] = defaultdict(list)
    for row in equity:
        eq_by[row["handle"]].append(row)

    board = []
    for person in people:
        handle = person["handle"]
        mine = by_handle[handle]
        filled = [o for o in mine if o["status"] == "filled" and o["ret_pred"] is not None]
        limit_up = sum(1 for o in mine if o["skip_reason"] == "limit_up")
        denom = len(filled) + limit_up
        unfilled = (limit_up / denom) if denom else 0.0
        excesses = [o["excess"] for o in filled if o["excess"] is not None]
        mean_excess = sum(excesses) / len(excesses) if excesses else None
        n = len(filled)
        wins = sum(1 for o in filled if o["ret_pred"] > 0)
        curve = eq_by[handle]
        peak = None
        max_dd = None
        for point in curve:
            eq = float(point["equity"])
            peak = eq if peak is None else max(peak, eq)
            if peak:
                dd = eq / peak - 1
                max_dd = dd if max_dd is None else min(max_dd, dd)
        mine_posts = post_by[handle]
        deleted = sum(1 for p in mine_posts if p["deleted_at"])
        lists = sum(1 for p in mine_posts if p["stance"] == "list")
        on_main = n >= MAIN_BOARD_MIN_FILLS and unfilled < float(MAIN_BOARD_MAX_UNFILLED)
        status = person["status"] or "active"
        if status == "eliminated":
            seat = "出局"
        elif status == "removed":
            seat = "移出"
        elif not mine and not curve:
            seat = "未开赛"
        elif on_main:
            seat = "主榜"
        else:
            seat = "观察席"
        board.append(
            {
                "handle": handle,
                "name": person["display_name"],
                "kind": person["kind"],
                "joined_on": person["joined_on"],
                "status": status,
                "eliminated_reason": person["eliminated_reason"],
                "seat": seat,
                "n": n,
                "win_rate": (wins / n) if n else None,
                "median_pred": _median([o["ret_pred"] for o in filled]),
                "mean_pred": (sum(o["ret_pred"] for o in filled) / n) if n else None,
                "mean_excess": mean_excess,
                "score": shrinkage(mean_excess, len(excesses)) if mean_excess is not None else None,
                "equity": float(curve[-1]["equity"]) if curve else None,
                "asof": curve[-1]["trade_date"] if curve else None,
                "max_dd": max_dd,
                "delete_rate": (deleted / len(mine_posts)) if mine_posts else None,
                "unfilled_rate": unfilled if denom else None,
                "list_rate": (lists / len(mine_posts)) if mine_posts else None,
                "posts": len(mine_posts),
            }
        )
    board.sort(key=lambda row: (
        row["seat"] in ("出局", "移出"),
        row["seat"] == "移出",
        row["seat"] != "主榜",
        -(row["score"] if row["score"] is not None else -999),
        row["handle"],
    ))
    return board


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def equity_series(conn: sqlite3.Connection, handle: str) -> list[dict]:
    if not _table(conn, "arena_equity"):
        return []
    rows = conn.execute(
        "SELECT trade_date, equity FROM arena_equity WHERE handle = ? ORDER BY trade_date",
        (handle,),
    ).fetchall()
    return [{"date": row["trade_date"], "equity": row["equity"]} for row in rows]


def contest_span(conn: sqlite3.Connection) -> dict:
    posts = 0
    first = None
    if _table(conn, "arena_posts"):
        row = conn.execute("SELECT COUNT(*) AS n, MIN(created_at) AS t FROM arena_posts").fetchone()
        posts = int(row["n"] or 0)
        first = row["t"]
    last = None
    if _table(conn, "arena_equity"):
        row = conn.execute("SELECT MAX(trade_date) AS d FROM arena_equity").fetchone()
        last = row["d"]
    return {"posts": posts, "first_post": first, "asof": last}


def stock_board(conn: sqlite3.Connection) -> list[dict]:
    """Closed calls grouped by stock. Win rate is among those settled trades."""
    if not _table(conn, "arena_orders"):
        return []
    rows = conn.execute(
        """
        SELECT o.code AS code,
               COALESCE(MAX(c.name), o.code) AS name,
               COUNT(*) AS n,
               SUM(CASE WHEN o.ret_acct > 0 THEN 1 ELSE 0 END) AS wins,
               AVG(o.ret_acct) AS avg_ret,
               GROUP_CONCAT(DISTINCT p.display_name) AS callers
        FROM arena_orders o
        LEFT JOIN arena_claims c ON c.id = o.claim_id
        LEFT JOIN arena_participants p ON p.handle = o.handle
        WHERE o.side = 'buy' AND o.status = 'filled' AND o.ret_acct IS NOT NULL
        GROUP BY o.code
        ORDER BY n DESC, avg_ret DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def tape(conn: sqlite3.Connection, handle: str | None = None, code: str | None = None, limit: int = 60) -> list[dict]:
    """Newest calls first. One row per structured name, or one row if the post had none."""
    if not _table(conn, "arena_posts"):
        return []
    clauses = []
    params: list = []
    if handle:
        clauses.append("p.handle = ?")
        params.append(handle)
    if code:
        clauses.append("c.code = ?")
        params.append(code)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT p.created_at, p.handle, p.text, p.permalink, p.stance AS post_stance,
               c.name, c.code, c.evidence, c.reject_reason, c.entry_date, c.stance AS claim_stance,
               o.status AS order_status, o.skip_reason, o.exit_reason, o.ret_acct, o.ret_pred
        FROM arena_posts p
        LEFT JOIN arena_claims c ON c.post_id = p.post_id
        LEFT JOIN arena_orders o ON o.claim_id = c.id AND o.side = 'buy'
        {where}
        ORDER BY p.created_at DESC, c.id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def orders_for(conn: sqlite3.Connection, handle: str) -> list[dict]:
    if not _table(conn, "arena_orders"):
        return []
    rows = conn.execute(
        """
        SELECT o.*, c.evidence, c.reject_reason AS claim_reject, p.text, p.permalink, p.deleted_at, p.created_at
        FROM arena_orders o
        LEFT JOIN arena_claims c ON c.id = o.claim_id
        LEFT JOIN arena_posts p ON p.post_id = c.post_id
        WHERE o.handle = ?
        ORDER BY o.entry_date, o.id
        """,
        (handle,),
    ).fetchall()
    return [dict(row) for row in rows]


def recent_claims(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    if not _table(conn, "arena_claims"):
        return []
    rows = conn.execute(
        """
        SELECT c.*, p.deleted_at, p.permalink, p.created_at
        FROM arena_claims c
        LEFT JOIN arena_posts p ON p.post_id = c.post_id
        ORDER BY c.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def locked_orders(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "arena_orders"):
        return []
    rows = conn.execute(
        """
        SELECT o.handle, o.code, o.side, o.entry_date, c.name, c.evidence
        FROM arena_orders o
        LEFT JOIN arena_claims c ON c.id = o.claim_id
        WHERE o.status = 'locked'
        ORDER BY o.entry_date, o.id
        """
    ).fetchall()
    return [dict(row) for row in rows]
