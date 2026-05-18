import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

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
    exchange: str
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
    log_level: str
    log_file: str
    recording_enabled: bool
    recording_db_path: str
    recording_session_dir: str
    recording_snapshot_interval_secs: int
    recording_balance_poll_interval_secs: int
    retention_max_db_size_mb: int
    cleanup_interval_secs: int
    recording_write_buffer_size: int
    log_max_file_size_mb: int
    log_max_backup_count: int
    predictit_proxy_url: str | None = None
    predictit_session_dir: str = "~/.kalshi/predictit_session"
    predictit_headless: bool = True
    predictit_include_withdrawal_fee: bool = True
    predictit_poll_interval_secs: int = 60
    capital_budgets: dict[str, float] = field(default_factory=dict)


def load_config(path: str) -> Config:
    load_dotenv()
    with open(path) as f:
        raw = yaml.safe_load(f)

    for key in ("mode", "strategy"):
        if key not in raw:
            raise ValueError(f"Missing required config key: {key!r}")

    mode = raw["mode"]
    if mode not in URLS:
        raise ValueError(f"Invalid mode: {mode!r}. Must be 'demo' or 'live'.")

    exchange = raw.get("exchange", "kalshi")
    valid_exchanges = ("kalshi", "predictit")
    if exchange not in valid_exchanges:
        raise ValueError(f"Invalid exchange: {exchange!r}. Must be one of {valid_exchanges}.")

    if exchange == "kalshi":
        if "credentials" not in raw:
            raise ValueError("Missing required config key: 'credentials'")
        creds_section = raw["credentials"]
        if exchange in creds_section and isinstance(creds_section[exchange], dict):
            if mode not in creds_section[exchange]:
                raise ValueError(f"No credentials for exchange {exchange!r} mode {mode!r}")
            creds = creds_section[exchange][mode]
        elif mode in creds_section:
            creds = creds_section[mode]
        else:
            raise ValueError(f"No credentials for mode {mode!r}")
        for key in ("api_key_id", "private_key_path"):
            if key not in creds:
                raise ValueError(f"Missing credential: {key!r} for mode {mode!r}")
    else:
        creds = {}

    api_key_id = creds.get("api_key_id", "")
    private_key_path = (
        Path(creds["private_key_path"]).expanduser()
        if creds.get("private_key_path")
        else Path("/dev/null")
    )
    url_pair = URLS.get(mode, ("", ""))
    rest_url, ws_url = url_pair
    strategy = raw["strategy"]
    logging_cfg = raw.get("logging", {})
    recording_cfg = raw.get("recording", {})

    pi_cfg = raw.get("predictit", {})
    predictit_proxy_url = os.environ.get("DECODO_PROXY_URL") or pi_cfg.get("proxy_url")
    predictit_session_dir = (
        os.environ.get("PREDICTIT_SESSION_DIR")
        or pi_cfg.get("session_dir", "~/.kalshi/predictit_session")
    )
    predictit_headless = pi_cfg.get("headless", True)
    predictit_include_withdrawal_fee = pi_cfg.get("include_withdrawal_fee", True)
    predictit_poll_interval_secs = int(pi_cfg.get("poll_interval_secs", 60))

    risk_mode = strategy.get("risk_mode", "conservative")
    override_keys = {
        "min_volume_24h", "min_bid_depth", "min_ask_depth", "min_profit_pct",
        "require_recent_trades", "max_exposure_ratio",
        "near_term_hours", "hurdle_rate_annual_pct",
        "unwind_phase1_secs", "unwind_phase2_secs", "unwind_price_step_cents",
        "min_open_interest", "min_liquidity",
        "maker_min_volume_24h", "sequential_execution",
    }
    strategy_overrides = {k: v for k, v in strategy.items() if k in override_keys}

    capital_budget_raw = raw.get("capital_budget", {})
    capital_budgets = {k: float(v) for k, v in capital_budget_raw.items() if v and float(v) > 0}

    return Config(
        mode=mode,
        exchange=exchange,
        api_key_id=api_key_id,
        private_key_path=private_key_path,
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
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/arb_bot.log"),
        recording_enabled=recording_cfg.get("enabled", True),
        recording_db_path=recording_cfg.get("db_path", "data/arb_history.duckdb"),
        recording_session_dir=recording_cfg.get("session_dir", "data/sessions"),
        recording_snapshot_interval_secs=int(recording_cfg.get("snapshot_interval_secs", 5)),
        recording_balance_poll_interval_secs=int(recording_cfg.get("balance_poll_interval_secs", 300)),
        retention_max_db_size_mb=max(0, int(recording_cfg.get("retention_max_db_size_mb", 5000))),
        cleanup_interval_secs=max(60, int(recording_cfg.get("cleanup_interval_secs", 1800))),
        recording_write_buffer_size=max(1, int(recording_cfg.get("write_buffer_size", 50))),
        log_max_file_size_mb=max(1, int(logging_cfg.get("max_file_size_mb", 5))),
        log_max_backup_count=max(1, int(logging_cfg.get("max_backup_count", 5))),
        predictit_proxy_url=predictit_proxy_url,
        predictit_session_dir=predictit_session_dir,
        predictit_headless=predictit_headless,
        predictit_include_withdrawal_fee=predictit_include_withdrawal_fee,
        predictit_poll_interval_secs=predictit_poll_interval_secs,
        capital_budgets=capital_budgets,
    )
