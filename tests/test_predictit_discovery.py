import asyncio
from unittest.mock import MagicMock, patch

from src.core.models import Event, Market
from src.exchanges.predictit.discovery import PredictItDiscovery


SAMPLE_PARSED_MARKETS = [
    {
        "id": 7456,
        "name": "Who will win the 2026 presidential election?",
        "shortName": "2026 Pres Election",
        "status": "Open",
        "contracts": [
            {
                "id": 28541,
                "name": "Democratic",
                "shortName": "Dem",
                "status": "Open",
                "dateEnd": "2026-11-03T23:59:00",
                "bestBuyYesCost": 0.54,
                "bestBuyNoCost": 0.48,
                "bestSellYesCost": 0.52,
                "bestSellNoCost": 0.46,
                "lastTradePrice": 0.53,
                "lastClosePrice": 0.53,
            },
            {
                "id": 28542,
                "name": "Republican",
                "shortName": "Rep",
                "status": "Open",
                "dateEnd": "2026-11-03T23:59:00",
                "bestBuyYesCost": 0.49,
                "bestBuyNoCost": 0.53,
                "bestSellYesCost": 0.47,
                "bestSellNoCost": 0.51,
                "lastTradePrice": 0.47,
                "lastClosePrice": 0.47,
            },
        ],
    }
]


def test_convert_to_events():
    mock_orderbook_mgr = MagicMock()
    mock_scanner = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=mock_scanner,
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    assert len(events) == 1


def test_event_has_correct_fields():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    event = events[0]
    assert event.event_ticker == "PI-7456"
    assert event.title == "Who will win the 2026 presidential election?"
    assert event.mutually_exclusive is True
    assert event.exchange == "predictit"
    assert len(event.markets) == 2


def test_market_tickers_use_contract_ids():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    tickers = events[0].market_tickers()
    assert "PI-7456-28541" in tickers
    assert "PI-7456-28542" in tickers


def test_market_metadata_stored():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    discovery.register_events(events)
    assert "PI-7456-28541" in discovery.market_metadata
    meta = discovery.market_metadata["PI-7456-28541"]
    assert meta["close_time"] == "2026-11-03T23:59:00"


def test_register_events_tracks_tickers():
    mock_orderbook_mgr = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 2
    assert "PI-7456" in discovery.event_tickers


def test_register_events_no_duplicates():
    mock_orderbook_mgr = MagicMock()
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=mock_orderbook_mgr,
        scanner=MagicMock(),
    )
    events = discovery._convert_to_events(SAMPLE_PARSED_MARKETS)
    discovery.register_events(events)
    new_tickers = discovery.register_events(events)
    assert len(new_tickers) == 0


def test_cleanup_expired_removes_past_events():
    discovery = PredictItDiscovery(
        scraper=MagicMock(),
        orderbook_mgr=MagicMock(),
        scanner=MagicMock(),
    )
    past_market = [
        {
            **SAMPLE_PARSED_MARKETS[0],
            "contracts": [
                {**c, "dateEnd": "2020-01-01T00:00:00"}
                for c in SAMPLE_PARSED_MARKETS[0]["contracts"]
            ],
        }
    ]
    events = discovery._convert_to_events(past_market)
    discovery.register_events(events)
    assert len(discovery.event_tickers) == 1
    removed = discovery.cleanup_expired()
    assert len(removed) == 1
    assert len(discovery.event_tickers) == 0
