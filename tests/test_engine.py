from datetime import datetime, timezone, timedelta
from src.engine import ArbEngine
from src.models import Orderbook
from src.risk import RiskProfile, load_risk_profile


def _ob(yes_prices=None, no_prices=None):
    yes = {round(p * 100): q for p, q in (yes_prices or [])}
    no = {round(p * 100): q for p, q in (no_prices or [])}
    return Orderbook(yes_bids=yes, no_bids=no)


def _make_engine(min_profit_pct=2.0, max_exposure_ratio=3.0, **kwargs):
    profile = RiskProfile(
        min_profit_pct=min_profit_pct,
        max_exposure_ratio=max_exposure_ratio,
        min_volume_24h=kwargs.get("min_volume_24h", 0),
        min_bid_depth=kwargs.get("min_bid_depth", 1),
        require_recent_trades=kwargs.get("require_recent_trades", False),
        near_term_hours=kwargs.get("near_term_hours", 24),
        hurdle_rate_annual_pct=kwargs.get("hurdle_rate_annual_pct", 10.0),
        unwind_phase1_secs=15,
        unwind_phase2_secs=30,
        unwind_price_step_cents=3,
    )
    return ArbEngine(
        risk_profile=profile,
        maker_max_horizon_hours=kwargs.get("maker_max_horizon_hours", 1.0),
    )


def _future_iso(days: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_evaluate_profitable_arb():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.event_ticker == "E1"
    assert signal.net_profit > 0
    assert signal.profit_pct >= 1.0
    assert len(signal.legs) == 3


def test_evaluate_no_arb_below_one_dollar():
    engine = _make_engine()
    orderbooks = {"M1": _ob([(0.30, 100)]), "M2": _ob([(0.30, 100)]), "M3": _ob([(0.30, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_below_profit_threshold():
    engine = _make_engine(min_profit_pct=10.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_rejects_high_exposure_ratio():
    engine = _make_engine(min_profit_pct=0.1, max_exposure_ratio=0.5)
    orderbooks = {"M1": _ob([(0.50, 100)]), "M2": _ob([(0.40, 100)]), "M3": _ob([(0.30, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_skips_markets_with_no_bids():
    engine = _make_engine()
    orderbooks = {"M1": _ob([(0.60, 100)]), "M2": _ob(), "M3": _ob([(0.50, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_evaluate_uses_best_bid():
    engine = _make_engine(min_profit_pct=0.5, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": _ob([(0.40, 50), (0.35, 100)]),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert any(price == 0.40 for _, price in signal.legs)


def _wide_orderbooks():
    return {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}


def test_taker_ignores_horizon():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    close = _future_iso(730)
    meta = {t: {"close_time": close} for t in ["M1", "M2", "M3"]}
    signal = engine.evaluate("E1", _wide_orderbooks(), market_metadata=meta)
    assert signal is not None


def test_min_bid_depth_rejects_thin_book():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {"M1": _ob([(0.40, 10)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_min_bid_depth_default_accepts():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.40, 1)]), "M2": _ob([(0.35, 1)]), "M3": _ob([(0.35, 1)])}
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None


def _make_engine_from_profile(mode="aggressive", **overrides):
    profile = load_risk_profile(mode, overrides)
    return ArbEngine(risk_profile=profile)


def test_volume_check_rejects_zero_volume_leg():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {"MED": _ob([(0.46, 10)]), "LAN": _ob([(0.99, 10)])}
    meta = {"MED": {"volume_24h": 0}, "LAN": {"volume_24h": 500}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_volume_check_accepts_high_volume():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.40, 100)]), "M3": _ob([(0.40, 100)])}
    meta = {"M1": {"volume_24h": 200}, "M2": {"volume_24h": 150}, "M3": {"volume_24h": 100}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


def test_aggressive_mode_allows_zero_volume():
    engine = _make_engine_from_profile(mode="aggressive")
    orderbooks = {"M1": _ob([(0.40, 10)]), "M2": _ob([(0.40, 10)]), "M3": _ob([(0.40, 10)])}
    meta = {"M1": {"volume_24h": 0}, "M2": {"volume_24h": 0}, "M3": {"volume_24h": 0}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


def _near_meta(*tickers):
    close = _future_iso(1 / 24)  # 1 hour out — within default 2h maker horizon
    return {t: {"close_time": close, "volume_24h": 500} for t in tickers}


def test_evaluate_maker_rejects_beyond_horizon():
    """Events closing in 6 hours are rejected when maker_max_horizon_hours=2."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    meta = {t: {"close_time": _future_iso(6 / 24), "volume_24h": 500} for t in ["M1", "M2", "M3"]}
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_evaluate_maker_signal_in_fee_gap():
    """3 legs at $0.35 (sum=$1.05): taker profit < 1%, maker profit 5%."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    assert engine.evaluate("E1", orderbooks) is None
    maker_signal = engine.evaluate_maker("E1", orderbooks, market_metadata=_near_meta("M1", "M2", "M3"))
    assert maker_signal is not None
    assert maker_signal.signal_type == "maker"
    assert maker_signal.net_profit > 0


def test_evaluate_maker_returns_none_below_dollar():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.50, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=_near_meta("M1", "M2")) is None


def test_evaluate_maker_requires_metadata():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.52, 100)]), "M2": _ob([(0.51, 100)])}
    assert engine.evaluate_maker("E1", orderbooks) is None


def test_evaluate_maker_rejects_no_close_time():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.52, 100)]), "M2": _ob([(0.51, 100)])}
    meta = {"M1": {"volume_24h": 500}, "M2": {"volume_24h": 500}}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_evaluate_maker_respects_volume_check():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"volume_24h": 0, "close_time": _future_iso(0.5)},
            "M2": {"volume_24h": 500, "close_time": _future_iso(0.5)},
            "M3": {"volume_24h": 500, "close_time": _future_iso(0.5)}}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_evaluate_maker_respects_depth_check():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {"M1": _ob([(0.35, 5)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=_near_meta("M1", "M2", "M3")) is None


def test_evaluate_maker_rejects_extreme_exposure():
    """2-leg at 50c/50c = $1.00, no profit."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.50, 100)]), "M2": _ob([(0.50, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=_near_meta("M1", "M2")) is None


def test_evaluate_maker_accepts_moderate_exposure():
    """2-leg at $0.52/$0.51 (sum=$1.03) has maker exposure ratio ~16, below 50."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.52, 100)]), "M2": _ob([(0.51, 100)])}
    signal = engine.evaluate_maker("E1", orderbooks, market_metadata=_near_meta("M1", "M2"))
    assert signal is not None
    assert signal.signal_type == "maker"
