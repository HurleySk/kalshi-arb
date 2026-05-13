import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

from src.dispatch import Dispatcher
from src.models import Orderbook, TradeSignal
from src.scanner import OrderbookManager


def _make_dispatcher():
    engine = MagicMock()
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False

    ob_mgr = OrderbookManager()
    ob_mgr.register_event("E1", ["M1", "M2", "M3"])

    dispatcher = Dispatcher(
        engine=engine,
        executor=executor,
        maker=None,
        orderbook_mgr=ob_mgr,
        market_metadata={},
    )
    return dispatcher, engine, executor


def test_dispatch_routes_profitable_signal():
    """When engine.evaluate returns a signal, dispatcher should fire execution."""
    dispatcher, engine, executor = _make_dispatcher()

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )
    engine.evaluate.return_value = signal

    dispatcher.orderbook_mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.4000", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M3", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})

    result = dispatcher.process_orderbook_update("M1")
    assert result is not None
    assert result.event_ticker == "E1"


def test_dispatch_skips_pending_event():
    """Events already pending execution should be skipped."""
    dispatcher, engine, executor = _make_dispatcher()
    dispatcher._pending_execution.add("E1")

    result = dispatcher.process_orderbook_update("M1")
    assert result is None
    engine.evaluate.assert_not_called()


def test_dispatch_routes_fill_to_executor():
    """Fills not owned by maker should go to executor."""
    dispatcher, _, executor = _make_dispatcher()
    fill = {"order_id": "o1", "market_ticker": "M1", "yes_price_dollars": "0.40", "count_fp": "1"}
    dispatcher.route_fill(fill)
    executor.handle_fill.assert_called_once_with(fill)


def test_dispatch_respects_signal_cooldown():
    """A second signal for the same event within the cooldown window should be suppressed."""
    dispatcher, engine, executor = _make_dispatcher()

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )
    engine.evaluate.return_value = signal

    dispatcher.orderbook_mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.4000", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M3", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})

    # First signal fires
    result1 = dispatcher.process_orderbook_update("M1")
    assert result1 is not None

    # Clear pending so cooldown is the only guard
    dispatcher.mark_execution_complete("E1")

    # Second signal within cooldown window should be suppressed
    result2 = dispatcher.process_orderbook_update("M1")
    assert result2 is None


def test_near_expiry_signal_suppressed_when_market_already_expired():
    """_is_near_expiry must return False for already-closed markets."""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone, timedelta

    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}
    ob_mgr.get_event_markets.return_value = ["M1"]

    already_closed = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market_metadata = {"M1": {"close_time": already_closed}}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata=market_metadata,
                            near_expiry_window_minutes=30)
    result = dispatcher.process_orderbook_update("M1")
    assert result is None
    engine.evaluate_near_expiry.assert_not_called()


def test_near_expiry_cooldown_prevents_second_signal():
    """Second near-expiry signal within cooldown window should be suppressed."""
    from unittest.mock import MagicMock
    from src.models import TradeSignal
    from datetime import datetime, timezone, timedelta

    ne_signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.55), ("M2", 0.55)], net_profit=0.02,
        profit_pct=2.0, exposure_ratio=1.0, signal_type="near_expiry_taker",
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    engine.evaluate_near_expiry.return_value = ne_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}
    ob_mgr.get_event_markets.return_value = ["M1"]

    close_soon = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market_metadata = {"M1": {"close_time": close_soon}}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata=market_metadata,
                            near_expiry_window_minutes=30)
    result1 = dispatcher.process_orderbook_update("M1")
    assert result1 is not None

    dispatcher.mark_execution_complete("E1")
    result2 = dispatcher.process_orderbook_update("M1")
    assert result2 is None  # cooldown still active


def test_dispatcher_routes_near_expiry_signal():
    from unittest.mock import MagicMock
    from src.models import TradeSignal
    from datetime import datetime, timezone, timedelta

    ne_signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.55), ("M2", 0.55)], net_profit=0.02,
        profit_pct=2.0, exposure_ratio=1.0, signal_type="near_expiry_taker",
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    engine.evaluate_near_expiry.return_value = ne_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}
    ob_mgr.get_event_markets.return_value = ["M1"]

    close_soon = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market_metadata = {"M1": {"close_time": close_soon}}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata=market_metadata,
                            near_expiry_window_minutes=30)
    signal = dispatcher.process_orderbook_update("M1")
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"


def test_dispatcher_routes_buy_side_signal():
    """Dispatcher returns a buy_side_taker signal when evaluate_buy_side fires."""
    from unittest.mock import MagicMock
    from src.models import TradeSignal

    buy_signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.28)], net_profit=0.05,
        profit_pct=5.0, exposure_ratio=0.0, signal_type="buy_side_taker",
        leg_actions=["buy"],
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = buy_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata={})
    signal = dispatcher.process_orderbook_update("M1")
    assert signal is not None
    assert signal.signal_type == "buy_side_taker"


def test_dispatcher_uses_api_total_over_registered_count():
    """When event_total_markets has a higher count than registered, api_total flows to evaluate_buy_side."""
    from unittest.mock import MagicMock, call
    from src.models import TradeSignal

    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock(), "M2": MagicMock()}
    ob_mgr.get_registered_market_count.return_value = 2

    # API says 5 total markets; only 2 are active/registered
    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata={},
                            enable_buy_side_arb=True,
                            event_total_markets={"E1": 5})
    dispatcher.process_orderbook_update("M1")

    engine.evaluate_buy_side.assert_called_once()
    _, kwargs = engine.evaluate_buy_side.call_args
    assert kwargs["expected_market_count"] == 5  # api_total, not registered (2)


def test_dispatcher_routes_monotone_signal():
    """Dispatcher returns a monotone signal when evaluate_monotone_pair fires on an adjacent pair."""
    from unittest.mock import MagicMock
    from src.models import TradeSignal, Orderbook
    from src.discovery import MonotoneFamilyRegistry

    mono_signal = TradeSignal(
        event_ticker="M_upper|M_lower", legs=[("M_upper", 0.65), ("M_lower", 0.45)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=0.0, signal_type="monotone",
        quantity=1, leg_actions=["sell", "buy"],
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    engine.evaluate_monotone_pair.return_value = mono_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False

    upper_book = Orderbook(yes_bids={65: 100}, no_bids={})
    lower_book = Orderbook(yes_bids={}, no_bids={55: 100})
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E_upper"
    ob_mgr.get_event_orderbooks.return_value = {"M_upper": upper_book}
    ob_mgr.get_orderbook.side_effect = lambda t: upper_book if t == "M_upper" else lower_book

    registry = MonotoneFamilyRegistry()
    registry.try_register("E_upper", "M_upper", "Will S&P close above 5,100?")
    registry.try_register("E_lower", "M_lower", "Will S&P close above 5,000?")

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata={},
                            monotone_registry=registry)
    signal = dispatcher.process_orderbook_update("M_upper")
    assert signal is not None
    assert signal.signal_type == "monotone"
    assert signal.leg_actions == ["sell", "buy"]


def test_dispatcher_monotone_skips_below_direction():
    """below/under families must be skipped — direction semantics are inverted."""
    from unittest.mock import MagicMock
    from src.models import TradeSignal, Orderbook
    from src.discovery import MonotoneFamilyRegistry

    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {}
    ob_mgr.get_orderbook.return_value = Orderbook(yes_bids={65: 100}, no_bids={55: 100})

    registry = MonotoneFamilyRegistry()
    registry.try_register("E1", "M1", "Will S&P close below 5,000?")
    registry.try_register("E2", "M2", "Will S&P close below 5,100?")

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata={},
                            monotone_registry=registry)
    signal = dispatcher.process_orderbook_update("M1")
    assert signal is None
    engine.evaluate_monotone_pair.assert_not_called()
