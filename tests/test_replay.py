import json
import time
import pytest
from src.models import Orderbook
from src.core.recorder import DataRecorder
from src.replay import ReplayEngine


@pytest.fixture
def db_with_data(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path)
    sid = rec.start_session({"risk_mode": "conservative"})
    now = time.time()
    rec._conn.execute(
        "INSERT INTO orderbook_snapshots (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json) VALUES (?, ?, ?, ?, ?, ?)",
        [sid, now, "EVT-A", "MKT-1", json.dumps({"55": 10.0}), json.dumps({})],
    )
    rec._conn.execute(
        "INSERT INTO orderbook_snapshots (session_id, ts, event_ticker, market_ticker, yes_bids_json, no_bids_json) VALUES (?, ?, ?, ?, ?, ?)",
        [sid, now, "EVT-A", "MKT-2", json.dumps({"56": 10.0}), json.dumps({})],
    )
    rec.end_session()
    rec.close()
    return db_path


def test_load_snapshots(db_with_data):
    engine = ReplayEngine(db_with_data)
    snapshots = engine.load_snapshots()
    assert len(snapshots) == 1
    ts, events = snapshots[0]
    assert "EVT-A" in events
    assert "MKT-1" in events["EVT-A"]
    assert "MKT-2" in events["EVT-A"]
    book = events["EVT-A"]["MKT-1"]
    assert isinstance(book, Orderbook)
    assert book.best_yes_bid() == 0.55
    engine.close()


def test_load_snapshots_date_filter(db_with_data):
    engine = ReplayEngine(db_with_data)
    future = time.time() + 9999
    snapshots = engine.load_snapshots(start=future)
    assert len(snapshots) == 0
    engine.close()


def test_sweep_single_param(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = engine.sweep({"min_profit_pct": [0.5, 1.0, 2.0, 3.0]})
    assert len(results) == 4
    for r in results:
        assert "params" in r
        assert "signal_count" in r
        assert "theoretical_profit" in r
    engine.close()


def test_sweep_train_test_split(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = engine.sweep(
        {"min_profit_pct": [0.5, 1.0]},
        train_end=time.time() + 1000,
        test_start=time.time() + 1000,
    )
    for r in results:
        assert "train_signal_count" in r
        assert "test_signal_count" in r
    engine.close()


def test_sweep_multi_param(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = engine.sweep({"min_profit_pct": [0.5, 2.0], "min_bid_depth": [1, 5]})
    assert len(results) == 4
    for r in results:
        assert "params" in r
        assert "min_profit_pct" in r["params"]
        assert "min_bid_depth" in r["params"]
    engine.close()


def test_find_plateaus(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = [
        {"params": {"min_profit_pct": 0.5}, "signal_count": 10, "theoretical_profit": 100.0},
        {"params": {"min_profit_pct": 1.0}, "signal_count": 9, "theoretical_profit": 95.0},
        {"params": {"min_profit_pct": 1.5}, "signal_count": 9, "theoretical_profit": 93.0},
        {"params": {"min_profit_pct": 2.0}, "signal_count": 2, "theoretical_profit": 10.0},
    ]
    plateaus = engine.find_plateaus(results, "min_profit_pct", threshold=0.10)
    assert len(plateaus) >= 1
    lo, hi = plateaus[0]
    assert lo == 0.5
    assert hi == 1.5
    engine.close()


def test_load_snapshots_int_keys(db_with_data):
    engine = ReplayEngine(db_with_data)
    snapshots = engine.load_snapshots()
    ts, events = snapshots[0]
    book = events["EVT-A"]["MKT-1"]
    for key in book.yes_bids:
        assert isinstance(key, int), f"Expected int key, got {type(key)}: {key!r}"
    engine.close()


def test_evaluate_snapshots_counts(db_with_data):
    from src.risk import load_risk_profile
    from src.engine import ArbEngine

    profile = load_risk_profile("conservative", {})
    arb_engine = ArbEngine(risk_profile=profile)
    replay = ReplayEngine(db_with_data)
    snapshots = replay.load_snapshots()
    signal_count, total_profit = replay._evaluate_snapshots(arb_engine, snapshots)
    assert signal_count >= 0
    assert total_profit >= 0.0
    replay.close()
