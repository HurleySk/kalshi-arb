import time
from unittest.mock import MagicMock

from src.scanner import OrderbookManager


def test_market_age_returns_seconds_since_update():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    age = mgr.market_age("M1")
    assert age < 1.0, f"Freshly updated market should be <1s old, got {age}"


def test_market_age_returns_inf_for_never_updated():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    age = mgr.market_age("M1")
    assert age == float("inf"), "Never-updated market should have infinite age"


def test_market_age_returns_inf_for_unknown():
    mgr = OrderbookManager()
    age = mgr.market_age("UNKNOWN")
    assert age == float("inf")


def test_apply_delta_updates_timestamp():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1"])
    mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    time.sleep(0.05)
    age_before_delta = mgr.market_age("M1")  # measured after sleep: should be ~0.05s
    mgr.apply_delta("M1", {"price_dollars": "0.56", "delta_fp": "5", "side": "yes"})
    age_after_delta = mgr.market_age("M1")   # timestamp just refreshed: should be ~0s
    assert age_after_delta < age_before_delta, "Delta should refresh the timestamp"


def test_dispatcher_skips_stale_event():
    """Dispatcher must not evaluate signals when orderbook data is stale."""
    from src.dispatch import Dispatcher
    from src.executor import ExecutionManager

    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])

    mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    # Make M1 stale
    mgr._last_update_ts["M1"] = time.time() - 10.0

    engine = MagicMock()
    executor = MagicMock(spec=ExecutionManager)
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False

    dispatcher = Dispatcher(
        engine=engine, executor=executor,
        maker=None,
        orderbook_mgr=mgr, market_metadata={},
    )

    result = dispatcher.process_orderbook_update("M1")
    assert result is None, "Should skip evaluation when orderbook is stale"
    engine.evaluate.assert_not_called()  # staleness guard must short-circuit before engine
