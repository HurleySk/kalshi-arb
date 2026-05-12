import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.executor import ExecutionManager
from src.models import TradeSignal
from src.risk import load_risk_profile


def _make_executor(fill_timeout=5):
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "open"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "open"}},
            {"order": {"order_id": "o3", "ticker": "M3", "status": "open"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(api=api, positions=positions, fill_timeout_secs=fill_timeout), api, positions


def test_build_orders_from_signal():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    orders = executor.build_orders(signal, quantity=10)
    assert len(orders) == 3
    assert orders[0]["ticker"] == "M1"
    assert orders[0]["yes_price"] == 40
    assert orders[0]["count"] == 10


def test_execute_calls_batch_create():
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=10))
    api.batch_create_orders.assert_called_once()


def test_is_executing_flag():
    executor, _, _ = _make_executor()
    assert not executor.is_executing()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.5,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=10))
    # After execution completes, flag should be cleared
    assert not executor.is_executing()


# --- RiskProfile + Unwind tests ---

def _make_executor_with_profile(mode="conservative", fill_timeout=1):
    profile = load_risk_profile(mode, {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
    })
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.46", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.99", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=fill_timeout, risk_profile=profile,
    ), api, positions


def test_partial_fill_triggers_unwind():
    executor, api, positions = _make_executor_with_profile(fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    assert executor.is_event_blacklisted("E1")
    # batch_create_orders: (1) original arb, (2+) unwind phases
    assert api.batch_create_orders.call_count >= 2


def test_immediate_fills_are_tracked():
    executor, api, positions = _make_executor_with_profile(fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    positions.record_fill.assert_called()
    call_args = [c.kwargs for c in positions.record_fill.call_args_list]
    tickers_filled = [c.get("ticker", "") for c in call_args]
    assert "M2" in tickers_filled


def test_unwind_places_buy_order():
    executor, api, _ = _make_executor_with_profile(fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    # Find the unwind call (not the first batch_create_orders call)
    assert api.batch_create_orders.call_count >= 2
    unwind_call = api.batch_create_orders.call_args_list[1]
    unwind_orders = unwind_call[0][0]
    assert unwind_orders[0]["action"] == "buy"
    assert unwind_orders[0]["ticker"] == "M2"
