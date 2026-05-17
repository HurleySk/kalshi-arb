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
    min_ask_depth: int = 1
    min_open_interest: float = 0.0
    min_liquidity: float = 0.0
    enable_buy_side_arb: bool = False
    near_expiry_window_minutes: int = 0
    near_expiry_min_profit_pct: float = 1.0
    near_expiry_min_bid_depth: int = 1
    near_expiry_min_volume_24h: float = 0.0
    two_sided_min_spread_cents: int = 6
    two_sided_max_inventory: int = 0  # 0 = disabled
    two_sided_timeout_secs: int = 120
    two_sided_min_volume_24h: float = 50.0
    buy_side_max_horizon_hours: float = 0.0  # 0 = disabled
    min_buy_side_coverage: float = 0.0  # 0 = disabled; rejects ask_sum below this floor
    maker_min_volume_24h: float = 0.0  # separate volume floor for maker (lower since makers create liquidity)
    sequential_execution: bool = True


PRESETS: dict[str, dict] = {
    "conservative": {
        "min_volume_24h": 50.0,
        "min_bid_depth": 5,
        "min_ask_depth": 5,
        "min_profit_pct": 2.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 2.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 15,
        "unwind_phase2_secs": 30,
        "unwind_price_step_cents": 3,
        "enable_buy_side_arb": False,
        "near_expiry_window_minutes": 30,
        "near_expiry_min_profit_pct": 1.0,
        "near_expiry_min_bid_depth": 1,
        "near_expiry_min_volume_24h": 0.0,
        "two_sided_min_spread_cents": 6,
        "two_sided_max_inventory": 0,
        "two_sided_timeout_secs": 120,
        "two_sided_min_volume_24h": 50.0,
        "buy_side_max_horizon_hours": 336.0,  # 14 days
        "min_buy_side_coverage": 0.90,
        "maker_min_volume_24h": 10.0,
        "sequential_execution": True,
    },
    "moderate": {
        "min_volume_24h": 10.0,
        "min_bid_depth": 2,
        "min_ask_depth": 2,
        "min_profit_pct": 1.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 3.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 30,
        "unwind_phase2_secs": 60,
        "unwind_price_step_cents": 5,
        "enable_buy_side_arb": False,
        "near_expiry_window_minutes": 60,
        "near_expiry_min_profit_pct": 0.5,
        "near_expiry_min_bid_depth": 1,
        "near_expiry_min_volume_24h": 0.0,
        "two_sided_min_spread_cents": 4,
        "two_sided_max_inventory": 0,
        "two_sided_timeout_secs": 180,
        "two_sided_min_volume_24h": 10.0,
        "buy_side_max_horizon_hours": 720.0,  # 30 days
        "min_buy_side_coverage": 0.88,
        "maker_min_volume_24h": 0.0,
        "sequential_execution": True,
    },
    "aggressive": {
        "min_volume_24h": 0.0,
        "min_bid_depth": 1,
        "min_ask_depth": 1,
        "min_profit_pct": 0.5,
        "require_recent_trades": False,
        "max_exposure_ratio": 5.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 45,
        "unwind_phase2_secs": 90,
        "unwind_price_step_cents": 8,
        "enable_buy_side_arb": False,
        "near_expiry_window_minutes": 120,
        "near_expiry_min_profit_pct": 0.3,
        "near_expiry_min_bid_depth": 1,
        "near_expiry_min_volume_24h": 0.0,
        "two_sided_min_spread_cents": 2,
        "two_sided_max_inventory": 0,
        "two_sided_timeout_secs": 300,
        "two_sided_min_volume_24h": 0.0,
        "buy_side_max_horizon_hours": 0.0,  # unlimited
        "min_buy_side_coverage": 0.85,
        "maker_min_volume_24h": 0.0,
        "sequential_execution": True,
    },
}


def load_risk_profile(mode: str, overrides: dict) -> RiskProfile:
    if mode not in PRESETS:
        raise ValueError(f"Invalid risk_mode: {mode!r}. Must be one of {list(PRESETS.keys())}")
    values = {**PRESETS[mode]}
    for key, val in overrides.items():
        if key not in values:
            continue
        if isinstance(values[key], bool):
            values[key] = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
        else:
            values[key] = type(values[key])(val)
    return RiskProfile(**values)
