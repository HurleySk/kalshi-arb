import json
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
