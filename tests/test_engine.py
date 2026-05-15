from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from src.engine import ArbEngine
from src.models import Orderbook
from src.risk import RiskProfile, load_risk_profile


def _ob(yes_prices=None, no_prices=None):
    yes = {round(p * 100): q for p, q in (yes_prices or [])}
    no = {round(p * 100): q for p, q in (no_prices or [])}
    if yes and not no:
        no = {round((1.0 - max(yes) / 100.0) * 100): 100}
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
        min_buy_side_coverage=kwargs.get("min_buy_side_coverage", 0.0),
        maker_min_volume_24h=kwargs.get("maker_min_volume_24h", 0.0),
        min_ask_depth=kwargs.get("min_ask_depth", 1),
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


def test_evaluate_maker_horizon_uses_event_tickers_not_global_metadata():
    """_days_to_expiry must scope to event tickers only.

    Regression: when the shared market_metadata dict is passed directly (instead of
    a per-event view), an unrelated market closing soon must not fool the horizon guard
    into accepting a far-dated event.
    """
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    # Event markets close in 6h — beyond the 2h maker horizon, should be rejected.
    # An unrelated market closes in 30 minutes — must not influence this evaluation.
    meta = {
        "M1": {"close_time": _future_iso(6 / 24), "volume_24h": 500},
        "M2": {"close_time": _future_iso(6 / 24), "volume_24h": 500},
        "M3": {"close_time": _future_iso(6 / 24), "volume_24h": 500},
        "UNRELATED": {"close_time": _future_iso(0.5 / 24), "volume_24h": 999},
    }
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


def test_evaluate_maker_uses_separate_volume_threshold():
    """Maker should use maker_min_volume_24h, not the taker min_volume_24h."""
    engine = _make_engine(
        min_profit_pct=1.0, max_exposure_ratio=100.0,
        min_volume_24h=50.0,
        maker_min_volume_24h=0.0,
        maker_max_horizon_hours=24.0,
    )
    close = _future_iso(0.02)  # ~30 minutes, within 24h horizon
    orderbooks = {"M1": _ob([(0.35, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"volume_24h": 5, "close_time": close},
            "M2": {"volume_24h": 5, "close_time": close},
            "M3": {"volume_24h": 5, "close_time": close}}
    # Taker should reject (volume 5 < min 50)
    assert engine.evaluate("E1", orderbooks, market_metadata=meta) is None
    # Maker should accept (maker_min_volume_24h=0)
    signal = engine.evaluate_maker("E1", orderbooks, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "maker"


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


def test_near_expiry_relaxes_min_bid_depth():
    """near_expiry_min_bid_depth=1 accepts thin books that min_bid_depth=5 would reject."""
    engine = _make_engine_near_expiry(near_expiry_min_bid_depth=1)
    # Depth=2 at best bid — below conservative min_bid_depth=5, but above near_expiry threshold=1
    orderbooks = {"M1": _ob([(0.40, 2)]), "M2": _ob([(0.35, 2)]), "M3": _ob([(0.35, 2)])}
    meta = {t: {"volume_24h": 0} for t in ["M1", "M2", "M3"]}
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


def test_near_expiry_respects_min_open_interest():
    """evaluate_near_expiry must apply min_open_interest filter (inherited from _validate_legs)."""
    profile = RiskProfile(
        min_profit_pct=2.0, max_exposure_ratio=10.0, min_volume_24h=0.0,
        min_bid_depth=1, require_recent_trades=False, near_term_hours=24,
        hurdle_rate_annual_pct=10.0, unwind_phase1_secs=15, unwind_phase2_secs=30,
        unwind_price_step_cents=3, min_open_interest=100.0,
        near_expiry_min_profit_pct=1.0,
    )
    engine = ArbEngine(risk_profile=profile)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {t: {"volume_24h": 0, "open_interest": 5.0} for t in ["M1", "M2", "M3"]}
    assert engine.evaluate_near_expiry("E1", orderbooks, market_metadata=meta) is None


def test_near_expiry_respects_min_liquidity():
    """evaluate_near_expiry must apply min_liquidity filter (inherited from _validate_legs)."""
    profile = RiskProfile(
        min_profit_pct=2.0, max_exposure_ratio=10.0, min_volume_24h=0.0,
        min_bid_depth=1, require_recent_trades=False, near_term_hours=24,
        hurdle_rate_annual_pct=10.0, unwind_phase1_secs=15, unwind_phase2_secs=30,
        unwind_price_step_cents=3, min_liquidity=500.0,
        near_expiry_min_profit_pct=1.0,
    )
    engine = ArbEngine(risk_profile=profile)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {t: {"volume_24h": 0, "liquidity": 10.0} for t in ["M1", "M2", "M3"]}
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


def test_evaluate_buy_side_rejects_below_coverage_floor():
    # ask_sum = 0.83 (3 legs: 41¢+41¢+1¢), floor = 0.88 → rejected as non-exhaustive
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_buy_side_coverage=0.88)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={59: 100}),  # ask = 41¢
        "M2": Orderbook(yes_bids={}, no_bids={59: 100}),  # ask = 41¢
        "M3": Orderbook(yes_bids={}, no_bids={99: 100}),  # ask = 1¢
    }
    # ask_sum = 0.83 < floor 0.88 → None
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None


def test_evaluate_buy_side_passes_above_coverage_floor():
    # ask_sum = 0.91 (2 legs: 55¢+36¢), floor = 0.88 → allowed through
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_buy_side_coverage=0.88)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={45: 100}),  # ask = 55¢
        "M2": Orderbook(yes_bids={}, no_bids={64: 100}),  # ask = 36¢
    }
    # ask_sum = 0.91 ≥ floor 0.88 → evaluated normally (profitable since 0.91 < $1 - fees)
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is not None


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


# --- near-miss recording tests ---

def test_ask_depth_rejects_no_asks():
    """Market with bids but no asks (one-sided) should be rejected."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=1)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={}),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_ask_depth_rejects_thin_asks():
    """Ask depth below min_ask_depth should reject the signal."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=10)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={60: 2}),
        "M2": Orderbook(yes_bids={35: 100}, no_bids={65: 100}),
        "M3": Orderbook(yes_bids={35: 100}, no_bids={65: 100}),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_ask_depth_accepts_sufficient_asks():
    """Signal should pass when ask depth meets the minimum."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=5)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={60: 10}),
        "M2": Orderbook(yes_bids={35: 100}, no_bids={65: 10}),
        "M3": Orderbook(yes_bids={35: 100}, no_bids={65: 10}),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None


def test_evaluate_records_near_miss():
    """Engine should call recorder.record_signal for taker near-misses."""
    engine = _make_engine(min_profit_pct=2.0, max_exposure_ratio=10.0)
    recorder = MagicMock()
    engine.recorder = recorder
    books = {
        "MKT-1": Orderbook(yes_bids={49: 10}),
        "MKT-2": Orderbook(yes_bids={49: 10}),
    }
    # bid_sum = 0.98, which is >= 0.97 near-miss threshold but not profitable
    result = engine.evaluate("EVT-A", books)
    assert result is None
    recorder.record_signal.assert_called_once()
    call_kwargs = recorder.record_signal.call_args[1]
    assert call_kwargs["outcome"] == "near_miss"
    assert call_kwargs["strategy"] == "taker"


# --- Strategy taker (src/strategies/taker.py) tests ---


def test_taker_evaluate_sell_side():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_sell_side
    from datetime import datetime, timezone, timedelta

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    close = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    books = {
        "T-1": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-2": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-3": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
    }
    meta = {
        "T-1": {"close_time": close, "volume_24h": 100},
        "T-2": {"close_time": close, "volume_24h": 100},
        "T-3": {"close_time": close, "volume_24h": 100},
    }
    signal = evaluate_sell_side("E-1", books, meta, fm, rp)
    assert signal is not None
    assert signal.signal_type == "taker"
    assert signal.net_profit > 0


def test_taker_evaluate_sell_side_no_arb():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_sell_side

    fm = KalshiFeeModel()
    rp = load_risk_profile("conservative", {})
    books = {
        "T-1": CoreOB(bids={30: 10.0}, asks={32: 5.0}),
        "T-2": CoreOB(bids={30: 10.0}, asks={32: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = evaluate_sell_side("E-1", books, meta, fm, rp)
    assert signal is None


def test_taker_evaluate_buy_side():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_buy_side

    fm = KalshiFeeModel()
    # Disable coverage filter so the test focuses on profit math
    rp = load_risk_profile("aggressive", {})
    # 4 markets at 22c ask = 88c total, profit = 1.0 - 0.88 - fees > 0
    # ask_sum=0.88 > 0.85 (aggressive coverage), max_ask=0.22 > 0.20
    books = {
        "T-1": CoreOB(bids={20: 10.0}, asks={22: 5.0}),
        "T-2": CoreOB(bids={20: 10.0}, asks={22: 5.0}),
        "T-3": CoreOB(bids={20: 10.0}, asks={22: 5.0}),
        "T-4": CoreOB(bids={20: 10.0}, asks={22: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-3": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-4": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = evaluate_buy_side("E-1", books, meta, fm, rp)
    assert signal is not None
    assert signal.signal_type == "buy_side_taker"
    assert signal.leg_actions == ["buy", "buy", "buy", "buy"]


# --- Strategy near_expiry and monotone tests ---


def test_near_expiry_evaluate():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.near_expiry import evaluate as ne_evaluate
    from datetime import datetime, timezone, timedelta

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {"near_expiry_window_minutes": 120})
    close = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    # Use 60¢ + 55¢ = 1.15 sum: exposure_ratio ≈ 4.03 < aggressive max_exposure_ratio=5.0
    books = {
        "T-1": CoreOB(bids={60: 10.0}, asks={62: 5.0}),
        "T-2": CoreOB(bids={55: 10.0}, asks={57: 5.0}),
    }
    meta = {
        "T-1": {"close_time": close, "volume_24h": 0},
        "T-2": {"close_time": close, "volume_24h": 0},
    }
    signal = ne_evaluate("E-1", books, meta, fm, rp)
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"


def test_monotone_evaluate():
    from src.core.models import Orderbook as CoreOB
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.monotone import evaluate as mono_evaluate

    fm = KalshiFeeModel()
    upper = CoreOB(bids={70: 10.0}, asks={72: 5.0})
    lower = CoreOB(bids={50: 10.0}, asks={40: 15.0})
    signal = mono_evaluate("T-UPPER", upper, "T-LOWER", lower, fm)
    assert signal is not None
    assert signal.signal_type == "monotone"
    assert len(signal.legs) == 2
    assert signal.leg_actions == ["sell", "buy"]


def test_monotone_no_profit():
    from src.core.models import Orderbook as CoreOB
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.monotone import evaluate as mono_evaluate

    fm = KalshiFeeModel()
    upper = CoreOB(bids={40: 10.0}, asks={42: 5.0})
    lower = CoreOB(bids={50: 10.0}, asks={45: 15.0})
    signal = mono_evaluate("T-UPPER", upper, "T-LOWER", lower, fm)
    assert signal is None


# --- Core Engine coordinator tests ---


def test_core_engine_delegates_to_taker():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.core.engine import ArbEngine as CoreEngine
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from datetime import datetime, timezone, timedelta

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    engine = CoreEngine(fee_model=fm, risk_profile=rp)
    close = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    books = {
        "T-1": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-2": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-3": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
    }
    meta = {
        "T-1": {"close_time": close, "volume_24h": 100},
        "T-2": {"close_time": close, "volume_24h": 100},
        "T-3": {"close_time": close, "volume_24h": 100},
    }
    signal = engine.evaluate(event_ticker="E-1", orderbooks=books, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "taker"


def test_core_engine_maker_signal():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.core.engine import ArbEngine as CoreEngine
    from src.exchanges.kalshi.fee_model import KalshiFeeModel

    from datetime import datetime, timezone, timedelta

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    engine = CoreEngine(fee_model=fm, risk_profile=rp, maker_max_horizon_hours=4.0)
    close = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    books = {
        "T-1": CoreOB(bids={35: 10.0}, asks={37: 5.0}),
        "T-2": CoreOB(bids={35: 10.0}, asks={37: 5.0}),
        "T-3": CoreOB(bids={35: 10.0}, asks={37: 5.0}),
    }
    meta = {
        "T-1": {"volume_24h": 100, "close_time": close},
        "T-2": {"volume_24h": 100, "close_time": close},
        "T-3": {"volume_24h": 100, "close_time": close},
    }
    signal = engine.evaluate_maker(event_ticker="E-1", orderbooks=books, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "maker"


def test_core_engine_two_sided_signal():
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.core.engine import ArbEngine as CoreEngine
    from src.exchanges.kalshi.fee_model import KalshiFeeModel

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    engine = CoreEngine(fee_model=fm, risk_profile=rp)
    # Wide spread: bid=40, ask=50 → spread=10¢, aggressive min=2+2=4
    book = CoreOB(bids={40: 10.0}, asks={50: 10.0})
    signal = engine.evaluate_two_sided("T-1", book, volume_24h=100)
    assert signal is not None
    assert signal.signal_type == "two_sided"
    assert signal.leg_actions == ["buy", "sell"]


def test_taker_sell_side_no_hurdle_rate():
    """Sell-side taker has no hurdle rate — matches original ArbEngine.evaluate() behavior."""
    from src.core.models import Orderbook as CoreOB
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_sell_side

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    books = {
        "T-1": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-2": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
        "T-3": CoreOB(bids={40: 10.0}, asks={42: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-3": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = evaluate_sell_side("E-1", books, meta, fm, rp)
    assert signal is not None


def test_core_orderbook_manager_market_age_unregistered():
    from src.core.orderbook_manager import OrderbookManager as CoreOBM
    mgr = CoreOBM()
    assert mgr.market_age("NONEXISTENT") == float("inf")
