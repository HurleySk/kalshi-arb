import os
import copy
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
    assert cfg.risk_mode == "conservative"
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


def test_load_config_with_risk_mode():
    custom = copy.deepcopy(SAMPLE_CONFIG)
    custom["strategy"]["risk_mode"] = "aggressive"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.risk_mode == "aggressive"


def test_load_config_defaults_risk_mode_to_conservative():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.risk_mode == "conservative"


def test_load_config_strategy_overrides():
    custom = copy.deepcopy(SAMPLE_CONFIG)
    custom["strategy"]["min_volume_24h"] = 200
    custom["strategy"]["min_bid_depth"] = 10
    custom["strategy"]["min_profit_pct"] = 5.0
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.strategy_overrides["min_volume_24h"] == 200
    assert cfg.strategy_overrides["min_bid_depth"] == 10
    assert cfg.strategy_overrides["min_profit_pct"] == 5.0


def test_load_config_maker_defaults():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.maker_enabled is True
    assert cfg.maker_fill_mode == "cancel_and_take"
    assert cfg.max_maker_events == 3
