from src.models import (
    OrderbookLevel,
    Orderbook,
    Market,
    Event,
    Order,
    OrderStatus,
    Position,
    TradeSignal,
)


def test_orderbook_best_bid_returns_highest_price():
    book = Orderbook(
        yes_bids=[OrderbookLevel(price=0.30, quantity=100), OrderbookLevel(price=0.25, quantity=50)],
        no_bids=[],
    )
    assert book.best_yes_bid() == 0.30


def test_orderbook_best_bid_empty_returns_none():
    book = Orderbook(yes_bids=[], no_bids=[])
    assert book.best_yes_bid() is None


def test_event_market_tickers():
    m1 = Market(ticker="T1", event_ticker="E1", title="Outcome 1", status="active")
    m2 = Market(ticker="T2", event_ticker="E1", title="Outcome 2", status="active")
    event = Event(
        event_ticker="E1",
        title="Test Event",
        series_ticker="S1",
        mutually_exclusive=True,
        markets=[m1, m2],
    )
    assert event.market_tickers() == ["T1", "T2"]


def test_trade_signal_has_required_fields():
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("T1", 0.55), ("T2", 0.50)],
        net_profit=0.03,
        profit_pct=3.0,
        exposure_ratio=1.5,
    )
    assert signal.event_ticker == "E1"
    assert len(signal.legs) == 2
