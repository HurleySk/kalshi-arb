from src.risk import RiskProfile, load_risk_profile


def test_conservative_preset():
    profile = load_risk_profile("conservative", {})
    assert profile.min_volume_24h == 50
    assert profile.min_bid_depth == 5
    assert profile.min_profit_pct == 2.0
    assert profile.require_recent_trades is True
    assert profile.max_exposure_ratio == 2.0
    assert profile.unwind_phase1_secs == 15
    assert profile.unwind_phase2_secs == 30
    assert profile.unwind_price_step_cents == 3


def test_moderate_preset():
    profile = load_risk_profile("moderate", {})
    assert profile.min_volume_24h == 10
    assert profile.min_bid_depth == 2
    assert profile.min_profit_pct == 1.0
    assert profile.require_recent_trades is True
    assert profile.max_exposure_ratio == 3.0
    assert profile.unwind_phase1_secs == 30
    assert profile.unwind_phase2_secs == 60
    assert profile.unwind_price_step_cents == 5


def test_aggressive_preset():
    profile = load_risk_profile("aggressive", {})
    assert profile.min_volume_24h == 0
    assert profile.min_bid_depth == 1
    assert profile.min_profit_pct == 0.5
    assert profile.require_recent_trades is False
    assert profile.max_exposure_ratio == 5.0
    assert profile.unwind_phase1_secs == 45
    assert profile.unwind_phase2_secs == 90
    assert profile.unwind_price_step_cents == 8


def test_overrides_take_precedence():
    profile = load_risk_profile("conservative", {"min_volume_24h": 200, "min_profit_pct": 5.0})
    assert profile.min_volume_24h == 200
    assert profile.min_profit_pct == 5.0
    assert profile.min_bid_depth == 5
    assert profile.require_recent_trades is True


def test_invalid_mode_raises():
    import pytest
    with pytest.raises(ValueError, match="Invalid risk_mode"):
        load_risk_profile("yolo", {})
