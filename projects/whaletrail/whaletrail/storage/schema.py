"""SQLite schema for the WhaleTrail paper trading system.

Tables
------
- ``runs``          — one row per backtest run; stores parameters and summary metrics.
- ``trades``        — every fill (executed trade) during a run.
- ``portfolio_snapshots`` — daily equity/cash/positions snapshots.
- ``quote_snapshots`` — tvscreener watchlist snapshots.
- ``daily_kline``   — baostock A-share daily bars (similarity + extras).
- ``ashare_universe`` — stock basic (ipo/status).
- ``ashare_industry`` — 申万一级行业.
- ``ashare_index_constituents`` — sz50 / hs300 / zz500 membership.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name   TEXT    NOT NULL,
    symbols         TEXT    NOT NULL,  -- JSON array of symbol strings
    start_date      TEXT    NOT NULL,  -- YYYY-MM-DD
    end_date        TEXT    NOT NULL,  -- YYYY-MM-DD
    initial_cash    REAL    NOT NULL,
    final_equity    REAL,
    metrics_json    TEXT,              -- JSON blob with all performance metrics
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK(side IN ('buy', 'sell')),
    quantity        REAL    NOT NULL,
    price           REAL    NOT NULL,
    commission      REAL    NOT NULL DEFAULT 0.0,
    timestamp       TEXT    NOT NULL,  -- ISO-8601 or YYYY-MM-DD
    pnl             REAL              -- realised P&L for closing trades
);

CREATE INDEX IF NOT EXISTS idx_trades_run_id ON trades(run_id);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    date            TEXT    NOT NULL,  -- YYYY-MM-DD
    equity          REAL    NOT NULL,
    cash            REAL    NOT NULL,
    positions_json  TEXT    NOT NULL   -- JSON blob: {symbol: {qty, avg_cost, market_value}}
);


CREATE TABLE IF NOT EXISTS quote_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tv_symbol       TEXT    NOT NULL,
    local_name      TEXT,
    yahoo_symbol    TEXT,
    asset_class     TEXT,
    exchange        TEXT,
    close           REAL,
    open            REAL,
    high            REAL,
    low             REAL,
    change_percent  REAL,
    volume          REAL,
    rsi             REAL,
    sma20           REAL,
    sma50           REAL,
    sma200          REAL,
    recommend_all   REAL,
    description     TEXT,
    source          TEXT    NOT NULL DEFAULT tvscreener,
    endpoint        TEXT    NOT NULL DEFAULT global,
    raw_json        TEXT,
    timestamp       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_run_id ON portfolio_snapshots(run_id);


CREATE TABLE IF NOT EXISTS daily_kline (
    code            TEXT    NOT NULL,  -- baostock code: sh.600690
    trade_date      TEXT    NOT NULL,  -- YYYY-MM-DD
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          REAL,
    amount          REAL,
    turn            REAL,              -- 换手率 %；停牌为空
    tradestatus     INTEGER,           -- 1 正常 / 0 停牌
    pct_chg         REAL,
    is_st           INTEGER,           -- 1 ST / 0 否
    pe_ttm          REAL,
    pb_mrq          REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_kline_date ON daily_kline(trade_date);

CREATE TABLE IF NOT EXISTS ashare_universe (
    code            TEXT    PRIMARY KEY,  -- baostock code
    name            TEXT,
    ipo_date        TEXT,
    out_date        TEXT,
    stock_type      TEXT,              -- baostock type: 1=股票
    status          TEXT               -- 1=上市 0=退市
);

CREATE TABLE IF NOT EXISTS ashare_industry (
    code            TEXT    PRIMARY KEY,
    name            TEXT,
    industry        TEXT,              -- 申万一级
    classification  TEXT,              -- e.g. 申万一级行业
    update_date     TEXT
);

CREATE TABLE IF NOT EXISTS ashare_index_constituents (
    index_id        TEXT    NOT NULL,  -- sz50 / hs300 / zz500
    code            TEXT    NOT NULL,
    name            TEXT,
    update_date     TEXT,
    PRIMARY KEY (index_id, code)
);
"""


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]
) -> None:
    """Add missing columns on a pre-existing table (CREATE IF NOT EXISTS is a no-op)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:
        return
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def create_tables(db_path: str | Path) -> sqlite3.Connection:
    """Create (or upgrade) all tables in the SQLite database at *db_path*.

    Parameters
    ----------
    db_path : str or Path
        Filesystem path to the SQLite database file.

    Returns
    -------
    sqlite3.Connection
        An open connection to the database.  The caller is responsible for
        closing it when done.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    _ensure_columns(conn, "quote_snapshots", [("open", "REAL"), ("high", "REAL"), ("low", "REAL")])
    _ensure_columns(
        conn,
        "daily_kline",
        [
            ("turn", "REAL"),
            ("tradestatus", "INTEGER"),
            ("pct_chg", "REAL"),
            ("is_st", "INTEGER"),
            ("pe_ttm", "REAL"),
            ("pb_mrq", "REAL"),
        ],
    )
    _ensure_columns(
        conn,
        "ashare_universe",
        [
            ("ipo_date", "TEXT"),
            ("out_date", "TEXT"),
            ("stock_type", "TEXT"),
            ("status", "TEXT"),
        ],
    )
    conn.commit()
    return conn
