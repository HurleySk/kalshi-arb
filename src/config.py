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
    min_profit_pct: float
    max_exposure_ratio: float
    near_term_hours: float
    hurdle_rate_annual_pct: float
    min_bid_depth: int
    fill_timeout_secs: int
    event_poll_interval_secs: int
    log_level: str
    log_file: str


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

    return Config(
        mode=mode,
        api_key_id=creds["api_key_id"],
        private_key_path=Path(creds["private_key_path"]).expanduser(),
        rest_base_url=rest_url,
        ws_url=ws_url,
        min_profit_pct=strategy["min_profit_pct"],
        max_exposure_ratio=strategy["max_exposure_ratio"],
        near_term_hours=strategy.get("near_term_hours", 24),
        hurdle_rate_annual_pct=strategy.get("hurdle_rate_annual_pct", 10.0),
        min_bid_depth=strategy.get("min_bid_depth", 1),
        fill_timeout_secs=strategy["fill_timeout_secs"],
        event_poll_interval_secs=strategy["event_poll_interval_secs"],
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/arb_bot.log"),
    )
