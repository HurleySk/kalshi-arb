"""
DataRecorder — SQLite-backed recorder for bot sessions, signals, executions,
fills, balances, and orderbook snapshots.

Usage:
    rec = DataRecorder("/path/to/bot.db")   # live recording
    rec = DataRecorder(None)                 # no-op (disabled)

All record_* methods silently no-op when the recorder is disabled or no
session is active.  Writes are committed after each insert for durability.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("kalshi-arb")


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
    """SQLite-backed recorder.

    Two modes:
    - session_dir mode: each session creates a new DB file in the directory.
      Cleanup is instant (rm old files). Pass session_dir="/path/to/dir".
    - legacy mode: single DB file. Pass db_path="/path/to/file.db".
    - disabled: pass both as None.
    """

    def __init__(
        self,
        db_path: str | None = None,
        max_db_size_mb: int = 5000,
        min_sessions: int = 1,
        session_dir: str | None = None,
    ) -> None:
        self._session_dir = session_dir
        self._enabled = db_path is not None or session_dir is not None
        self._session_id: int | None = None
        self._conn: sqlite3.Connection | None = None
        self._db_path: str | None = None
        self._max_total_size_mb = max_db_size_mb

        if session_dir:
            Path(session_dir).mkdir(parents=True, exist_ok=True)
        elif db_path and self._enabled:
            self._db_path = db_path
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

        if self._session_dir:
            ts = time.time()
            db_file = Path(self._session_dir) / f"session_{ts:.6f}.db"
            self._db_path = str(db_file)
            self._conn = sqlite3.connect(str(db_file), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()

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
    # Retention
    # ------------------------------------------------------------------

    def cleanup_old_files(self) -> dict | None:
        """Delete oldest session files until total size is under cap. Instant via os.unlink."""
        if not self._session_dir:
            return None

        import glob as _glob
        pattern = str(Path(self._session_dir) / "session_*.db*")
        files = sorted(_glob.glob(pattern))

        total_bytes = sum(os.path.getsize(f) for f in files)
        cap_bytes = self._max_total_size_mb * 1024 * 1024
        if total_bytes <= cap_bytes:
            return None

        before_mb = total_bytes / (1024 * 1024)
        deleted_files: list[str] = []
        current = str(self._db_path) if self._db_path else ""

        session_groups: dict[str, list[str]] = {}
        for f in files:
            base = f.split("-wal")[0].split("-shm")[0]
            session_groups.setdefault(base, []).append(f)

        for base in sorted(session_groups.keys()):
            if total_bytes <= cap_bytes:
                break
            if base == current or base == current.split("-wal")[0]:
                continue
            for f in session_groups[base]:
                sz = os.path.getsize(f)
                os.unlink(f)
                total_bytes -= sz
                deleted_files.append(f)

        if not deleted_files:
            return None

        after_mb = total_bytes / (1024 * 1024)
        logger.info("Session cleanup: deleted %d file(s). Size: %.1f MB → %.1f MB",
                     len(deleted_files), before_mb, after_mb)
        return {"deleted": deleted_files, "before_mb": round(before_mb, 1), "after_mb": round(after_mb, 1)}

    async def cleanup_loop(self, interval_secs: float = 1800) -> None:
        """Periodically clean up old session files."""
        while True:
            await asyncio.sleep(interval_secs)
            try:
                if self._session_dir:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.cleanup_old_files)
            except Exception:
                logger.exception("Session cleanup failed, will retry next cycle")

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
