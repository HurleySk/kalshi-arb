"""
Regression tests from 2026-05-12 trading session where the bot lost money
due to phantom liquidity and untracked partial fills.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import Orderbook, TradeSignal
from src.positions import PositionTracker
from src.risk import load_risk_profile


def _conservative_engine():
    return ArbEngine(risk_profile=load_risk_profile("conservative", {}))


def _partial_fill_executor(fill_timeout=1, mode="conservative"):
    profile = load_risk_profile(mode, {
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
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.4600", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.9900", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = PositionTracker()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=fill_timeout, risk_profile=profile,
    ), api, positions


def _medlan_signal():
    return TradeSignal(
        event_ticker="KXATPSETWINNER-26MAY12MEDLAN-1",
        legs=[("KXATPSETWINNER-26MAY12MEDLAN-1-MED", 0.46),
              ("KXATPSETWINNER-26MAY12MEDLAN-1-LAN", 0.99)],
        net_profit=0.4319, profit_pct=43.19, exposure_ratio=1.29,
    )


def test_phantom_liquidity_rejected_by_volume_check():
    """The MEDLAN event had bids but zero volume on the MED leg."""
    engine = _conservative_engine()
    orderbooks = {
        "MED": Orderbook(yes_bids={46: 10}, no_bids={}),
        "LAN": Orderbook(yes_bids={99: 10}, no_bids={}),
    }
    meta = {
        "MED": {"volume_24h": 0},
        "LAN": {"volume_24h": 500},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None, "Should reject arb when any leg has zero volume"


def test_partial_fill_detection_counts_correctly():
    """Executor must count 1/2 filled (not 0/2) when batch returns one executed leg."""
    executor, api, positions = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.run(executor.execute(signal, quantity=1))

    m2_pos = positions.get_position("M2")
    assert m2_pos is not None, "M2 fill was not tracked from batch response"
    assert m2_pos.quantity == 1


def test_partial_fill_blacklists_event():
    """After partial fill + timeout, the event must be blacklisted."""
    executor, _, _ = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.run(executor.execute(signal, quantity=1))
    assert executor.is_event_blacklisted(signal.event_ticker)


def test_repeat_execution_prevented():
    """Same event should not re-execute after a partial fill failure."""
    executor, _, _ = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.run(executor.execute(signal, quantity=1))
    assert executor.is_event_blacklisted("KXATPSETWINNER-26MAY12MEDLAN-1")


def test_unwind_fires_on_partial_fill():
    """After partial fill, unwind must place a buy-back order."""
    executor, api, _ = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    async def _run():
        await executor.execute(signal, quantity=1)
        await asyncio.sleep(0.1)

    asyncio.run(_run())

    assert api.batch_create_orders.call_count >= 2
    unwind_call = api.batch_create_orders.call_args_list[1]
    unwind_orders = unwind_call[0][0]
    assert unwind_orders[0]["action"] == "buy"
    assert unwind_orders[0]["ticker"] == "M2"


def test_asymmetric_fill_rejected_conservative():
    """Conservative mode rejects arb where low-prob leg has thin depth."""
    engine = _conservative_engine()
    orderbooks = {
        "M-FAVORITE": Orderbook(yes_bids={99: 100}, no_bids={}),
        "M-UNDERDOG": Orderbook(yes_bids={46: 1}, no_bids={}),
    }
    meta = {
        "M-FAVORITE": {"volume_24h": 500},
        "M-UNDERDOG": {"volume_24h": 100},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None, "Should reject: underdog depth 1 < conservative min 5"
