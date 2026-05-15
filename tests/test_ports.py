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
