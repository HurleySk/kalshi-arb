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


def test_conservative_preset_has_near_expiry_window():
    profile = load_risk_profile("conservative", {})
    assert profile.near_expiry_window_minutes == 30
    assert profile.near_expiry_min_profit_pct == 1.0
    assert profile.near_expiry_min_bid_depth == 1
    assert profile.near_expiry_min_volume_24h == 0.0


def test_moderate_preset_has_wider_window():
    profile = load_risk_profile("moderate", {})
    assert profile.near_expiry_window_minutes == 60


def test_aggressive_preset_has_widest_window():
    profile = load_risk_profile("aggressive", {})
    assert profile.near_expiry_window_minutes == 120


def test_conservative_two_sided_fields():
    profile = load_risk_profile("conservative", {})
    assert profile.two_sided_min_spread_cents == 6
    assert profile.two_sided_max_inventory == 10
    assert profile.two_sided_timeout_secs == 120
    assert profile.two_sided_min_volume_24h == 50.0


def test_moderate_two_sided_fields():
    profile = load_risk_profile("moderate", {})
    assert profile.two_sided_min_spread_cents == 4
    assert profile.two_sided_max_inventory == 25
    assert profile.two_sided_timeout_secs == 180
    assert profile.two_sided_min_volume_24h == 10.0


def test_aggressive_two_sided_fields():
    profile = load_risk_profile("aggressive", {})
    assert profile.two_sided_min_spread_cents == 2
    assert profile.two_sided_max_inventory == 50
