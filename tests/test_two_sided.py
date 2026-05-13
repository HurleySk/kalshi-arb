import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.two_sided import TwoSidedManager
from src.risk import load_risk_profile
from src.models import TradeSignal


def _make_manager(timeout_secs=5, max_inventory=10):
    profile = load_risk_profile("aggressive", {
        "two_sided_timeout_secs": timeout_secs,
        "two_sided_max_inventory": max_inventory,
    })
    api = MagicMock()
    api.build_buy_order.return_value = {"action": "buy"}
    api.build_sell_order.return_value = {"action": "sell"}
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "BUY1", "status": "resting"}},
            {"order": {"order_id": "SELL1", "status": "resting"}},
        ]
    })
    api.cancel_order = AsyncMock(return_value={})
    return TwoSidedManager(api=api, risk_profile=profile), api


def _signal(ticker="M1", quantity=1):
    return TradeSignal(
        event_ticker=ticker, legs=[(ticker, 0.46), (ticker, 0.54)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=0.0,
        signal_type="two_sided", leg_actions=["buy", "sell"], quantity=quantity,
    )


def test_post_places_both_orders():
    manager, api = _make_manager()
    posted = asyncio.run(manager.post(_signal()))
    assert posted
    assert api.batch_create_orders.called


def test_post_tracks_position():
    manager, api = _make_manager()
    asyncio.run(manager.post(_signal("M1")))
    assert "M1" in manager._positions
    pos = manager._positions["M1"]
    assert pos["buy_id"] == "BUY1"
    assert pos["sell_id"] == "SELL1"
    assert pos["filled_side"] is None


def test_post_rejects_duplicate_ticker():
    manager, api = _make_manager()
    asyncio.run(manager.post(_signal("M1")))
    posted2 = asyncio.run(manager.post(_signal("M1")))
    assert not posted2
    assert api.batch_create_orders.call_count == 1


def test_inventory_cap_prevents_over_posting():
    manager, api = _make_manager(max_inventory=2)
    posted = asyncio.run(manager.post(_signal(quantity=5)))
    assert posted
    assert manager._positions["M1"]["quantity"] == 2


def test_cancel_unfilled_on_timeout():
    manager, api = _make_manager(timeout_secs=1)
    manager._positions["M1"] = {
        "buy_id": "BUY1", "sell_id": "SELL1",
        "filled_side": None, "quantity": 1, "posted_at": 0,
    }
    asyncio.run(manager._check_timeouts())
    assert api.cancel_order.call_count == 2
    assert "M1" not in manager._positions


def test_timeout_does_not_cancel_filled_positions():
    manager, api = _make_manager(timeout_secs=1)
    manager._positions["M1"] = {
        "buy_id": "BUY1", "sell_id": "SELL1",
        "filled_side": "buy", "quantity": 1, "posted_at": 0,
    }
    asyncio.run(manager._check_timeouts())
    api.cancel_order.assert_not_called()


def test_owns_order_returns_true_for_known_ids():
    manager, _ = _make_manager()
    manager._positions["M1"] = {
        "buy_id": "BUY1", "sell_id": "SELL1",
        "filled_side": None, "quantity": 1, "posted_at": 0,
    }
    assert manager.owns_order("BUY1")
    assert manager.owns_order("SELL1")
    assert not manager.owns_order("UNKNOWN")
