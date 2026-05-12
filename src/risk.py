from dataclasses import dataclass


@dataclass
class RiskProfile:
    min_volume_24h: float
    min_bid_depth: int
    min_profit_pct: float
    require_recent_trades: bool
    max_exposure_ratio: float
    near_term_hours: float
    hurdle_rate_annual_pct: float
    unwind_phase1_secs: int
    unwind_phase2_secs: int
    unwind_price_step_cents: int


PRESETS: dict[str, dict] = {
    "conservative": {
        "min_volume_24h": 50,
        "min_bid_depth": 5,
        "min_profit_pct": 2.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 2.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 15,
        "unwind_phase2_secs": 30,
        "unwind_price_step_cents": 3,
    },
    "moderate": {
        "min_volume_24h": 10,
        "min_bid_depth": 2,
        "min_profit_pct": 1.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 3.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 30,
        "unwind_phase2_secs": 60,
        "unwind_price_step_cents": 5,
    },
    "aggressive": {
        "min_volume_24h": 0,
        "min_bid_depth": 1,
        "min_profit_pct": 0.5,
        "require_recent_trades": False,
        "max_exposure_ratio": 5.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 45,
        "unwind_phase2_secs": 90,
        "unwind_price_step_cents": 8,
    },
}


def load_risk_profile(mode: str, overrides: dict) -> RiskProfile:
    if mode not in PRESETS:
        raise ValueError(f"Invalid risk_mode: {mode!r}. Must be one of {list(PRESETS.keys())}")
    values = {**PRESETS[mode]}
    for key, val in overrides.items():
        if key in values:
            values[key] = type(values[key])(val)
    return RiskProfile(**values)
