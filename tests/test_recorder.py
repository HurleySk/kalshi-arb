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


def _populate_sessions(recorder, num_sessions, obs_per_session=10, sig_per_session=5):
    """Helper: create multiple sessions with snapshots, signals, executions, and fills."""
    for i in range(num_sessions):
        sid = recorder.start_session({"session": i})
        for j in range(obs_per_session):
            recorder.record_orderbook_snapshot(
                event_ticker=f"EVT-{i}",
                market_ticker=f"MKT-{i}-{j}",
                yes_bids={55: 10.0},
                no_bids={45: 8.0},
            )
        for j in range(sig_per_session):
            recorder.record_signal(
                event_ticker=f"EVT-{i}", strategy="taker", outcome="skip",
                reject_reason="test", bid_sum=1.0, ask_sum=None,
                profit_pct=0.0, exposure_ratio=0.0, legs=None, metadata=None,
            )
        recorder.record_execution(
            event_ticker=f"EVT-{i}", strategy="taker",
            legs=[{"ticker": f"MKT-{i}", "price": 0.55}],
            result="full_fill", fill_details=None, unwind_cost=0.0,
        )
        recorder.record_fill(
            ticker=f"MKT-{i}", side="yes", action="sell",
            price=0.55, quantity=1, realized_pnl=0.01,
        )
        recorder.record_balance(cash_cents=10000, portfolio_cents=10500)
        recorder.end_session()


def test_purge_skips_when_under_cap(recorder):
    """purge_old_sessions should no-op when DB is under the size cap."""
    recorder.start_session({})
    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-1",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    recorder.end_session()
    result = recorder.purge_old_sessions(max_db_size_mb=1000, min_sessions=1)
    assert result is None
    rows = recorder._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert rows == 1


def test_purge_deletes_oldest_session_snapshots_and_signals(tmp_path):
    """purge should delete snapshots and signals from oldest session first."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=10, sig_per_session=5)

    result = rec.purge_old_sessions(max_db_size_mb=0, min_sessions=1)
    assert result is not None
    assert len(result["sessions_purged"]) == 2

    obs = rec._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    sig = rec._conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()[0]
    assert obs == 10
    assert sig == 5

    execs = rec._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    fills = rec._conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    bals = rec._conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    assert execs == 3
    assert fills == 3
    assert bals == 3

    rec.close()


def test_purge_respects_min_sessions(tmp_path):
    """purge should never delete below min_sessions even if over cap."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=10, sig_per_session=5)

    result = rec.purge_old_sessions(max_db_size_mb=0, min_sessions=2)
    assert result is not None
    assert len(result["sessions_purged"]) == 1

    sessions_with_obs = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(sessions_with_obs) == 2

    rec.close()


def test_purge_sessions_rows_preserved(tmp_path):
    """Session table rows should never be deleted (referential integrity with trade data)."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=5, sig_per_session=3)

    rec.purge_old_sessions(max_db_size_mb=0, min_sessions=1)

    sessions = rec._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions == 3

    rec.close()


def test_start_session_triggers_purge(tmp_path):
    """start_session should purge old data when DB exceeds cap."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path, max_db_size_mb=0, min_sessions=1)
    _populate_sessions(rec, num_sessions=2, obs_per_session=10, sig_per_session=5)

    rec.start_session({"session": "new"})

    obs_sessions = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(obs_sessions) == 1

    rec.close()


def test_cleanup_loop_calls_purge(tmp_path):
    """cleanup_loop should call purge_old_sessions periodically."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path, max_db_size_mb=0, min_sessions=1)

    _populate_sessions(rec, num_sessions=2, obs_per_session=5, sig_per_session=3)

    async def _run():
        loop_task = asyncio.create_task(rec.cleanup_loop(interval_secs=0.01))
        await asyncio.sleep(0.1)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())

    obs_sessions = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(obs_sessions) == 1

    rec.close()
