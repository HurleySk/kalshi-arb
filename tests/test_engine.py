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
        min_open_interest=kwargs.get("min_open_interest", 0.0),
        min_liquidity=kwargs.get("min_liquidity", 0.0),
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


def test_evaluate_maker_custom_horizon_accepts_within():
    """With maker_max_horizon_hours=12, events closing in 6h should be accepted."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, maker_max_horizon_hours=12.0)
    meta = {t: {"close_time": _future_iso(6 / 24), "volume_24h": 500} for t in ["M1", "M2", "M3"]}
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    signal = engine.evaluate_maker("E1", orderbooks, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "maker"


def test_evaluate_maker_custom_horizon_rejects_beyond():
    """With maker_max_horizon_hours=4, events closing in 6h should be rejected."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, maker_max_horizon_hours=4.0)
    meta = {t: {"close_time": _future_iso(6 / 24), "volume_24h": 500} for t in ["M1", "M2", "M3"]}
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_taker_ignores_horizon_no_metadata():
    """Taker path works fine even without metadata (no horizon logic at all)."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    signal = engine.evaluate("E1", _wide_orderbooks())
    assert signal is not None


def test_evaluate_maker_hurdle_rate_rejects_low_annualized():
    """Event within horizon but beyond near_term_hours must beat hurdle rate.

    near_term_hours=1 (so 12h out is 'long-dated'), hurdle_rate=1000%.
    3 legs at 0.35 = sum 1.05, profit=5%, days=0.5 -> annualized=5%*365/0.5=3650%.
    But with hurdle at 5000% it should be rejected.
    """
    engine = _make_engine(
        min_profit_pct=1.0,
        max_exposure_ratio=10.0,
        maker_max_horizon_hours=24.0,
        near_term_hours=1,
        hurdle_rate_annual_pct=5000.0,
    )
    meta = {t: {"close_time": _future_iso(0.5), "volume_24h": 500} for t in ["M1", "M2", "M3"]}
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    assert engine.evaluate_maker("E1", orderbooks, market_metadata=meta) is None


def test_evaluate_maker_hurdle_rate_accepts_high_annualized():
    """Same setup but with a beatable hurdle rate."""
    engine = _make_engine(
        min_profit_pct=1.0,
        max_exposure_ratio=10.0,
        maker_max_horizon_hours=24.0,
        near_term_hours=1,
        hurdle_rate_annual_pct=100.0,
    )
    meta = {t: {"close_time": _future_iso(0.5), "volume_24h": 500} for t in ["M1", "M2", "M3"]}
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    signal = engine.evaluate_maker("E1", orderbooks, market_metadata=meta)
    assert signal is not None


def test_signal_includes_quantity_from_depth():
    """Signal quantity should be min depth across legs, capped by max_contracts_per_arb."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    engine.max_contracts_per_arb = 5
    orderbooks = {
        "M1": _ob([(0.40, 10)]),
        "M2": _ob([(0.35, 3)]),
        "M3": _ob([(0.35, 8)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 3  # min(10, 3, 8) = 3, capped at 5 → 3


def test_signal_quantity_capped_by_max():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    engine.max_contracts_per_arb = 2
    orderbooks = {
        "M1": _ob([(0.40, 100)]),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 2


def test_signal_quantity_defaults_to_one():
    """Without max_contracts_per_arb set, quantity defaults to 1."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": _ob([(0.40, 100)]),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 1


def test_min_open_interest_rejects_low_oi():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_open_interest=100.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"open_interest": 50}, "M2": {"open_interest": 200}, "M3": {"open_interest": 200}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_min_liquidity_rejects_illiquid():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_liquidity=1000.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"liquidity": 500}, "M2": {"liquidity": 2000}, "M3": {"liquidity": 2000}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_zero_thresholds_accept_all():
    """Default 0 thresholds should not filter anything."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {
        "M1": {"open_interest": 0, "liquidity": 0},
        "M2": {"open_interest": 0, "liquidity": 0},
        "M3": {"open_interest": 0, "liquidity": 0},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


# --- evaluate_monotone_pair tests ---

def test_evaluate_monotone_pair_fires_on_violation():
    """Upper bid (0.65) > lower ask (1 - NO bid 0.55 = 0.45): monotone violation."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.65, 100)])
    lower_book = Orderbook(yes_bids={}, no_bids={55: 100})  # YES ask = 45¢
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is not None
    assert signal.signal_type == "monotone"
    assert signal.leg_actions == ["sell", "buy"]


def test_evaluate_monotone_pair_no_signal_when_no_violation():
    """Upper bid (0.40) < lower ask (0.55): no violation."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.40, 100)])
    lower_book = Orderbook(yes_bids={}, no_bids={45: 100})  # YES ask = 55¢
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is None


def test_evaluate_monotone_pair_respects_min_profit_pct():
    engine = _make_engine(min_profit_pct=50.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.65, 100)])
    lower_book = Orderbook(yes_bids={}, no_bids={55: 100})
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is None


# --- evaluate_near_expiry tests ---

def _make_engine_near_expiry(**kwargs):
    profile = RiskProfile(
        min_profit_pct=2.0,
        max_exposure_ratio=kwargs.get("max_exposure_ratio", 10.0),
        min_volume_24h=50.0,
        min_bid_depth=5,
        require_recent_trades=False,
        near_term_hours=24,
        hurdle_rate_annual_pct=10.0,
        unwind_phase1_secs=15,
        unwind_phase2_secs=30,
        unwind_price_step_cents=3,
        near_expiry_window_minutes=30,
        near_expiry_min_profit_pct=kwargs.get("near_expiry_min_profit_pct", 1.0),
        near_expiry_min_bid_depth=kwargs.get("near_expiry_min_bid_depth", 1),
        near_expiry_min_volume_24h=kwargs.get("near_expiry_min_volume_24h", 0.0),
    )
    return ArbEngine(risk_profile=profile)


def test_near_expiry_fires_when_normal_evaluate_would_fail_filters():
    """Normal evaluate rejects due to min_volume_24h=50, near_expiry accepts at 0."""
    engine = _make_engine_near_expiry()
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {
        "M1": {"volume_24h": 0, "close_time": _future_iso(0)},
        "M2": {"volume_24h": 0, "close_time": _future_iso(0)},
        "M3": {"volume_24h": 0, "close_time": _future_iso(0)},
    }
    assert engine.evaluate("E1", orderbooks, market_metadata=meta) is None
    signal = engine.evaluate_near_expiry("E1", orderbooks, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"


def test_near_expiry_uses_near_expiry_min_profit_pct():
    """Signal rejected if below near_expiry_min_profit_pct."""
    engine = _make_engine_near_expiry(near_expiry_min_profit_pct=50.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {t: {"volume_24h": 0, "close_time": _future_iso(0)} for t in ["M1", "M2", "M3"]}
    assert engine.evaluate_near_expiry("E1", orderbooks, market_metadata=meta) is None


# --- evaluate_buy_side tests ---

def test_evaluate_buy_side_profitable():
    # YES ask = 1 - NO bid. 3 legs: NO bids at 72¢ each → YES asks at 28¢ → sum=84¢ < $1
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is not None
    assert signal.signal_type == "buy_side_taker"
    assert signal.net_profit > 0
    assert all(a == "buy" for a in signal.leg_actions)


def test_evaluate_buy_side_no_signal_when_sum_above_one():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={65: 100}),  # ask = 35¢
        "M2": Orderbook(yes_bids={}, no_bids={65: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={65: 100}),  # sum = 105¢ > $1
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_returns_none_when_no_ask():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={}),  # no NO bids → no ask
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_respects_min_profit_pct():
    engine = _make_engine(min_profit_pct=50.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_respects_depth():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 5}),  # thin
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_rejects_zero_ask():
    # NO bid at 100¢ → YES ask = 0¢ — expired/certainty market, must not send 0¢ order
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={100: 100}),  # YES ask = 0¢
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_rejects_incomplete_coverage_low_sum():
    # Three legs at 1¢ ask + one at 3¢ → sum=6¢ — ask_sum < 0.60 filter fires
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={99: 100}),  # YES ask = 1¢
        "M2": Orderbook(yes_bids={}, no_bids={99: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={99: 100}),
        "M4": Orderbook(yes_bids={}, no_bids={97: 100}),  # YES ask = 3¢
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_rejects_no_dominant_outcome():
    # Sum passes 0.60 but max ask is only 9¢ — high-prob outcome not registered
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {f"M{i}": Orderbook(yes_bids={}, no_bids={91: 100}) for i in range(8)}
    # 8 legs at 9¢ each → sum=72¢ (passes sum check), max=9¢ (fails max check)
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_rejects_incomplete_registration():
    # Bot registered 3 markets but only has orderbook data for 2 — missing one outcome
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={60: 100}),  # ask = 40¢
        "M2": Orderbook(yes_bids={}, no_bids={40: 100}),  # ask = 60¢
    }
    # ask_sum = 1.00, would normally be filtered by profit check; use lower asks
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={65: 100}),  # ask = 35¢
        "M2": Orderbook(yes_bids={}, no_bids={38: 100}),  # ask = 62¢
    }
    # ask_sum = 0.97, but expected_market_count=3 means one market missing → reject
    signal = engine.evaluate_buy_side("E1", orderbooks, expected_market_count=3)
    assert signal is None


def test_evaluate_buy_side_passes_when_all_markets_present():
    # Same setup but expected_market_count matches registered count → allowed through
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={45: 100}),  # ask = 55¢
        "M2": Orderbook(yes_bids={}, no_bids={70: 100}),  # ask = 30¢
    }
    signal = engine.evaluate_buy_side("E1", orderbooks, expected_market_count=2)
    assert signal is not None  # ask_sum = 0.85, max_ask = 0.55, profit > 0, passes


# --- evaluate_two_sided tests ---

def _make_two_sided_engine(min_spread_cents=6, max_inventory=10, min_volume=0.0):
    profile = RiskProfile(
        min_profit_pct=1.0, max_exposure_ratio=3.0, min_volume_24h=0.0,
        min_bid_depth=1, require_recent_trades=False, near_term_hours=24,
        hurdle_rate_annual_pct=10.0, unwind_phase1_secs=15, unwind_phase2_secs=30,
        unwind_price_step_cents=3, two_sided_min_spread_cents=min_spread_cents,
        two_sided_max_inventory=max_inventory, two_sided_timeout_secs=120,
        two_sided_min_volume_24h=min_volume,
    )
    return ArbEngine(risk_profile=profile)


def test_evaluate_two_sided_fires_on_wide_spread():
    # YES bid 45¢, YES ask (= 1 - NO bid) = 1 - 0.45 = 55¢ → spread = 10¢ > 6¢
    engine = _make_two_sided_engine(min_spread_cents=6)
    book = Orderbook(yes_bids={45: 50}, no_bids={45: 50})  # ask = 55¢
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is not None
    assert signal.signal_type == "two_sided"
    assert signal.leg_actions == ["buy", "sell"]
    buy_leg = signal.legs[0]
    sell_leg = signal.legs[1]
    assert buy_leg[0] == "M1"
    assert sell_leg[0] == "M1"
    assert buy_leg[1] == 0.46   # bid + 1¢
    assert sell_leg[1] == 0.54  # ask - 1¢


def test_evaluate_two_sided_no_signal_on_narrow_spread():
    engine = _make_two_sided_engine(min_spread_cents=6)
    book = Orderbook(yes_bids={48: 50}, no_bids={48: 50})  # spread = 4¢
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is None


def test_evaluate_two_sided_disabled_when_max_inventory_zero():
    engine = _make_two_sided_engine(max_inventory=0)
    book = Orderbook(yes_bids={40: 50}, no_bids={40: 50})
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is None
