import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from src.discovery import EventDiscovery
from src.scanner import OrderbookManager
from src.models import Event, Market


def _make_discovery():
    api = MagicMock()
    ob_mgr = OrderbookManager()
    scanner = MagicMock()
    scanner.subscribe = AsyncMock()
    discovery = EventDiscovery(api=api, orderbook_mgr=ob_mgr, scanner=scanner)
    return discovery, api


def test_register_events_stores_metadata():
    discovery, _ = _make_discovery()
    market = Market(
        ticker="M1", event_ticker="E1", title="Test",
        status="active", close_time="2026-06-01T00:00:00Z",
        volume_24h=100.0,
    )
    event = Event(event_ticker="E1", title="Test Event", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    new_tickers = discovery.register_events([event])
    assert new_tickers == ["M1"]
    assert "M1" in discovery.market_metadata
    assert discovery.market_metadata["M1"]["volume_24h"] == 100.0


def test_register_events_skips_duplicates():
    discovery, _ = _make_discovery()
    market = Market(ticker="M1", event_ticker="E1", title="Test",
                    status="active", volume_24h=100.0)
    event = Event(event_ticker="E1", title="Test", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    discovery.register_events([event])
    new_tickers = discovery.register_events([event])
    assert new_tickers == []


def test_cleanup_removes_expired():
    discovery, _ = _make_discovery()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    m_exp = Market(ticker="M_EXP", event_ticker="E_EXP", title="Exp",
                   status="active", close_time=past)
    m_act = Market(ticker="M_ACT", event_ticker="E_ACT", title="Act",
                   status="active", close_time=future)

    discovery.register_events([
        Event(event_ticker="E_EXP", title="", series_ticker="", mutually_exclusive=True, markets=[m_exp]),
        Event(event_ticker="E_ACT", title="", series_ticker="", mutually_exclusive=True, markets=[m_act]),
    ])

    removed = discovery.cleanup_expired()
    assert "E_EXP" in removed
    assert "E_ACT" not in removed
    assert "M_EXP" not in discovery.market_metadata
    assert "M_ACT" in discovery.market_metadata
