import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.executor import ExecutionManager
from src.models import TradeSignal
from src.positions import PositionTracker
from src.risk import load_risk_profile


def _make_executor(batch_create_side_effect=None, get_balance_side_effect=None):
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
    })
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    if batch_create_side_effect:
        api.batch_create_orders = AsyncMock(side_effect=batch_create_side_effect)
    else:
        api.batch_create_orders = AsyncMock(return_value={"orders": []})
    if get_balance_side_effect:
        api.get_balance = AsyncMock(side_effect=get_balance_side_effect)
    else:
        api.get_balance = AsyncMock(return_value={"balance": 10000})
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = PositionTracker()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=1, risk_profile=profile,
    ), api


def _signal(leg_actions=None):
    return TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.55), ("M2", 0.55)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
        leg_actions=leg_actions,
    )


def test_batch_create_timeout_does_not_hang():
    """If batch_create_orders hangs, execute() must not block forever."""
    async def _hang():
        await asyncio.sleep(999)

    executor, api = _make_executor(batch_create_side_effect=_hang)

    async def _run():
        try:
            await asyncio.wait_for(executor.execute(_signal()), timeout=20)
        except asyncio.TimeoutError:
            raise AssertionError("execute() hung — batch_create_orders has no timeout")

    asyncio.run(_run())


def test_balance_check_timeout_proceeds():
    """If get_balance hangs, buy-side execute() should proceed anyway."""
    async def _hang():
        await asyncio.sleep(999)

    executor, api = _make_executor(get_balance_side_effect=_hang)

    async def _run():
        signal = _signal(leg_actions=["buy", "buy"])
        try:
            await asyncio.wait_for(executor.execute(signal), timeout=15)
        except asyncio.TimeoutError:
            raise AssertionError("execute() hung — get_balance has no timeout")

    asyncio.run(_run())
