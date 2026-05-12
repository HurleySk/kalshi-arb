import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.executor import ExecutionManager
from src.models import TradeSignal


def _make_executor(fill_timeout=5):
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price_cents": round(yes_price * 100),
        "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order_id": "o1", "ticker": "M1", "status": "open"},
            {"order_id": "o2", "ticker": "M2", "status": "open"},
            {"order_id": "o3", "ticker": "M3", "status": "open"},
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
    assert orders[0]["yes_price_cents"] == 40
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
