"""Repository — high-level CRUD wrapper around the SQLite database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from whaletrail.storage.schema import create_tables


class Repository:
    """Persist and query backtest results.

    Parameters
    ----------
    db_path : str or Path
        Path to the SQLite database.  Created automatically if it does not
        exist.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = create_tables(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn


    def save_quote_snapshots(self, rows: list[dict]) -> int:
        now = datetime.now().isoformat()
        batch = [
            (
                r.get("tv_symbol", ""),
                r.get("local_name"),
                r.get("yahoo_symbol"),
                r.get("asset_class"),
                r.get("exchange"),
                r.get("open"),
                r.get("high"),
                r.get("low"),
                r.get("close"),
                r.get("change_percent"),
                r.get("volume"),
                r.get("rsi"),
                r.get("sma20"),
                r.get("sma50"),
                r.get("sma200"),
                r.get("recommend_all"),
                r.get("description"),
                r.get("source", "tvscreener"),
                r.get("endpoint", "global"),
                json.dumps(r.get("raw"), ensure_ascii=False) if r.get("raw") else None,
                r.get("timestamp", now),
            )
            for r in rows
        ]
        cur = self.conn.executemany(
            """INSERT INTO quote_snapshots
               (tv_symbol, local_name, yahoo_symbol, asset_class, exchange,
                open, high, low, close, change_percent, volume, rsi, sma20, sma50, sma200,
                recommend_all, description, source, endpoint, raw_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(
        self,
        strategy_name: str,
        symbols: list[str],
        start: str,
        end: str,
        initial_cash: float,
        final_equity: float,
        metrics: dict[str, Any],
    ) -> int:
        """Persist a completed backtest run and return its auto-generated ID."""
        cur = self.conn.execute(
            """INSERT INTO runs
               (strategy_name, symbols, start_date, end_date,
                initial_cash, final_equity, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy_name,
                json.dumps(symbols, ensure_ascii=False),
                str(start),
                str(end),
                initial_cash,
                final_equity,
                json.dumps(metrics, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_trade(self, run_id: int, trade: dict[str, Any]) -> int:
        """Persist a single executed trade."""
        cur = self.conn.execute(
            """INSERT INTO trades
               (run_id, symbol, side, quantity, price, commission, timestamp, pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                trade.get("symbol", ""),
                trade.get("side", ""),
                trade.get("quantity", 0.0),
                trade.get("price", 0.0),
                trade.get("commission", 0.0),
                str(trade.get("timestamp", "")),
                trade.get("pnl"),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_snapshot(
        self,
        run_id: int,
        date: str,
        equity: float,
        cash: float,
        positions: dict[str, Any],
    ) -> int:
        """Persist a daily portfolio snapshot."""
        cur = self.conn.execute(
            """INSERT INTO portfolio_snapshots
               (run_id, date, equity, cash, positions_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                run_id,
                str(date),
                equity,
                cash,
                json.dumps(positions, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_run(self, run_id: int) -> Optional[dict[str, Any]]:
        """Return a single run record as a dict, or *None*."""
        row = self.conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row, "runs")

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent runs, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r, "runs") for r in rows]

    # ------------------------------------------------------------------
    # Quote snapshots
    # ------------------------------------------------------------------

    def latest_quote_snapshots(self) -> list[dict[str, Any]]:
        """Return the most recent quote snapshot per symbol, newest first.

        Uses the highest row id per ``tv_symbol`` so multiple fetches on
        the same day do not produce duplicates.
        """
        rows = self.conn.execute(
            """SELECT * FROM quote_snapshots
               WHERE id IN (
                   SELECT MAX(id) FROM quote_snapshots GROUP BY tv_symbol
               )
               ORDER BY timestamp DESC"""
        ).fetchall()
        return [self._row_to_dict(r, "quote_snapshots") for r in rows]

    def latest_quote_timestamp(self) -> Optional[str]:
        """Return the timestamp of the newest quote snapshot, or *None*."""
        row = self.conn.execute(
            "SELECT MAX(timestamp) FROM quote_snapshots"
        ).fetchone()
        return row[0] if row and row[0] else None

    # ------------------------------------------------------------------
    # A-share daily bars (baostock universe, chart-similarity scan)
    # ------------------------------------------------------------------
    def save_daily_bars(self, rows: list[dict]) -> int:
        """Bulk-upsert daily bars into ``daily_kline``. Returns rows written."""
        batch = [
            (
                r.get("code", ""),
                r.get("trade_date", ""),
                r.get("open"),
                r.get("high"),
                r.get("low"),
                r.get("close"),
                r.get("volume"),
                r.get("amount"),
                r.get("turn"),
                r.get("tradestatus"),
                r.get("pct_chg"),
                r.get("is_st"),
                r.get("pe_ttm"),
                r.get("pb_mrq"),
            )
            for r in rows
        ]
        cur = self.conn.executemany(
            """INSERT OR REPLACE INTO daily_kline
               (code, trade_date, open, high, low, close, volume, amount,
                turn, tradestatus, pct_chg, is_st, pe_ttm, pb_mrq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        self.conn.commit()
        return cur.rowcount

    def save_universe(self, rows: list[dict]) -> int:
        """Upsert stock-basic rows into ``ashare_universe``."""
        batch = [
            (
                r.get("code", ""),
                r.get("name") or "",
                r.get("ipo_date"),
                r.get("out_date"),
                r.get("stock_type"),
                r.get("status"),
            )
            for r in rows
        ]
        cur = self.conn.executemany(
            """INSERT OR REPLACE INTO ashare_universe
               (code, name, ipo_date, out_date, stock_type, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            batch,
        )
        self.conn.commit()
        return cur.rowcount

    def save_industry(self, rows: list[dict]) -> int:
        """Replace the 申万一级 map with the latest snapshot."""
        self.conn.execute("DELETE FROM ashare_industry")
        batch = [
            (
                r.get("code", ""),
                r.get("name") or "",
                r.get("industry"),
                r.get("classification"),
                r.get("update_date"),
            )
            for r in rows
        ]
        cur = self.conn.executemany(
            """INSERT INTO ashare_industry
               (code, name, industry, classification, update_date)
               VALUES (?, ?, ?, ?, ?)""",
            batch,
        )
        self.conn.commit()
        return cur.rowcount

    def save_index_constituents(self, index_id: str, rows: list[dict]) -> int:
        """Replace membership for one index (``sz50`` / ``hs300`` / ``zz500``)."""
        self.conn.execute(
            "DELETE FROM ashare_index_constituents WHERE index_id = ?", (index_id,)
        )
        batch = [
            (
                index_id,
                r.get("code", ""),
                r.get("name") or "",
                r.get("update_date"),
            )
            for r in rows
        ]
        cur = self.conn.executemany(
            """INSERT INTO ashare_index_constituents
               (index_id, code, name, update_date)
               VALUES (?, ?, ?, ?)""",
            batch,
        )
        self.conn.commit()
        return cur.rowcount

    def universe_names(self) -> dict[str, str]:
        """Return ``{code: name}`` for the persisted A-share universe."""
        rows = self.conn.execute(
            "SELECT code, name FROM ashare_universe"
        ).fetchall()
        return {r["code"]: r["name"] for r in rows}

    def listed_universe(self) -> list[tuple[str, str]]:
        """Listed (or pre-migration) stocks as ``(code, name)``."""
        rows = self.conn.execute(
            """SELECT code, name FROM ashare_universe
               WHERE status = '1' OR status IS NULL
               ORDER BY code"""
        ).fetchall()
        return [(r["code"], r["name"] or "") for r in rows]

    def industry_map(self) -> dict[str, str]:
        """Return ``{code: industry}`` for rows that have a non-empty industry."""
        rows = self.conn.execute(
            "SELECT code, industry FROM ashare_industry WHERE industry IS NOT NULL AND industry != ''"
        ).fetchall()
        return {r["code"]: r["industry"] for r in rows}

    def index_members(self, index_id: str) -> list[str]:
        """Return baostock codes currently stored for *index_id*."""
        rows = self.conn.execute(
            "SELECT code FROM ashare_index_constituents WHERE index_id = ? ORDER BY code",
            (index_id,),
        ).fetchall()
        return [r["code"] for r in rows]

    def daily_last_date(self, code: str) -> Optional[str]:
        """Return the newest ``trade_date`` persisted for *code*, or *None*."""
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM daily_kline WHERE code = ?", (code,)
        ).fetchone()
        return row[0] if row and row[0] else None

    def daily_last_dates(self) -> dict[str, str]:
        """Return ``{code: newest trade_date}`` for every stored symbol."""
        rows = self.conn.execute(
            "SELECT code, MAX(trade_date) AS last FROM daily_kline GROUP BY code"
        ).fetchall()
        return {r["code"]: r["last"] for r in rows if r["last"]}

    def daily_extras_gaps(self) -> dict[str, str]:
        """Return ``{code: earliest trade_date}`` still missing extra fields.

        Pre-migration rows have ``tradestatus IS NULL``. Halted days after a
        proper refill have ``tradestatus = 0`` and a null ``turn``, so they
        are not treated as gaps.
        """
        rows = self.conn.execute(
            """SELECT code, MIN(trade_date) AS first
               FROM daily_kline
               WHERE tradestatus IS NULL
               GROUP BY code"""
        ).fetchall()
        return {r["code"]: r["first"] for r in rows if r["first"]}

    def daily_closes(
        self,
        start: str | None = None,
        end: str | None = None,
        codes: list[str] | None = None,
    ) -> dict[str, list[float]]:
        """Return ``{code: [close...]}`` ordered by ``trade_date``.

        Used by the chart-similarity scan: fetch every symbol's close series
        over a date window in one query, then rank by DTW.
        """
        query = "SELECT code, close FROM daily_kline"
        conds: list[str] = []
        params: list = []
        if start:
            conds.append("trade_date >= ?")
            params.append(start)
        if end:
            conds.append("trade_date <= ?")
            params.append(end)
        if codes:
            conds.append(f"code IN ({','.join('?' * len(codes))})")
            params.extend(codes)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY trade_date"

        out: dict[str, list[float]] = {}
        for r in self.conn.execute(query, params):
            out.setdefault(r["code"], []).append(float(r["close"]))
        return out

    def daily_bars(
        self,
        start: str | None = None,
        end: str | None = None,
        codes: list[str] | None = None,
    ) -> dict[str, dict[str, list]]:
        """Return ``{code: {column: [values...]}}`` ordered by ``trade_date``.

        Columns: trade_date, open, high, low, close, volume, turn,
        tradestatus, is_st.  Halt days keep a row (volume 0, turn None).
        Used by the fused similarity scan so price / turnover / chips share
        the same calendar slice.
        """
        query = (
            "SELECT code, trade_date, open, high, low, close, volume, "
            "turn, tradestatus, is_st FROM daily_kline"
        )
        conds: list[str] = []
        params: list = []
        if start:
            conds.append("trade_date >= ?")
            params.append(start)
        if end:
            conds.append("trade_date <= ?")
            params.append(end)
        if codes:
            conds.append(f"code IN ({','.join('?' * len(codes))})")
            params.extend(codes)
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY trade_date"

        empty = {
            "trade_date": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
            "turn": [],
            "tradestatus": [],
            "is_st": [],
        }
        out: dict[str, dict[str, list]] = {}
        for r in self.conn.execute(query, params):
            rec = out.get(r["code"])
            if rec is None:
                rec = {k: [] for k in empty}
                out[r["code"]] = rec
            rec["trade_date"].append(r["trade_date"])
            rec["open"].append(r["open"])
            rec["high"].append(r["high"])
            rec["low"].append(r["low"])
            rec["close"].append(r["close"])
            rec["volume"].append(r["volume"])
            rec["turn"].append(r["turn"])
            rec["tradestatus"].append(r["tradestatus"])
            rec["is_st"].append(r["is_st"])
        return out

    def daily_ohlc(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict]:
        """Return OHLC rows for *code* ordered by ``trade_date``.

        Used by the similar-stock template K chart. Empty list on miss —
        callers must not invent bars. Calendar is whatever was stored from
        baostock (exchange sessions), not filled natural days.
        """
        query = (
            "SELECT trade_date, open, high, low, close, volume, turn "
            "FROM daily_kline WHERE code = ?"
        )
        params: list = [code]
        if start:
            query += " AND trade_date >= ?"
            params.append(start)
        if end:
            query += " AND trade_date <= ?"
            params.append(end)
        query += " ORDER BY trade_date"
        rows: list[dict] = []
        for r in self.conn.execute(query, params):
            rows.append(
                {
                    "trade_date": r["trade_date"],
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r["volume"],
                    "turn": r["turn"],
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, table: str) -> dict[str, Any]:
        """Convert a sqlite3.Row to a plain dict with JSON fields decoded."""
        d = dict(row)
        if table == "runs":
            d["symbols"] = json.loads(d.get("symbols", "[]") or "[]")
            metrics_raw = d.get("metrics_json")
            d["metrics"] = json.loads(metrics_raw) if metrics_raw else {}
            d.pop("metrics_json", None)
        elif table == "quote_snapshots":
            raw = d.get("raw_json")
            if raw:
                try:
                    d["raw"] = json.loads(raw)
                except (TypeError, ValueError):
                    d["raw"] = raw
            d.pop("raw_json", None)
        return d
