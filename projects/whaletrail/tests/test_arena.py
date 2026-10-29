"""Arena clock, fills, and the Grok contestant. No network, no X."""

from __future__ import annotations

from datetime import date, datetime

from whaletrail.arena.ledger import (
    ensure_participants,
    ingest,
    load_universe,
    settle_through,
)
from whaletrail.arena.report import leaderboard
from whaletrail.arena.rules import CN, canonical_code, shrinkage
from whaletrail.storage.schema import create_tables


def is_trading(day: date) -> bool:
    return day.weekday() < 5


def cn(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=CN)


def build(tmp_path):
    conn = create_tables(tmp_path / "arena.db")
    conn.row_factory = __import__("sqlite3").Row
    ensure_participants(
        conn,
        [
            {"handle": "alpha", "display_name": "甲", "kind": "kol", "joined_on": "2026-03-02"},
            {"handle": "grok", "display_name": "Grok", "kind": "grok", "joined_on": "2026-03-02"},
        ],
        cn(2026, 3, 1, 12),
    )
    conn.executemany(
        "INSERT INTO ashare_universe (code, name) VALUES (?, ?)",
        [("sz.000001", "平安银行"), ("sh.600000", "浦发银行"), ("sh.688111", "金山办公"), ("sz.002281", "光迅科技")],
    )
    conn.commit()
    return conn


def bar(conn, code, day, o, h, l, c, status=1, st=0):
    conn.execute(
        """
        INSERT INTO daily_kline (code, trade_date, open, high, low, close, tradestatus, is_st)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (code, day, o, h, l, c, status, st),
    )


def idx(conn, day, o, c):
    conn.execute(
        """
        INSERT INTO index_kline (code, name, trade_date, open, high, low, close)
        VALUES ('sh.000300', '沪深300', ?, ?, ?, ?, ?)
        """,
        (day, o, max(o, c), min(o, c), c),
    )


def sheet(**kwargs):
    base = {
        "handle": "alpha",
        "post_id": "p1",
        "created_at": "2026-03-02T08:00:00+08:00",
        "text": "明天开盘买平安银行。",
        "stance": "forward",
        "names": [{"code": "000001", "evidence": "平安银行"}],
    }
    base.update(kwargs)
    return base


def test_canonical_code_keeps_star_and_refuses_bse():
    assert canonical_code("688111") == ("sh.688111", "ok")
    assert canonical_code("SH.600000") == ("sh.600000", "ok")
    assert canonical_code("830001")[1] == "bse"
    assert canonical_code("莱美")[1] == "bad"


def test_forward_call_fills_at_open_and_exits_next_close(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(conn, sheet(), cn(2026, 3, 2, 9, 20), by_code, by_name, is_trading)
    assert result["orders"] == 1
    assert result["entry_date"] == "2026-03-02"
    bar(conn, "sz.000001", "2026-02-27", 20, 20, 20, 20)
    bar(conn, "sz.000001", "2026-03-02", 20, 21, 19.5, 21)
    bar(conn, "sz.000001", "2026-03-03", 21, 21.5, 20.5, 21)
    idx(conn, "2026-03-02", 4000, 4000)
    idx(conn, "2026-03-03", 4000, 4000)
    conn.commit()
    settle_through(conn, date(2026, 3, 3), is_trading)
    order = conn.execute("SELECT * FROM arena_orders WHERE side = 'buy'").fetchone()
    assert order["status"] == "filled"
    assert order["qty"] == 900
    assert order["exit_reason"] == "time"
    assert order["exit_date"] == "2026-03-03"
    assert abs(order["pnl"] - 844.56) < 0.02
    cash = conn.execute("SELECT cash FROM arena_participants WHERE handle = 'alpha'").fetchone()["cash"]
    assert abs(cash - 100844.56) < 0.02
    assert order["ret_pred"] > 0
    assert abs(order["excess"] - order["ret_pred"]) < 1e-9


def test_four_names_are_a_list_and_do_not_trade(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    names = [{"code": "000001", "evidence": "平安银行"}] * 4
    result = ingest(
        conn,
        sheet(post_id="list", text="平安银行 " * 4, names=names),
        cn(2026, 3, 2, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["orders"] == 0
    assert result["stance"] == "list"
    assert conn.execute("SELECT COUNT(*) AS n FROM arena_orders").fetchone()["n"] == 0


def test_late_capture_is_archived_and_not_traded(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    # After entry-day 15:30 settle cutoff, morning posts are late_capture.
    result = ingest(conn, sheet(post_id="late"), cn(2026, 3, 2, 15, 31), by_code, by_name, is_trading)
    assert result["late"] is True
    assert result["orders"] == 0
    post = conn.execute("SELECT late_capture FROM arena_posts").fetchone()
    assert post["late_capture"] == 1


def test_post_before_lock_may_lock_after_0925(tmp_path):
    """Offline catch-up: created_at ≤ 09:25, lock at 09:40 still same-day trade."""
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        sheet(post_id="catchup", created_at="2026-03-02T09:03:05+08:00"),
        cn(2026, 3, 2, 9, 40),
        by_code,
        by_name,
        is_trading,
    )
    assert result["late"] is False
    assert result["orders"] == 1
    assert result["entry_date"] == "2026-03-02"


def test_grok_same_lock_grace_as_kol(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        {
            "handle": "grok",
            "post_id": "grok:2026-03-02",
            "created_at": "2026-03-02T09:20:00+08:00",
            "text": "截至上周五收盘，明天开盘只买平安银行，不追涨停。这份说明写给归档，字数超过四十。",
            "stance": "forward",
            "names": [{"code": "000001", "evidence": "平安银行"}],
        },
        cn(2026, 3, 2, 10, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["ok"] is True
    assert result["late"] is False
    assert result["orders"] == 1
    assert result["entry_date"] == "2026-03-02"


def test_daily_cap_and_duplicate_code(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    text = "平安银行 浦发银行 金山办公 光迅科技"
    first = sheet(
        post_id="a",
        text=text,
        names=[
            {"code": "000001", "evidence": "平安银行"},
            {"code": "600000", "evidence": "浦发银行"},
            {"code": "688111", "evidence": "金山办公"},
        ],
    )
    second = sheet(
        post_id="b",
        text=text,
        names=[
            {"code": "002281", "evidence": "光迅科技"},
            {"code": "000001", "evidence": "平安银行"},
        ],
    )
    now = cn(2026, 3, 2, 9, 10)
    assert ingest(conn, first, now, by_code, by_name, is_trading)["orders"] == 3
    result = ingest(conn, second, now, by_code, by_name, is_trading)
    assert result["orders"] == 0
    assert "daily_cap" in result["rejects"]


def test_limit_up_does_not_fill(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    bar(conn, "sz.000001", "2026-02-27", 10, 10, 10, 10)
    bar(conn, "sz.000001", "2026-03-02", 11, 11, 11, 11)
    conn.commit()
    settle_through(conn, date(2026, 3, 2), is_trading)
    order = conn.execute("SELECT status, skip_reason FROM arena_orders").fetchone()
    assert order["status"] == "skipped"
    assert order["skip_reason"] == "limit_up"
    cash = conn.execute("SELECT cash FROM arena_participants WHERE handle = 'alpha'").fetchone()["cash"]
    assert cash == 100000


def test_stop_touch_sells_at_stop_while_prediction_uses_the_close(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    bar(conn, "sz.000001", "2026-02-27", 20, 20, 20, 20)
    bar(conn, "sz.000001", "2026-03-02", 20, 20.5, 19.8, 20.2)
    # stop is 18.80. Next day gaps above it, trades through it, and closes higher.
    bar(conn, "sz.000001", "2026-03-03", 19.5, 20.5, 18.5, 20.4)
    idx(conn, "2026-03-02", 4000, 4000)
    idx(conn, "2026-03-03", 4000, 4000)
    conn.commit()
    settle_through(conn, date(2026, 3, 3), is_trading)
    order = conn.execute("SELECT * FROM arena_orders WHERE side = 'buy'").fetchone()
    assert order["exit_reason"] == "stop_touch"
    assert abs(order["exit_raw"] - 18.80) < 0.001
    assert order["ret_acct"] < order["ret_pred"]


def test_one_word_limit_down_defers_the_sale(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    bar(conn, "sz.000001", "2026-02-27", 20, 20, 20, 20)
    bar(conn, "sz.000001", "2026-03-02", 20, 20, 19.5, 20)
    bar(conn, "sz.000001", "2026-03-03", 18, 18, 18, 18)  # 20 * 0.9 = 18
    bar(conn, "sz.000001", "2026-03-04", 19, 19.4, 18.9, 19.2)
    conn.commit()
    settle_through(conn, date(2026, 3, 3), is_trading)
    order = conn.execute("SELECT exit_date, ret_pred FROM arena_orders WHERE side = 'buy'").fetchone()
    assert order["exit_date"] is None
    assert order["ret_pred"] is not None and order["ret_pred"] < 0
    settle_through(conn, date(2026, 3, 4), is_trading)
    order = conn.execute("SELECT exit_date, exit_reason FROM arena_orders WHERE side = 'buy'").fetchone()
    assert order["exit_date"] == "2026-03-04"
    assert order["exit_reason"] == "time"


def test_exit_call_sells_the_open(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    ingest(
        conn,
        sheet(
            post_id="sell1",
            created_at="2026-03-03T08:30:00+08:00",
            text="明天开盘卖掉平安银行。",
            stance="exit",
            names=[{"code": "000001", "evidence": "平安银行"}],
        ),
        cn(2026, 3, 3, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    bar(conn, "sz.000001", "2026-02-27", 20, 20, 20, 20)
    bar(conn, "sz.000001", "2026-03-02", 20, 20.4, 19.8, 20.2)
    bar(conn, "sz.000001", "2026-03-03", 20.5, 21, 20.2, 20.8)
    conn.commit()
    settle_through(conn, date(2026, 3, 3), is_trading)
    order = conn.execute("SELECT exit_reason, exit_raw FROM arena_orders WHERE side = 'buy'").fetchone()
    assert order["exit_reason"] == "exit_call"
    assert abs(order["exit_raw"] - 20.5) < 0.001


def test_evidence_must_be_a_substring(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        sheet(names=[{"code": "000001", "evidence": "不在正文里"}]),
        cn(2026, 3, 2, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["orders"] == 0
    assert result["rejects"] == ["evidence"]


def test_grok_sheet_uses_the_same_ledger(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    text = "截至上周五收盘，明天开盘只买平安银行，不追涨停。这份说明写给归档，字数超过四十。"
    result = ingest(
        conn,
        {
            "handle": "grok",
            "post_id": "grok:2026-03-02",
            "created_at": "2026-03-02T08:40:00+08:00",
            "text": text,
            "stance": "forward",
            "names": [{"code": "000001", "evidence": "平安银行"}],
        },
        cn(2026, 3, 2, 8, 50),
        by_code,
        by_name,
        is_trading,
    )
    assert result["orders"] == 1
    bad = ingest(
        conn,
        {
            "handle": "grok",
            "post_id": "grok:1999-01-01",
            "created_at": "2026-03-02T08:41:00+08:00",
            "text": text,
            "stance": "forward",
            "names": [{"code": "600000", "evidence": "平安银行"}],
        },
        cn(2026, 3, 2, 8, 51),
        by_code,
        by_name,
        is_trading,
    )
    assert bad["ok"] is False


def test_recap_after_the_close_targets_the_next_open_but_does_not_trade(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        sheet(post_id="recap", created_at="2026-03-02T18:00:00+08:00", stance="recap", names=[]),
        cn(2026, 3, 2, 18, 10),
        by_code,
        by_name,
        is_trading,
    )
    assert result["orders"] == 0
    assert result["entry_date"] == "2026-03-03"
    assert result["late"] is False


def test_shrinkage_pulls_a_short_sample_toward_zero():
    assert shrinkage(0.10, 8) == 0.05
    assert shrinkage(0.10, 0) is None


def test_leaderboard_hides_short_samples_from_the_main_seat(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    bar(conn, "sz.000001", "2026-02-27", 20, 20, 20, 20)
    bar(conn, "sz.000001", "2026-03-02", 20, 21, 19.5, 21)
    bar(conn, "sz.000001", "2026-03-03", 21, 21.5, 20.5, 21)
    idx(conn, "2026-03-02", 4000, 4000)
    idx(conn, "2026-03-03", 4000, 4000)
    conn.commit()
    settle_through(conn, date(2026, 3, 3), is_trading)
    board = {row["handle"]: row for row in leaderboard(conn)}
    assert board["alpha"]["seat"] == "观察席"
    assert board["alpha"]["n"] == 1
    assert board["grok"]["n"] == 0
    assert board["grok"]["seat"] == "观察席"
    assert board["grok"]["equity"] == 100000


def test_new_account_joins_when_the_call_is_real(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        sheet(handle="newbie", post_id="new1", display_name="新人"),
        cn(2026, 3, 2, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["ok"] is True
    assert result["orders"] == 1
    row = conn.execute("SELECT status, cash, joined_on FROM arena_participants WHERE handle='newbie'").fetchone()
    assert row["status"] == "active"
    assert row["cash"] == 100000
    assert row["joined_on"] == "2026-03-02"


def test_hundredth_active_account_blocks_the_next_one(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    for i in range(98):
        conn.execute(
            """
            INSERT INTO arena_participants
                (handle, display_name, kind, joined_on, cash, created_at, status)
            VALUES (?, ?, 'kol', '2026-03-02', 100000, '2026-03-01', 'active')
            """,
            (f"h{i}", f"h{i}"),
        )
    conn.commit()
    result = ingest(
        conn,
        sheet(handle="newbie", post_id="new1"),
        cn(2026, 3, 2, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["ok"] is False
    assert "100" in result["error"]


def test_eliminated_account_does_not_return(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    conn.execute("UPDATE arena_participants SET status='eliminated', eliminated_reason='bust' WHERE handle='alpha'")
    conn.commit()
    result = ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    assert result["ok"] is False
    assert "出局" in result["error"]


def test_flat_account_below_one_slot_is_out(tmp_path):
    conn = build(tmp_path)
    conn.execute("UPDATE arena_participants SET cash=1000 WHERE handle='alpha'")
    conn.commit()
    settle_through(conn, date(2026, 3, 2), is_trading)
    row = conn.execute("SELECT status, eliminated_reason FROM arena_participants WHERE handle='alpha'").fetchone()
    assert row["status"] == "eliminated"
    assert row["eliminated_reason"] == "bust"


def test_ten_silent_sessions_benches_a_flat_account(tmp_path):
    conn = build(tmp_path)
    conn.execute(
        """
        INSERT INTO arena_posts (post_id, handle, created_at, captured_at, text, stance)
        VALUES ('old1', 'alpha', '2026-03-02T08:00:00+08:00', '2026-03-02T08:30:00+08:00', 'x', 'recap')
        """
    )
    conn.commit()
    settle_through(conn, date(2026, 3, 16), is_trading)
    row = conn.execute("SELECT status, eliminated_reason FROM arena_participants WHERE handle='alpha'").fetchone()
    assert row["status"] == "eliminated"
    assert row["eliminated_reason"] == "stale"


def test_roster_file_loads():
    from whaletrail.arena.ledger import load_roster
    from whaletrail.arena.paths import roster_path

    people = load_roster(roster_path())
    handles = [p["handle"] for p in people]
    assert handles[0] == "grok"
    assert "fighterpromoter" in handles
    assert "edgarzepedatava" in handles
    assert len(handles) == 22


def _status(conn, handle="alpha"):
    return conn.execute(
        "SELECT status, eliminated_on, eliminated_reason, joined_on, cash FROM arena_participants WHERE handle=?",
        (handle,),
    ).fetchone()


def test_no_post_account_is_not_removed_after_four_sessions(tmp_path):
    conn = build(tmp_path)
    settle_through(conn, date(2026, 3, 6), is_trading)  # 3/3..3/6 = 4 sessions
    assert _status(conn)["status"] == "active"


def test_no_post_account_is_removed_after_five_sessions(tmp_path):
    conn = build(tmp_path)
    settle_through(conn, date(2026, 3, 9), is_trading)  # 3/3..3/9 = 5 sessions
    row = _status(conn)
    assert row["status"] == "removed"
    assert row["eliminated_reason"] == "no_posts"
    assert row["eliminated_on"] == "2026-03-09"


def test_account_with_a_post_is_not_removed(tmp_path):
    conn = build(tmp_path)
    by_code, by_name = load_universe(conn)
    assert ingest(conn, sheet(text="收盘复盘平安银行。", stance="recap", names=[]), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)["ok"]
    settle_through(conn, date(2026, 3, 9), is_trading)
    assert _status(conn)["status"] == "active"


def test_removed_does_not_count_as_active(tmp_path):
    from whaletrail.arena.ledger import _active_count

    conn = build(tmp_path)
    before = _active_count(conn)
    conn.execute("UPDATE arena_participants SET status='removed' WHERE handle='alpha'")
    conn.commit()
    assert _active_count(conn) == before - 1


def test_removed_account_can_rejoin_with_fresh_start(tmp_path):
    conn = build(tmp_path)
    settle_through(conn, date(2026, 3, 9), is_trading)
    assert _status(conn)["status"] == "removed"
    by_code, by_name = load_universe(conn)
    result = ingest(
        conn,
        sheet(post_id="back1", created_at="2026-03-10T08:00:00+08:00"),
        cn(2026, 3, 10, 9, 0),
        by_code,
        by_name,
        is_trading,
    )
    assert result["ok"] is True
    row = _status(conn)
    assert row["status"] == "active"
    assert row["joined_on"] == "2026-03-10"
    assert row["cash"] == 100000
    assert row["eliminated_reason"] is None


def test_removed_account_rejoin_respects_the_cap(tmp_path):
    conn = build(tmp_path)
    conn.execute("UPDATE arena_participants SET status='removed' WHERE handle='alpha'")
    for i in range(99):
        conn.execute(
            """
            INSERT INTO arena_participants
                (handle, display_name, kind, joined_on, cash, created_at, status)
            VALUES (?, ?, 'kol', '2026-03-02', 100000, '2026-03-01', 'active')
            """,
            (f"h{i}", f"h{i}"),
        )
    conn.commit()
    by_code, by_name = load_universe(conn)
    result = ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    assert result["ok"] is False
    assert "100" in result["error"]
    assert _status(conn)["status"] == "removed"


def test_eliminated_still_cannot_rejoin_after_removal_rule(tmp_path):
    conn = build(tmp_path)
    conn.execute("UPDATE arena_participants SET status='eliminated', eliminated_reason='stale' WHERE handle='alpha'")
    conn.commit()
    by_code, by_name = load_universe(conn)
    result = ingest(conn, sheet(), cn(2026, 3, 2, 9, 0), by_code, by_name, is_trading)
    assert result["ok"] is False
    assert "出局" in result["error"]
