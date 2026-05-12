import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.maker import MakerManager
from src.models import TradeSignal, Orderbook, OrderbookLevel


def _make_maker(max_events=3, fill_mode="cancel_and_take"):
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "mo1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.52", "fill_count_fp": "0.00"}},
            {"order": {"order_id": "mo2", "ticker": "M2", "status": "resting",
                       "yes_price_dollars": "0.51", "fill_count_fp": "0.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    maker = MakerManager(api=api, fill_mode=fill_mode, max_events=max_events)
    return maker, api


def _maker_signal():
    return TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.52), ("M2", 0.51)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
        signal_type="maker",
    )


def test_post_maker_orders():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    api.batch_create_orders.assert_called_once()
    orders = api.batch_create_orders.call_args[0][0]
    assert len(orders) == 2
    assert all(o["action"] == "sell" for o in orders)
    assert maker.active_event_count() == 1


def test_max_events_respected():
    maker, api = _make_maker(max_events=1)
    s1 = _maker_signal()
    s2 = TradeSignal(
        event_ticker="E2",
        legs=[("M3", 0.53), ("M4", 0.50)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
        signal_type="maker",
    )
    asyncio.get_event_loop().run_until_complete(maker.post(s1))
    asyncio.get_event_loop().run_until_complete(maker.post(s2))

    assert maker.active_event_count() == 1
    assert api.batch_create_orders.call_count == 1


def test_no_duplicate_posts():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    assert api.batch_create_orders.call_count == 1


def test_cancel_all():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))
    asyncio.get_event_loop().run_until_complete(maker.cancel_all())

    api.batch_cancel_orders.assert_called()
    assert maker.active_event_count() == 0


def test_owns_order():
    maker, _ = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    assert maker.owns_order("mo1")
    assert maker.owns_order("mo2")
    assert not maker.owns_order("unknown")
