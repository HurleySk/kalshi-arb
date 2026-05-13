import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from src.discovery import EventDiscovery, MonotoneFamilyRegistry
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
        volume_24h=100.0, open_interest=500.0, liquidity=2000.0,
    )
    event = Event(event_ticker="E1", title="Test Event", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    new_tickers = discovery.register_events([event])
    assert new_tickers == ["M1"]
    assert "M1" in discovery.market_metadata
    assert discovery.market_metadata["M1"]["volume_24h"] == 100.0
    assert discovery.market_metadata["M1"]["open_interest"] == 500.0
    assert discovery.market_metadata["M1"]["liquidity"] == 2000.0


def test_register_events_stores_total_market_count():
    # Event with total_market_count=3 (1 inactive not in markets list) should be stored
    discovery, _ = _make_discovery()
    m1 = Market(ticker="M1", event_ticker="E1", title="O1", status="active", volume_24h=0.0)
    m2 = Market(ticker="M2", event_ticker="E1", title="O2", status="active", volume_24h=0.0)
    event = Event(event_ticker="E1", title="Test", series_ticker="S1",
                  mutually_exclusive=True, markets=[m1, m2], total_market_count=3)

    discovery.register_events([event])
    assert discovery.event_total_markets["E1"] == 3


def test_register_events_skips_duplicates():
    discovery, _ = _make_discovery()
    market = Market(ticker="M1", event_ticker="E1", title="Test",
                    status="active", volume_24h=100.0)
    event = Event(event_ticker="E1", title="Test", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    discovery.register_events([event])
    new_tickers = discovery.register_events([event])
    assert new_tickers == []


def test_registers_threshold_pair_with_same_template():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "Will S&P 500 close above 5,000 on May 15?")
    reg.try_register("E2", "M2", "Will S&P 500 close above 5,100 on May 15?")
    families = reg.get_families()
    assert len(families) == 1
    family = list(families.values())[0]
    assert len(family) == 2


def test_does_not_group_unrelated_events():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "Will it rain in Seattle?")
    reg.try_register("E2", "M2", "Will the Fed raise rates?")
    assert len(reg.get_families()) == 0


def test_family_sorted_by_threshold_ascending():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "S&P above 5,200 by June?")
    reg.try_register("E2", "M2", "S&P above 5,000 by June?")
    reg.try_register("E3", "M3", "S&P above 5,100 by June?")
    families = reg.get_families()
    assert len(families) == 1
    members = list(families.values())[0]
    thresholds = [m["threshold"] for m in members]
    assert thresholds == sorted(thresholds)


def test_try_register_returns_family_key_when_matched():
    reg = MonotoneFamilyRegistry()
    key1 = reg.try_register("E1", "M1", "S&P above 5,000 by June?")
    key2 = reg.try_register("E2", "M2", "S&P above 5,100 by June?")
    assert key1 is not None
    assert key1 == key2


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
    assert "E_EXP" not in discovery.event_total_markets
    assert "E_ACT" in discovery.event_total_markets


def test_cleanup_removes_monotone_registry_entries():
    discovery, _ = _make_discovery()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    m1 = Market(ticker="M1", event_ticker="E1", title="Exp", status="active", close_time=past)
    m2 = Market(ticker="M2", event_ticker="E2", title="Exp", status="active", close_time=past)
    discovery.register_events([
        Event(event_ticker="E1", title="Will S&P close above 5,000?", series_ticker="", mutually_exclusive=True, markets=[m1]),
        Event(event_ticker="E2", title="Will S&P close above 5,100?", series_ticker="", mutually_exclusive=True, markets=[m2]),
    ])
    assert len(discovery.monotone_registry.get_families()) == 1

    discovery.cleanup_expired()
    assert len(discovery.monotone_registry.get_families()) == 0


def test_repoll_does_not_duplicate_monotone_registry():
    """Re-registering the same event on re-poll must not add duplicate family members."""
    discovery, _ = _make_discovery()
    m1 = Market(ticker="M1", event_ticker="E1", title="Test", status="active")
    m2 = Market(ticker="M2", event_ticker="E2", title="Test", status="active")
    event1 = Event(event_ticker="E1", title="Will S&P close above 5,000?", series_ticker="", mutually_exclusive=True, markets=[m1])
    event2 = Event(event_ticker="E2", title="Will S&P close above 5,100?", series_ticker="", mutually_exclusive=True, markets=[m2])

    discovery.register_events([event1, event2])
    discovery.register_events([event1, event2])  # simulate re-poll

    families = discovery.monotone_registry.get_families()
    assert len(families) == 1
    members = list(families.values())[0]
    assert len(members) == 2  # not 4
