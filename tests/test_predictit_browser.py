import asyncio
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.exchanges.predictit.browser import PredictItBrowser


def test_browser_init_defaults():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url=None,
    )
    assert browser.session_dir == Path("/tmp/test_session")
    assert browser.proxy_url is None
    assert browser.headless is True
    assert browser.page is None


def test_browser_init_with_proxy():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url="http://user:pass@proxy:8080",
        headless=False,
    )
    assert browser.proxy_url == "http://user:pass@proxy:8080"
    assert browser.headless is False


def test_session_state_path():
    browser = PredictItBrowser(
        session_dir="/tmp/test_session",
        proxy_url=None,
    )
    assert browser._state_path == Path("/tmp/test_session/state.json")


def test_has_saved_session_false_when_no_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        browser = PredictItBrowser(session_dir=tmpdir, proxy_url=None)
        assert browser.has_saved_session() is False


def test_has_saved_session_true_when_file_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        state_path.write_text('{"cookies": []}')
        browser = PredictItBrowser(session_dir=tmpdir, proxy_url=None)
        assert browser.has_saved_session() is True


def test_proxy_config_none():
    browser = PredictItBrowser(session_dir="/tmp/test", proxy_url=None)
    assert browser._proxy_config() is None


def test_proxy_config_parsed():
    browser = PredictItBrowser(
        session_dir="/tmp/test",
        proxy_url="http://user:pass@proxy.com:8080",
    )
    config = browser._proxy_config()
    assert config["server"] == "http://proxy.com:8080"
    assert config["username"] == "user"
    assert config["password"] == "pass"
