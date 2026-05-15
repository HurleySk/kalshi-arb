from dataclasses import dataclass
from pathlib import Path

import yaml

DEMO_REST_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
LIVE_REST_URL = "https://api.elections.kalshi.com/trade-api/v2"
LIVE_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

URLS = {
    "demo": (DEMO_REST_URL, DEMO_WS_URL),
    "live": (LIVE_REST_URL, LIVE_WS_URL),
}


@dataclass
class Config:
    mode: str
    api_key_id: str
    private_key_path: Path
    rest_base_url: str
    ws_url: str
    risk_mode: str
    strategy_overrides: dict
    fill_timeout_secs: int
    event_poll_interval_secs: int
    max_session_loss: float
    circuit_breaker_on_any_loss: bool
    maker_enabled: bool
    maker_fill_mode: str
    max_maker_events: int
    maker_max_horizon_hours: float
    max_contracts_per_arb: int
    sequential_execution: bool
    log_level: str
    log_file: str
    recording_enabled: bool
    recording_db_path: str
    recording_session_dir: str
    recording_snapshot_interval_secs: int
    recording_balance_poll_interval_secs: int
    retention_max_db_size_mb: int
    cleanup_interval_secs: int
    log_max_file_size_mb: int
    log_max_backup_count: int


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    for key in ("mode", "credentials", "strategy"):
        if key not in raw:
            raise ValueError(f"Missing required config key: {key!r}")

    mode = raw["mode"]
    if mode not in URLS:
        raise ValueError(f"Invalid mode: {mode!r}. Must be 'demo' or 'live'.")

    if mode not in raw["credentials"]:
        raise ValueError(f"No credentials for mode {mode!r}")

    creds = raw["credentials"][mode]
    for key in ("api_key_id", "private_key_path"):
        if key not in creds:
            raise ValueError(f"Missing credential: {key!r} for mode {mode!r}")

    rest_url, ws_url = URLS[mode]
    strategy = raw["strategy"]
    logging_cfg = raw.get("logging", {})
    recording_cfg = raw.get("recording", {})

    risk_mode = strategy.get("risk_mode", "conservative")
    override_keys = {
        "min_volume_24h", "min_bid_depth", "min_profit_pct",
        "require_recent_trades", "max_exposure_ratio",
        "near_term_hours", "hurdle_rate_annual_pct",
        "unwind_phase1_secs", "unwind_phase2_secs", "unwind_price_step_cents",
        "min_open_interest", "min_liquidity",
        "maker_min_volume_24h",
    }
    strategy_overrides = {k: v for k, v in strategy.items() if k in override_keys}

    return Config(
        mode=mode,
        api_key_id=creds["api_key_id"],
        private_key_path=Path(creds["private_key_path"]).expanduser(),
        rest_base_url=rest_url,
        ws_url=ws_url,
        risk_mode=risk_mode,
        strategy_overrides=strategy_overrides,
        fill_timeout_secs=strategy.get("fill_timeout_secs", 30),
        event_poll_interval_secs=strategy.get("event_poll_interval_secs", 60),
        max_session_loss=float(strategy.get("max_session_loss", 1.0)),
        circuit_breaker_on_any_loss=strategy.get("circuit_breaker_on_any_loss", True),
        maker_enabled=strategy.get("maker_enabled", True),
        maker_fill_mode=strategy.get("maker_fill_mode", "cancel_and_take"),
        max_maker_events=int(strategy.get("max_maker_events", 3)),
        maker_max_horizon_hours=float(strategy.get("maker_max_horizon_hours", 2.0)),
        max_contracts_per_arb=int(strategy.get("max_contracts_per_arb", 1)),
        sequential_execution=strategy.get("sequential_execution", True),
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/arb_bot.log"),
        recording_enabled=recording_cfg.get("enabled", True),
        recording_db_path=recording_cfg.get("db_path", "data/arb_history.db"),
        recording_session_dir=recording_cfg.get("session_dir", "data/sessions"),
        recording_snapshot_interval_secs=int(recording_cfg.get("snapshot_interval_secs", 5)),
        recording_balance_poll_interval_secs=int(recording_cfg.get("balance_poll_interval_secs", 300)),
        retention_max_db_size_mb=max(0, int(recording_cfg.get("retention_max_db_size_mb", 5000))),
        cleanup_interval_secs=max(60, int(recording_cfg.get("cleanup_interval_secs", 1800))),
        log_max_file_size_mb=max(1, int(logging_cfg.get("max_file_size_mb", 5))),
        log_max_backup_count=max(1, int(logging_cfg.get("max_backup_count", 5))),
    )
