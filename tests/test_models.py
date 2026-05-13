from src.models import (
    Orderbook,
    Market,
    Event,
    Order,
    OrderStatus,
    Position,
    TradeSignal,
)


def test_orderbook_best_bid_returns_highest_price():
    book = Orderbook(yes_bids={30: 100, 25: 50}, no_bids={})
    assert book.best_yes_bid() == 0.30


def test_orderbook_best_bid_empty_returns_none():
    book = Orderbook(yes_bids={}, no_bids={})
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


def test_trade_signal_default_signal_type():
    signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.5)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.0,
    )
    assert signal.signal_type == "taker"


def test_trade_signal_maker_type():
    signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.5)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.0,
        signal_type="maker",
    )
    assert signal.signal_type == "maker"


def test_best_yes_ask_from_no_bids():
    book = Orderbook(yes_bids={}, no_bids={60: 10.0})
    assert book.best_yes_ask() == 0.40


def test_best_yes_ask_returns_none_when_no_no_bids():
    book = Orderbook(yes_bids={40: 10.0}, no_bids={})
    assert book.best_yes_ask() is None


def test_best_yes_ask_uses_highest_no_bid():
    # Highest NO bid = 70¢ → YES ask = 30¢
    book = Orderbook(yes_bids={}, no_bids={60: 5.0, 70: 3.0})
    assert book.best_yes_ask() == 0.30


def test_yes_ask_depth_at_sums_matching_no_bids():
    # YES ask 40¢ → need NO bids at 60¢ or higher
    book = Orderbook(yes_bids={}, no_bids={60: 5.0, 65: 3.0, 50: 10.0})
    assert book.yes_ask_depth_at(0.40) == 8.0  # 5 + 3 (not 50¢ NO bid)


def test_yes_ask_depth_at_returns_zero_when_no_match():
    book = Orderbook(yes_bids={}, no_bids={30: 10.0})
    assert book.yes_ask_depth_at(0.40) == 0.0
