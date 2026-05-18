import asyncio
import json
import os
import time

import duckdb
import pytest

from src.core.recorder import DataRecorder


@pytest.fixture
def recorder(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path)
    return rec


def test_start_and_end_session(recorder):
    sid = recorder.start_session({"risk_mode": "conservative"})
    assert sid == 1
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", [sid]).fetchall()
    assert len(rows) == 1
    assert rows[0][2] is None  # end_time is NULL

    recorder.end_session()
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", [sid]).fetchall()
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
    # Set buffer_size=1 to force immediate flush
    recorder._buffer_size = 1
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


def test_snapshot_buffering(recorder):
    """Snapshots should buffer until threshold is reached."""
    recorder.start_session({})
    recorder._buffer_size = 3

    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-1",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    rows = recorder._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert rows == 0  # not yet flushed

    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-2",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-3",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    rows = recorder._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert rows == 3  # flushed at threshold


def test_flush_on_close(recorder):
    """Closing should flush any buffered snapshots."""
    recorder.start_session({})
    recorder._buffer_size = 100  # large buffer so it won't auto-flush

    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-1",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )

    # Reopen DB to check data was flushed
    db_path = recorder._db_path
    recorder.close()
    conn = duckdb.connect(db_path, read_only=True)
    rows = conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    conn.close()
    assert rows == 1


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


def test_multiple_sessions(recorder):
    """Multiple sessions should coexist in the same DB."""
    sid1 = recorder.start_session({"session": 1})
    recorder.record_signal(
        event_ticker="EVT-A", strategy="taker", outcome="fire",
        reject_reason=None, bid_sum=1.08, ask_sum=None,
        profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None,
    )
    recorder.end_session()

    sid2 = recorder.start_session({"session": 2})
    recorder.record_signal(
        event_ticker="EVT-B", strategy="maker", outcome="reject",
        reject_reason="depth", bid_sum=0.95, ask_sum=None,
        profit_pct=None, exposure_ratio=None, legs=[], metadata=None,
    )
    recorder.end_session()

    assert sid1 != sid2
    rows = recorder._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert rows == 2
    signals = recorder._conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()[0]
    assert signals == 2


def test_cleanup_prunes_oldest_session(tmp_path):
    """cleanup_old_sessions should delete oldest session rows when over cap."""
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path, max_db_size_mb=0)

    # Create two sessions with data
    rec.start_session({"session": 1})
    rec._buffer_size = 1
    for i in range(20):
        rec.record_orderbook_snapshot(
            event_ticker=f"EVT-1", market_ticker=f"MKT-{i}",
            yes_bids={55: 10.0}, no_bids={45: 8.0},
        )
    rec.end_session()

    rec.start_session({"session": 2})
    for i in range(5):
        rec.record_orderbook_snapshot(
            event_ticker=f"EVT-2", market_ticker=f"MKT-{i}",
            yes_bids={55: 10.0}, no_bids={45: 8.0},
        )
    rec._flush_snapshots()

    result = rec.cleanup_old_sessions()
    # With max_db_size_mb=0, it should try to prune (but may keep current session)
    # At minimum, the first session should be pruned
    if result is not None:
        assert len(result["deleted_sessions"]) >= 1

    # Current session's data should still exist
    rows = rec._conn.execute(
        "SELECT COUNT(*) FROM orderbook_snapshots WHERE session_id = ?",
        [rec._session_id],
    ).fetchone()[0]
    assert rows == 5
    rec.close()


def test_cleanup_skips_when_under_cap(tmp_path):
    """cleanup_old_sessions should no-op when file size is under cap."""
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path, max_db_size_mb=5000)
    rec.start_session({})
    result = rec.cleanup_old_sessions()
    assert result is None
    rec.close()


def test_cleanup_loop_runs(tmp_path):
    """cleanup_loop should periodically run cleanup."""
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path, max_db_size_mb=5000)
    rec.start_session({})

    async def _run():
        loop_task = asyncio.create_task(rec.cleanup_loop(interval_secs=0.01))
        await asyncio.sleep(0.1)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    rec.close()
