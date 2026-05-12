from src.engine import ArbEngine
from src.models import Orderbook, OrderbookLevel


def _make_engine(min_profit_pct=2.0, max_exposure_ratio=3.0):
    return ArbEngine(min_profit_pct=min_profit_pct, max_exposure_ratio=max_exposure_ratio)


def test_evaluate_profitable_arb():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=5.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.event_ticker == "E1"
    assert signal.net_profit > 0
    assert signal.profit_pct >= 1.0
    assert len(signal.legs) == 3


def test_evaluate_no_arb_below_one_dollar():
    engine = _make_engine()
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_below_profit_threshold():
    engine = _make_engine(min_profit_pct=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_high_exposure_ratio():
    engine = _make_engine(min_profit_pct=0.1, max_exposure_ratio=0.5)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
        "M4": Orderbook(yes_bids=[OrderbookLevel(price=0.25, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_skips_markets_with_no_bids():
    engine = _make_engine()
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.60, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_uses_best_bid():
    engine = _make_engine(min_profit_pct=0.5, max_exposure_ratio=5.0)
    orderbooks = {
        "M1": Orderbook(
            yes_bids=[OrderbookLevel(price=0.40, quantity=50), OrderbookLevel(price=0.35, quantity=100)],
            no_bids=[],
        ),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    # Should use best bid (0.40) not 0.35
    assert any(price == 0.40 for _, price in signal.legs)
