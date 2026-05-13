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
