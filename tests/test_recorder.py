import asyncio
import json
import os
import sqlite3
import time

import pytest

from src.recorder import DataRecorder


@pytest.fixture
def recorder(tmp_path):
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    return rec


def test_start_and_end_session(recorder):
    sid = recorder.start_session({"risk_mode": "conservative"})
    assert sid == 1
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchall()
    assert len(rows) == 1
    assert rows[0][2] is None  # end_time is NULL

    recorder.end_session()
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchall()
    assert rows[0][2] is not None  # end_time is set


def test_record_signal(recorder):
    recorder.start_session({})
    recorder.record_signal(
        event_ticker="EVT-A",
        strategy="taker",
        outcome="fire",
        reject_reason=None,
        bid_sum=1.08,
        ask_sum=None,
        profit_pct=2.5,
        exposure_ratio=1.5,
        legs=[{"ticker": "MKT-1", "price": 0.55, "depth": 10}],
        metadata={"extra": "data"},
    )
    rows = recorder._conn.execute("SELECT * FROM signal_evaluations").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "EVT-A"  # event_ticker
    assert rows[0][4] == "taker"  # strategy
    assert rows[0][5] == "fire"   # outcome


def test_record_execution(recorder):
    recorder.start_session({})
    recorder.record_execution(
        event_ticker="EVT-A",
        strategy="taker",
        legs=[{"ticker": "MKT-1", "action": "sell", "price": 0.55, "quantity": 1}],
        result="full_fill",
        fill_details={"order_1": "executed"},
        unwind_cost=0.0,
    )
    rows = recorder._conn.execute("SELECT * FROM executions").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "EVT-A"
    assert rows[0][5] == "full_fill"


def test_record_fill(recorder):
    recorder.start_session({})
    recorder.record_fill(
        ticker="MKT-1",
        side="yes",
        action="sell",
        price=0.55,
        quantity=1,
        realized_pnl=None,
    )
    rows = recorder._conn.execute("SELECT * FROM fills").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "MKT-1"
    assert rows[0][6] == 0.55


def test_record_balance(recorder):
    recorder.start_session({})
    recorder.record_balance(cash_cents=10000, portfolio_cents=10500)
    rows = recorder._conn.execute("SELECT * FROM balances").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == 10000
    assert rows[0][4] == 10500


def test_record_orderbook_snapshot(recorder):
    recorder.start_session({})
    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A",
        market_ticker="MKT-1",
        yes_bids={55: 10.0, 50: 5.0},
        no_bids={45: 8.0},
    )
    rows = recorder._conn.execute("SELECT * FROM orderbook_snapshots").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0][5])  # yes_bids_json
    assert data == {"55": 10.0, "50": 5.0}


def test_no_recording_without_session(recorder):
    """record_signal should silently no-op if no session is active."""
    recorder.record_signal(
        event_ticker="EVT-A", strategy="taker", outcome="fire",
        reject_reason=None, bid_sum=1.08, ask_sum=None,
        profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None,
    )
    rows = recorder._conn.execute("SELECT * FROM signal_evaluations").fetchall()
    assert len(rows) == 0


def test_recorder_disabled():
    """DataRecorder(None) creates a no-op recorder."""
    rec = DataRecorder(None)
    rec.start_session({})
    rec.record_signal(
        event_ticker="EVT-A", strategy="taker", outcome="fire",
        reject_reason=None, bid_sum=1.08, ask_sum=None,
        profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None,
    )
    # Should not raise — all methods are no-ops


def _create_session_file(session_dir, ts, obs_count=5):
    """Helper: create a session DB file with some data."""
    rec = DataRecorder(session_dir=str(session_dir))
    rec._session_dir = str(session_dir)
    db_file = session_dir / f"session_{ts:.6f}.db"
    rec._db_path = str(db_file)
    rec._conn = sqlite3.connect(str(db_file))
    rec._conn.execute("PRAGMA journal_mode=WAL")
    rec._init_schema()
    rec._session_id = 1
    rec._conn.execute(
        "INSERT INTO sessions (id, start_time, config_json) VALUES (1, ?, '{}')", (ts,))
    for i in range(obs_count):
        rec.record_orderbook_snapshot(
            event_ticker=f"EVT-{ts}", market_ticker=f"MKT-{i}",
            yes_bids={55: 10.0}, no_bids={45: 8.0},
        )
    rec._conn.commit()
    rec._conn.close()
    rec._conn = None
    return db_file


def test_per_session_creates_new_db_file(tmp_path):
    """start_session in session_dir mode creates a new DB file."""
    session_dir = tmp_path / "sessions"
    rec = DataRecorder(session_dir=str(session_dir))
    sid = rec.start_session({"test": True})
    assert sid == 1
    assert rec._db_path is not None
    assert "session_" in rec._db_path
    rec.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-1",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    rows = rec._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert rows == 1
    rec.end_session()
    rec.close()

    import glob
    files = glob.glob(str(session_dir / "session_*.db"))
    assert len(files) == 1


def test_cleanup_deletes_oldest_files(tmp_path):
    """cleanup_old_files should delete oldest session files when over cap."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    _create_session_file(session_dir, 1000.0, obs_count=50)
    _create_session_file(session_dir, 2000.0, obs_count=50)
    _create_session_file(session_dir, 3000.0, obs_count=50)

    rec = DataRecorder(session_dir=str(session_dir), max_db_size_mb=0)
    rec._db_path = str(session_dir / "session_9999.000000.db")
    result = rec.cleanup_old_files()

    assert result is not None
    assert len(result["deleted"]) >= 1

    import glob
    remaining = glob.glob(str(session_dir / "session_*.db*"))
    assert len(remaining) < 6


def test_cleanup_skips_when_under_cap(tmp_path):
    """cleanup_old_files should no-op when total size is under cap."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    _create_session_file(session_dir, 1000.0, obs_count=5)

    rec = DataRecorder(session_dir=str(session_dir), max_db_size_mb=5000)
    result = rec.cleanup_old_files()
    assert result is None


def test_cleanup_preserves_current_session(tmp_path):
    """cleanup_old_files should never delete the active session's DB."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    old_file = _create_session_file(session_dir, 1000.0, obs_count=50)
    current_file = _create_session_file(session_dir, 2000.0, obs_count=50)

    rec = DataRecorder(session_dir=str(session_dir), max_db_size_mb=0)
    rec._db_path = str(current_file)
    rec.cleanup_old_files()

    assert current_file.exists(), "Current session file must not be deleted"


def test_cleanup_loop_deletes_files(tmp_path):
    """cleanup_loop should periodically delete oldest session files."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    _create_session_file(session_dir, 1000.0, obs_count=50)
    _create_session_file(session_dir, 2000.0, obs_count=50)

    rec = DataRecorder(session_dir=str(session_dir), max_db_size_mb=0)
    rec._db_path = str(session_dir / "session_9999.000000.db")

    async def _run():
        loop_task = asyncio.create_task(rec.cleanup_loop(interval_secs=0.01))
        await asyncio.sleep(0.1)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    import glob
    remaining = glob.glob(str(session_dir / "session_*.db"))
    assert len(remaining) < 2
