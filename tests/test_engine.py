from datetime import datetime, timezone, timedelta
from src.engine import ArbEngine
from src.models import Orderbook, OrderbookLevel
from src.risk import load_risk_profile


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


def test_min_bid_depth_rejects_thin_book():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=10)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_min_bid_depth_default_accepts():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=1)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=1)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=1)], no_bids=[]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None


# --- RiskProfile-based tests ---

def _make_engine_from_profile(mode="aggressive", **overrides):
    profile = load_risk_profile(mode, overrides)
    return ArbEngine(risk_profile=profile)


def test_volume_check_rejects_zero_volume_leg():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {
        "MED": Orderbook(yes_bids=[OrderbookLevel(price=0.46, quantity=10)], no_bids=[]),
        "LAN": Orderbook(yes_bids=[OrderbookLevel(price=0.99, quantity=10)], no_bids=[]),
    }
    meta = {"MED": {"volume_24h": 0}, "LAN": {"volume_24h": 500}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_volume_check_accepts_high_volume():
    engine = _make_engine_from_profile(mode="conservative")
    # Balanced prices give low exposure ratio (passes conservative max=2.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
    }
    meta = {"M1": {"volume_24h": 200}, "M2": {"volume_24h": 150}, "M3": {"volume_24h": 100}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


def test_aggressive_mode_allows_zero_volume():
    engine = _make_engine_from_profile(mode="aggressive")
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=10)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=10)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=10)], no_bids=[]),
    }
    meta = {"M1": {"volume_24h": 0}, "M2": {"volume_24h": 0}, "M3": {"volume_24h": 0}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


# --- Maker evaluation tests ---

def test_evaluate_maker_signal_in_fee_gap():
    """3 legs at $0.35 (sum=$1.05): taker profit 0.2% < 1% threshold, maker profit 5%."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    assert engine.evaluate("E1", orderbooks) is None

    maker_signal = engine.evaluate_maker("E1", orderbooks)
    assert maker_signal is not None
    assert maker_signal.signal_type == "maker"
    assert maker_signal.net_profit > 0


def test_evaluate_maker_returns_none_below_dollar():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    assert engine.evaluate_maker("E1", orderbooks) is None


def test_evaluate_maker_respects_volume_check():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    meta = {"M1": {"volume_24h": 0}, "M2": {"volume_24h": 500}, "M3": {"volume_24h": 500}}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_evaluate_maker_respects_depth_check():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=5)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    assert engine.evaluate_maker("E1", orderbooks) is None


def test_evaluate_maker_rejects_high_exposure():
    """2-leg at $0.51/$0.50 (sum=$1.01) has exposure ratio ~49, way above max."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    assert engine.evaluate_maker("E1", orderbooks) is None
