"""
Tests for src/analytics.py — Analytics class and CLI.
"""

import json
import time
import pytest
from src.core.recorder import DataRecorder
from src.core.analytics import Analytics


@pytest.fixture
def db_with_signals(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    rec = DataRecorder(db_path)
    sid = rec.start_session({"risk_mode": "conservative"})

    rec.record_signal(event_ticker="EVT-A", strategy="taker", outcome="fire",
                      reject_reason=None, bid_sum=1.08, ask_sum=None,
                      profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-B", strategy="taker", outcome="reject",
                      reject_reason="depth_filter", bid_sum=1.01, ask_sum=None,
                      profit_pct=0.5, exposure_ratio=1.0, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-C", strategy="taker", outcome="near_miss",
                      reject_reason=None, bid_sum=0.98, ask_sum=None,
                      profit_pct=None, exposure_ratio=None, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-D", strategy="buy_side", outcome="fire",
                      reject_reason=None, bid_sum=None, ask_sum=0.90,
                      profit_pct=3.0, exposure_ratio=0.0, legs=[], metadata=None)

    rec.record_execution(event_ticker="EVT-A", strategy="taker",
                         legs=[], result="full_fill", fill_details=None, unwind_cost=0.0)
    rec.record_execution(event_ticker="EVT-E", strategy="taker",
                         legs=[], result="partial_fill", fill_details=None, unwind_cost=0.15)

    rec.record_fill(ticker="MKT-1", side="yes", action="sell", price=0.55, quantity=1, realized_pnl=None)
    rec.record_fill(ticker="MKT-1", side="yes", action="buy", price=0.50, quantity=1, realized_pnl=0.05)

    rec.record_balance(cash_cents=10000, portfolio_cents=10500)
    rec.record_balance(cash_cents=10200, portfolio_cents=10700)

    rec.end_session()
    rec.close()
    return db_path


def test_strategy_breakdown(db_with_signals):
    analytics = Analytics(db_with_signals)
    breakdown = analytics.strategy_breakdown()
    assert "taker" in breakdown
    assert breakdown["taker"]["fire_count"] == 1
    assert breakdown["taker"]["reject_count"] == 1
    assert breakdown["taker"]["near_miss_count"] == 1
    assert "buy_side" in breakdown
    assert breakdown["buy_side"]["fire_count"] == 1
    analytics.close()


def test_rejection_funnel(db_with_signals):
    analytics = Analytics(db_with_signals)
    funnel = analytics.rejection_funnel()
    assert funnel["depth_filter"] == 1
    analytics.close()


def test_partial_fill_analysis(db_with_signals):
    analytics = Analytics(db_with_signals)
    pf = analytics.partial_fill_analysis()
    assert pf["partial_count"] == 1
    assert pf["total_executions"] == 2
    assert pf["total_unwind_cost"] == 0.15
    analytics.close()


def test_balance_curve(db_with_signals):
    analytics = Analytics(db_with_signals)
    curve = analytics.balance_curve()
    assert curve["start_cash_cents"] == 10000
    assert curve["end_cash_cents"] == 10200
    analytics.close()


def test_near_miss_analysis(db_with_signals):
    analytics = Analytics(db_with_signals)
    nm = analytics.near_miss_analysis()
    assert nm["total_near_misses"] == 1
    analytics.close()


def test_full_report_string(db_with_signals):
    analytics = Analytics(db_with_signals)
    report = analytics.full_report()
    assert "taker" in report
    assert "Strategy Breakdown" in report
    analytics.close()


# --- Additional edge-case tests ---

def test_strategy_breakdown_totals(db_with_signals):
    analytics = Analytics(db_with_signals)
    breakdown = analytics.strategy_breakdown()
    # taker: 1 fire + 1 reject + 1 near_miss = 3 total
    assert breakdown["taker"]["total"] == 3
    analytics.close()


def test_partial_fill_analysis_rate(db_with_signals):
    analytics = Analytics(db_with_signals)
    pf = analytics.partial_fill_analysis()
    assert abs(pf["partial_rate"] - 0.5) < 1e-6
    assert abs(pf["avg_unwind_cost"] - 0.15) < 1e-6
    analytics.close()


def test_balance_curve_change(db_with_signals):
    analytics = Analytics(db_with_signals)
    curve = analytics.balance_curve()
    assert curve["change_cents"] == 200
    assert curve["snapshots"] == 2
    analytics.close()


def test_near_miss_best_miss(db_with_signals):
    analytics = Analytics(db_with_signals)
    nm = analytics.near_miss_analysis()
    # EVT-C has bid_sum=0.98 which is the only near-miss; best_miss picks highest bid_sum
    assert nm["best_miss"] is not None
    assert nm["best_miss"]["event_ticker"] == "EVT-C"
    assert nm["best_miss"]["strategy"] == "taker"
    analytics.close()


def test_near_miss_by_strategy(db_with_signals):
    analytics = Analytics(db_with_signals)
    nm = analytics.near_miss_analysis()
    assert nm["by_strategy"]["taker"] == 1
    analytics.close()


def test_rejection_funnel_empty_when_no_rejects(tmp_path):
    """Rejection funnel returns empty dict when there are no rejects."""
    db_path = str(tmp_path / "empty.duckdb")
    rec = DataRecorder(db_path)
    rec.start_session({"risk_mode": "aggressive"})
    rec.record_signal(event_ticker="EVT-X", strategy="taker", outcome="fire",
                      reject_reason=None, bid_sum=1.10, ask_sum=None,
                      profit_pct=3.0, exposure_ratio=1.0, legs=[], metadata=None)
    rec.close()

    analytics = Analytics(db_path)
    funnel = analytics.rejection_funnel()
    assert funnel == {}
    analytics.close()


def test_balance_curve_empty(tmp_path):
    """balance_curve handles no balance rows gracefully."""
    db_path = str(tmp_path / "empty.duckdb")
    rec = DataRecorder(db_path)
    rec.start_session({})
    rec.close()

    analytics = Analytics(db_path)
    curve = analytics.balance_curve()
    assert curve["snapshots"] == 0
    assert curve["start_cash_cents"] is None
    assert curve["end_cash_cents"] is None
    analytics.close()


def test_partial_fill_analysis_no_executions(tmp_path):
    """partial_fill_analysis handles no execution rows gracefully."""
    db_path = str(tmp_path / "empty.duckdb")
    rec = DataRecorder(db_path)
    rec.start_session({})
    rec.close()

    analytics = Analytics(db_path)
    pf = analytics.partial_fill_analysis()
    assert pf["total_executions"] == 0
    assert pf["partial_count"] == 0
    assert pf["partial_rate"] == 0.0
    analytics.close()


def test_time_filter_restricts_results(db_with_signals):
    """Time filters exclude rows outside the range."""
    analytics = Analytics(db_with_signals)
    # Far-future range: no rows should match
    far_future = time.time() + 100_000
    breakdown = analytics.strategy_breakdown(start=far_future)
    assert breakdown == {}
    analytics.close()


def test_full_report_json_keys(db_with_signals):
    """full_report returns a non-empty string containing key section headers."""
    analytics = Analytics(db_with_signals)
    report = analytics.full_report()
    for section in ["Strategy Breakdown", "Rejection Funnel", "Partial Fill", "Balance Curve", "Near Miss"]:
        assert section in report, f"Missing section: {section}"
    analytics.close()
