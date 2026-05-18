"""
DataRecorder — DuckDB-backed recorder for bot sessions, signals, executions,
fills, balances, and orderbook snapshots.

Usage:
    rec = DataRecorder("/path/to/bot.duckdb")   # live recording
    rec = DataRecorder(None)                     # no-op (disabled)

All record_* methods silently no-op when the recorder is disabled or no
session is active.  Low-frequency writes (signals, executions, fills, balances)
are committed immediately.  Orderbook snapshots are buffered and flushed in
batches for performance.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger("kalshi-arb")


_DDL_STATEMENTS: list[str] = [
    # Sequences
    "CREATE SEQUENCE IF NOT EXISTS sessions_id_seq",
    "CREATE SEQUENCE IF NOT EXISTS signal_evaluations_id_seq",
    "CREATE SEQUENCE IF NOT EXISTS executions_id_seq",
    "CREATE SEQUENCE IF NOT EXISTS fills_id_seq",
    "CREATE SEQUENCE IF NOT EXISTS balances_id_seq",
    "CREATE SEQUENCE IF NOT EXISTS orderbook_snapshots_id_seq",

    # Tables
    """CREATE TABLE IF NOT EXISTS sessions (
        id          INTEGER PRIMARY KEY DEFAULT(nextval('sessions_id_seq')),
        start_time  DOUBLE NOT NULL,
        end_time    DOUBLE,
        config_json VARCHAR
    )""",

    """CREATE TABLE IF NOT EXISTS signal_evaluations (
        id             INTEGER PRIMARY KEY DEFAULT(nextval('signal_evaluations_id_seq')),
        session_id     INTEGER NOT NULL,
        ts             DOUBLE NOT NULL,
        event_ticker   VARCHAR NOT NULL,
        strategy       VARCHAR NOT NULL,
        outcome        VARCHAR NOT NULL,
        reject_reason  VARCHAR,
        bid_sum        DOUBLE,
        ask_sum        DOUBLE,
        profit_pct     DOUBLE,
        exposure_ratio DOUBLE,
        legs_json      VARCHAR,
        metadata_json  VARCHAR
    )""",

    """CREATE TABLE IF NOT EXISTS executions (
        id               INTEGER PRIMARY KEY DEFAULT(nextval('executions_id_seq')),
        session_id       INTEGER NOT NULL,
        ts               DOUBLE NOT NULL,
        event_ticker     VARCHAR NOT NULL,
        strategy         VARCHAR NOT NULL,
        result           VARCHAR NOT NULL,
        legs_json        VARCHAR,
        fill_details_json VARCHAR,
        unwind_cost      DOUBLE
    )""",

    """CREATE TABLE IF NOT EXISTS fills (
        id           INTEGER PRIMARY KEY DEFAULT(nextval('fills_id_seq')),
        session_id   INTEGER NOT NULL,
        ts           DOUBLE NOT NULL,
        ticker       VARCHAR NOT NULL,
        side         VARCHAR NOT NULL,
        action       VARCHAR NOT NULL,
        price        DOUBLE NOT NULL,
        quantity     INTEGER NOT NULL,
        realized_pnl DOUBLE
    )""",

    """CREATE TABLE IF NOT EXISTS balances (
        id              INTEGER PRIMARY KEY DEFAULT(nextval('balances_id_seq')),
        session_id      INTEGER NOT NULL,
        ts              DOUBLE NOT NULL,
        cash_cents      INTEGER NOT NULL,
        portfolio_cents INTEGER NOT NULL
    )""",

    """CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id             INTEGER PRIMARY KEY DEFAULT(nextval('orderbook_snapshots_id_seq')),
        session_id     INTEGER NOT NULL,
        ts             DOUBLE NOT NULL,
        event_ticker   VARCHAR NOT NULL,
        market_ticker  VARCHAR NOT NULL,
        yes_bids_json  VARCHAR,
        no_bids_json   VARCHAR
    )""",

    # Indexes
    "CREATE INDEX IF NOT EXISTS idx_se_session   ON signal_evaluations(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_se_event     ON signal_evaluations(event_ticker)",
    "CREATE INDEX IF NOT EXISTS idx_se_strategy  ON signal_evaluations(strategy)",
    "CREATE INDEX IF NOT EXISTS idx_se_ts        ON signal_evaluations(ts)",
    "CREATE INDEX IF NOT EXISTS idx_ex_session   ON executions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_ex_event     ON executions(event_ticker)",
    "CREATE INDEX IF NOT EXISTS idx_ex_ts        ON executions(ts)",
    "CREATE INDEX IF NOT EXISTS idx_fills_session ON fills(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_fills_ticker  ON fills(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_fills_ts      ON fills(ts)",
    "CREATE INDEX IF NOT EXISTS idx_bal_session   ON balances(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_bal_ts        ON balances(ts)",
    "CREATE INDEX IF NOT EXISTS idx_obs_session   ON orderbook_snapshots(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_obs_market    ON orderbook_snapshots(market_ticker)",
    "CREATE INDEX IF NOT EXISTS idx_obs_ts        ON orderbook_snapshots(ts)",
]


class DataRecorder:
    """DuckDB-backed recorder.

    Single-file mode: all sessions share one DuckDB file.  Cleanup prunes
    the oldest sessions' rows and checkpoints to reclaim space.
    """

    def __init__(
        self,
        db_path: str | None = None,
        max_db_size_mb: int = 5000,
        write_buffer_size: int = 50,
    ) -> None:
        self._enabled = db_path is not None
        self._session_id: int | None = None
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._db_path: str | None = db_path
        self._max_total_size_mb = max_db_size_mb
        self._snapshot_buffer: list[tuple] = []
        self._buffer_size = write_buffer_size
        self._last_flush = time.time()
        self._flush_interval = 5.0

        if db_path and self._enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(db_path)
            self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        assert self._conn is not None
        for ddl in _DDL_STATEMENTS:
            self._conn.execute(ddl)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self, config: dict) -> int | None:
        """Open a new recording session.  Returns session id, or None if disabled."""
        if not self._enabled:
            return None

        assert self._conn is not None
        result = self._conn.execute(
            "INSERT INTO sessions (start_time, end_time, config_json) VALUES (?, NULL, ?) RETURNING id",
            [time.time(), json.dumps(config)],
        )
        self._session_id = result.fetchone()[0]
        return self._session_id

    def end_session(self) -> None:
        """Close the current session by recording end_time."""
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._flush_snapshots()
        self._conn.execute(
            "UPDATE sessions SET end_time = ? WHERE id = ?",
            [time.time(), self._session_id],
        )
        self._session_id = None

    # ------------------------------------------------------------------
    # Retention — row-level pruning
    # ------------------------------------------------------------------

    def cleanup_old_sessions(self) -> dict | None:
        """Delete oldest sessions' rows until DB file is under size cap."""
        if not self._enabled or not self._db_path or not self._conn:
            return None

        try:
            total_bytes = os.path.getsize(self._db_path)
        except OSError:
            return None

        cap_bytes = self._max_total_size_mb * 1024 * 1024
        if total_bytes <= cap_bytes:
            return None

        before_mb = total_bytes / (1024 * 1024)
        deleted_sessions: list[int] = []

        while total_bytes > cap_bytes:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE id != ? ORDER BY start_time ASC LIMIT 1",
                [self._session_id or -1],
            ).fetchone()
            if row is None:
                break
            sid = row[0]

            for table in (
                "orderbook_snapshots", "signal_evaluations",
                "executions", "fills", "balances",
            ):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE session_id = ?", [sid]
                )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", [sid])
            deleted_sessions.append(sid)

            self._conn.execute("CHECKPOINT")
            try:
                total_bytes = os.path.getsize(self._db_path)
            except OSError:
                break

        if not deleted_sessions:
            return None

        after_mb = total_bytes / (1024 * 1024)
        logger.info(
            "Session cleanup: pruned %d session(s). Size: %.1f MB → %.1f MB",
            len(deleted_sessions), before_mb, after_mb,
        )
        return {
            "deleted_sessions": deleted_sessions,
            "before_mb": round(before_mb, 1),
            "after_mb": round(after_mb, 1),
        }

    async def cleanup_loop(self, interval_secs: float = 1800) -> None:
        """Periodically prune old sessions to stay under size cap."""
        while True:
            await asyncio.sleep(interval_secs)
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.cleanup_old_sessions)
            except Exception:
                logger.exception("Session cleanup failed, will retry next cycle")

    # ------------------------------------------------------------------
    # Snapshot write buffer
    # ------------------------------------------------------------------

    def _flush_snapshots(self) -> None:
        if not self._snapshot_buffer or not self._conn:
            return
        self._conn.executemany(
            """INSERT INTO orderbook_snapshots
                (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            self._snapshot_buffer,
        )
        self._snapshot_buffer.clear()
        self._last_flush = time.time()

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
            [
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
            ],
        )

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
            [
                self._session_id,
                time.time(),
                event_ticker,
                strategy,
                result,
                json.dumps(legs),
                json.dumps(fill_details) if fill_details is not None else None,
                unwind_cost,
            ],
        )

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
            [
                self._session_id,
                time.time(),
                ticker,
                side,
                action,
                price,
                quantity,
                realized_pnl,
            ],
        )

    def record_balance(self, *, cash_cents: int, portfolio_cents: int) -> None:
        if not self._enabled or self._session_id is None:
            return
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO balances (session_id, ts, cash_cents, portfolio_cents)
            VALUES (?, ?, ?, ?)
            """,
            [self._session_id, time.time(), cash_cents, portfolio_cents],
        )

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
        self._snapshot_buffer.append((
            self._session_id,
            time.time(),
            event_ticker,
            market_ticker,
            json.dumps({str(k): v for k, v in yes_bids.items()}),
            json.dumps({str(k): v for k, v in no_bids.items()}),
        ))
        now = time.time()
        if len(self._snapshot_buffer) >= self._buffer_size or (now - self._last_flush) > self._flush_interval:
            self._flush_snapshots()

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
            self._flush_snapshots()
            self._conn.close()
            self._conn = None
