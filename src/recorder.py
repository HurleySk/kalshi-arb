"""
DataRecorder — SQLite-backed recorder for bot sessions, signals, executions,
fills, balances, and orderbook snapshots.

Usage:
    rec = DataRecorder("/path/to/bot.db")   # live recording
    rec = DataRecorder(None)                 # no-op (disabled)

All record_* methods silently no-op when the recorder is disabled or no
session is active.  Writes are committed after each insert for durability.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time  REAL    NOT NULL,
    end_time    REAL,
    config_json TEXT
);
"""

_CREATE_SIGNAL_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS signal_evaluations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    ts             REAL    NOT NULL,
    event_ticker   TEXT    NOT NULL,
    strategy       TEXT    NOT NULL,
    outcome        TEXT    NOT NULL,
    reject_reason  TEXT,
    bid_sum        REAL,
    ask_sum        REAL,
    profit_pct     REAL,
    exposure_ratio REAL,
    legs_json      TEXT,
    metadata_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_se_session   ON signal_evaluations(session_id);
CREATE INDEX IF NOT EXISTS idx_se_event     ON signal_evaluations(event_ticker);
CREATE INDEX IF NOT EXISTS idx_se_strategy  ON signal_evaluations(strategy);
CREATE INDEX IF NOT EXISTS idx_se_ts        ON signal_evaluations(ts);
"""

_CREATE_EXECUTIONS = """
CREATE TABLE IF NOT EXISTS executions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    ts             REAL    NOT NULL,
    event_ticker   TEXT    NOT NULL,
    strategy       TEXT    NOT NULL,
    result         TEXT    NOT NULL,
    legs_json      TEXT,
    fill_details_json TEXT,
    unwind_cost    REAL
);
CREATE INDEX IF NOT EXISTS idx_ex_session  ON executions(session_id);
CREATE INDEX IF NOT EXISTS idx_ex_event    ON executions(event_ticker);
CREATE INDEX IF NOT EXISTS idx_ex_ts       ON executions(ts);
"""

_CREATE_FILLS = """
CREATE TABLE IF NOT EXISTS fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(id),
    ts           REAL    NOT NULL,
    ticker       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    price        REAL    NOT NULL,
    quantity     INTEGER NOT NULL,
    realized_pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_fills_session ON fills(session_id);
CREATE INDEX IF NOT EXISTS idx_fills_ticker  ON fills(ticker);
CREATE INDEX IF NOT EXISTS idx_fills_ts      ON fills(ts);
"""

_CREATE_BALANCES = """
CREATE TABLE IF NOT EXISTS balances (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES sessions(id),
    ts               REAL    NOT NULL,
    cash_cents       INTEGER NOT NULL,
    portfolio_cents  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bal_session ON balances(session_id);
CREATE INDEX IF NOT EXISTS idx_bal_ts      ON balances(ts);
"""

_CREATE_ORDERBOOK_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    ts             REAL    NOT NULL,
    event_ticker   TEXT    NOT NULL,
    market_ticker  TEXT    NOT NULL,
    yes_bids_json  TEXT,
    no_bids_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_session ON orderbook_snapshots(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_market  ON orderbook_snapshots(market_ticker);
CREATE INDEX IF NOT EXISTS idx_obs_ts      ON orderbook_snapshots(ts);
"""


class DataRecorder:
    """SQLite-backed recorder.  Pass db_path=None for a disabled no-op instance."""

    def __init__(self, db_path: str | None) -> None:
        self._enabled = db_path is not None
        self._session_id: int | None = None
        self._conn: sqlite3.Connection | None = None

        if self._enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        assert self._conn is not None
        for ddl in (
            _CREATE_SESSIONS,
            _CREATE_SIGNAL_EVALUATIONS,
            _CREATE_EXECUTIONS,
            _CREATE_FILLS,
            _CREATE_BALANCES,
            _CREATE_ORDERBOOK_SNAPSHOTS,
        ):
            # Each DDL block may contain multiple statements; executescript
            # handles semicolon separation and commits automatically.
            self._conn.executescript(ddl)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self, config: dict) -> int | None:
        """Open a new recording session.  Returns session id, or None if disabled."""
        if not self._enabled:
            return None
        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO sessions (start_time, end_time, config_json) VALUES (?, NULL, ?)",
            (time.time(), json.dumps(config)),
        )
        self._conn.commit()
        self._session_id = cur.lastrowid
        return self._session_id

    def end_session(self) -> None:
        """Close the current session by recording end_time."""
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            "UPDATE sessions SET end_time = ? WHERE id = ?",
            (time.time(), self._session_id),
        )
        self._conn.commit()
        self._session_id = None

    # ------------------------------------------------------------------
    # Record helpers (all guard on _enabled and _session_id)
    # ------------------------------------------------------------------

    def record_signal(
        self,
        *,
        event_ticker: str,
        strategy: str,
        outcome: str,
        reject_reason: str | None,
        bid_sum: float | None,
        ask_sum: float | None,
        profit_pct: float | None,
        exposure_ratio: float | None,
        legs: list[dict[str, Any]] | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO signal_evaluations
                (session_id, ts, event_ticker, strategy, outcome, reject_reason,
                 bid_sum, ask_sum, profit_pct, exposure_ratio, legs_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._session_id,
                time.time(),
                event_ticker,
                strategy,
                outcome,
                reject_reason,
                bid_sum,
                ask_sum,
                profit_pct,
                exposure_ratio,
                json.dumps(legs) if legs is not None else None,
                json.dumps(metadata) if metadata is not None else None,
            ),
        )
        self._conn.commit()

    def record_execution(
        self,
        *,
        event_ticker: str,
        strategy: str,
        legs: list[dict[str, Any]],
        result: str,
        fill_details: dict[str, Any] | None,
        unwind_cost: float | None,
    ) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO executions
                (session_id, ts, event_ticker, strategy, result,
                 legs_json, fill_details_json, unwind_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._session_id,
                time.time(),
                event_ticker,
                strategy,
                result,
                json.dumps(legs),
                json.dumps(fill_details) if fill_details is not None else None,
                unwind_cost,
            ),
        )
        self._conn.commit()

    def record_fill(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        price: float,
        quantity: float,
        realized_pnl: float | None,
    ) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO fills
                (session_id, ts, ticker, side, action, price, quantity, realized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._session_id,
                time.time(),
                ticker,
                side,
                action,
                price,
                quantity,
                realized_pnl,
            ),
        )
        self._conn.commit()

    def record_balance(self, *, cash_cents: int, portfolio_cents: int) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO balances (session_id, ts, cash_cents, portfolio_cents)
            VALUES (?, ?, ?, ?)
            """,
            (self._session_id, time.time(), cash_cents, portfolio_cents),
        )
        self._conn.commit()

    def record_orderbook_snapshot(
        self,
        *,
        event_ticker: str,
        market_ticker: str,
        yes_bids: dict[int, float],
        no_bids: dict[int, float],
    ) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO orderbook_snapshots
                (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._session_id,
                time.time(),
                event_ticker,
                market_ticker,
                json.dumps({str(k): v for k, v in yes_bids.items()}),
                json.dumps({str(k): v for k, v in no_bids.items()}),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """End any open session and close the database connection."""
        if not self._enabled:
            return
        if self._session_id is not None:
            self.end_session()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
