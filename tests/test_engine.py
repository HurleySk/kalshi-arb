from datetime import datetime, timezone, timedelta
from src.engine import ArbEngine
from src.models import Orderbook, OrderbookLevel


def _make_engine(min_profit_pct=2.0, max_exposure_ratio=3.0, **kwargs):
    return ArbEngine(min_profit_pct=min_profit_pct, max_exposure_ratio=max_exposure_ratio, **kwargs)


def _future_iso(days: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_evaluate_profitable_arb():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
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
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None  # 5.14% < 10%


def test_evaluate_rejects_high_exposure_ratio():
    engine = _make_engine(min_profit_pct=0.1, max_exposure_ratio=0.5)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.30, quantity=100)], no_bids=[]),
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
    engine = _make_engine(min_profit_pct=0.5, max_exposure_ratio=10.0)
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
    assert any(price == 0.40 for _, price in signal.legs)


def _wide_orderbooks():
    return {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }


def test_near_term_arb_accepted():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, near_term_hours=24)
    close = _future_iso(0.5)
    meta = {t: {"close_time": close} for t in ["M1", "M2", "M3"]}
    signal = engine.evaluate("E1", _wide_orderbooks(), market_metadata=meta)
    assert signal is not None


def test_long_dated_high_return_accepted():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, hurdle_rate_annual_pct=10.0)
    close = _future_iso(30)
    meta = {t: {"close_time": close} for t in ["M1", "M2", "M3"]}
    # 5.14% over 30 days → annualized 62.5% → above 10% hurdle
    signal = engine.evaluate("E1", _wide_orderbooks(), market_metadata=meta)
    assert signal is not None


def test_long_dated_low_return_rejected():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, hurdle_rate_annual_pct=10.0)
    close = _future_iso(730)
    meta = {t: {"close_time": close} for t in ["M1", "M2", "M3"]}
    # 5.14% over 730 days → annualized 2.57% → below 10% hurdle
    signal = engine.evaluate("E1", _wide_orderbooks(), market_metadata=meta)
    assert signal is None


def test_no_metadata_skips_horizon_check():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    signal = engine.evaluate("E1", _wide_orderbooks())
    assert signal is not None
