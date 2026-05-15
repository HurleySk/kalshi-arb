"""Verify that Protocol classes are importable and structurally sound."""
from src.ports import (
    FeeModel, ExchangeAPI, OrderBuilder,
    OrderbookFeed, MarketDiscovery, PositionConstraints,
)


def test_protocols_importable():
    assert FeeModel is not None
    assert ExchangeAPI is not None
    assert OrderBuilder is not None
    assert OrderbookFeed is not None
    assert MarketDiscovery is not None
    assert PositionConstraints is not None


def test_kalshi_fee_model_conforms():
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    fm = KalshiFeeModel()
    assert abs(fm.taker_fee(0.50) - 0.07 * 0.50 * 0.50) < 1e-9
    assert fm.maker_fee(0.50) == 0.0
    assert fm.profit_fee(1.0) == 0.0


def test_kalshi_order_builder_conforms():
    from src.exchanges.kalshi.order_builder import KalshiOrderBuilder
    ob = KalshiOrderBuilder()
    sell = ob.build_sell_order("T-1", 0.55, 1)
    assert sell["ticker"] == "T-1"
    assert sell["action"] == "sell"
    assert sell["yes_price"] == 55

    buy = ob.build_buy_order("T-1", 0.40, 2)
    assert buy["action"] == "buy"
    assert buy["yes_price"] == 40
    assert buy["count"] == 2

    close_long = ob.build_close_order("T-1", 1)
    assert close_long["action"] == "sell"
    assert close_long["yes_price"] == 1

    close_short = ob.build_close_order("T-1", -1)
    assert close_short["action"] == "buy"
    assert close_short["yes_price"] == 99

    unwrapped = ob.unwrap_order({"order": {"order_id": "abc"}})
    assert unwrapped == {"order_id": "abc"}


def test_kalshi_constraints_conforms():
    from src.exchanges.kalshi.constraints import KalshiConstraints
    c = KalshiConstraints()
    assert c.max_position_size("T-1") is None
    assert c.max_total_exposure() is None


def test_exchange_factory():
    from src.exchanges import create_exchange
    import pytest
    with pytest.raises(KeyError):
        create_exchange("nonexistent", {})
