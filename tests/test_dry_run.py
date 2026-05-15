import asyncio
import json
import time

import pytest

from src.dry_run import DryRunEngine
from src.recorder import DataRecorder
from src.simulator import FaultConfig


def _make_db(tmp_path, bid1=60, bid2=60):
    """Create a test DB with one event whose bids sum > $1.07 (taker-profitable)."""
    db_path = str(tmp_path / "dry_run_test.db")
    rec = DataRecorder(db_path)
    sid = rec.start_session({"risk_mode": "conservative"})
    now = time.time()
    rec._conn.execute(
        "INSERT INTO orderbook_snapshots (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, now, "EVT-A", "MKT-1", json.dumps({str(bid1): 10.0}), json.dumps({str(100 - bid1): 10.0})),
    )
    rec._conn.execute(
        "INSERT INTO orderbook_snapshots (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, now, "EVT-A", "MKT-2", json.dumps({str(bid2): 10.0}), json.dumps({str(100 - bid2): 10.0})),
    )
    rec._conn.commit()
    rec.end_session()
    rec.close()
    return db_path


def test_dry_run_full_fill(tmp_path):
    db = _make_db(tmp_path)
    engine = DryRunEngine(db, risk_mode="aggressive", fault_config=FaultConfig(seed=1))
    report = asyncio.run(engine.run())

    assert report["signals_fired"] >= 1
    assert report["executions"] >= 1
    assert report["passed"] is True
    for pos in engine.positions.open_positions():
        assert pos.quantity > 0, f"Phantom short on {pos.ticker}: qty={pos.quantity}"


def test_dry_run_partial_fill_unwind(tmp_path):
    db = _make_db(tmp_path)
    faults = FaultConfig(partial_fill_rate=0.5, seed=7)
    engine = DryRunEngine(db, risk_mode="aggressive", fault_config=faults)
    report = asyncio.run(engine.run())

    assert report["signals_fired"] >= 1
    assert report["passed"] is True


def test_dry_run_ws_race_dedup(tmp_path):
    """WS fills injected at 100% rate must all be deduped — no phantom positions."""
    db = _make_db(tmp_path)
    faults = FaultConfig(ws_race_rate=1.0, seed=42)
    engine = DryRunEngine(db, risk_mode="aggressive", fault_config=faults)
    report = asyncio.run(engine.run())

    assert report["ws_fills_injected"] > 0, "WS fills should have been injected"
    assert report["ws_fills_deduped"] == report["ws_fills_injected"], \
        "All WS fills should be deduped (already processed from batch response)"
    assert report["passed"] is True
    for pos in engine.positions.open_positions():
        assert pos.quantity >= 0, f"Phantom short on {pos.ticker}: qty={pos.quantity}"


def test_dry_run_invariants_clean(tmp_path):
    db = _make_db(tmp_path)
    faults = FaultConfig(partial_fill_rate=0.1, ws_race_rate=0.3, seed=99)
    engine = DryRunEngine(db, risk_mode="aggressive", fault_config=faults)
    report = asyncio.run(engine.run())

    assert report["passed"] is True
    assert len(report["invariant_violations"]) == 0
