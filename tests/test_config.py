import os
import tempfile
import yaml
from src.config import load_config, Config, DEMO_REST_URL, DEMO_WS_URL, LIVE_REST_URL, LIVE_WS_URL


SAMPLE_CONFIG = {
    "mode": "demo",
    "credentials": {
        "demo": {
            "api_key_id": "test-key",
            "private_key_path": "/tmp/test_key.pem",
        },
        "live": {
            "api_key_id": "live-key",
            "private_key_path": "/tmp/live_key.pem",
        },
    },
    "strategy": {
        "min_profit_pct": 2.0,
        "max_exposure_ratio": 3.0,
        "fill_timeout_secs": 30,
        "event_poll_interval_secs": 60,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/arb_bot.log",
    },
}


def test_load_config_demo_mode():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.mode == "demo"
    assert cfg.api_key_id == "test-key"
    assert cfg.rest_base_url == DEMO_REST_URL
    assert cfg.ws_url == DEMO_WS_URL
    assert cfg.min_profit_pct == 2.0
    assert cfg.max_exposure_ratio == 3.0
    assert cfg.fill_timeout_secs == 30


def test_load_config_live_mode():
    live_config = {**SAMPLE_CONFIG, "mode": "live"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(live_config, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.mode == "live"
    assert cfg.api_key_id == "live-key"
    assert cfg.rest_base_url == LIVE_REST_URL
    assert cfg.ws_url == LIVE_WS_URL


def test_load_config_invalid_mode():
    bad_config = {**SAMPLE_CONFIG, "mode": "invalid"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_config, f)
        f.flush()
        try:
            load_config(f.name)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    os.unlink(f.name)
