from unittest.mock import MagicMock
from src.api import KalshiAPI


def _make_api() -> KalshiAPI:
    auth = MagicMock()
    auth.build_headers.return_value = {
        "KALSHI-ACCESS-KEY": "k",
        "KALSHI-ACCESS-TIMESTAMP": "123",
        "KALSHI-ACCESS-SIGNATURE": "sig",
    }
    return KalshiAPI(base_url="https://test.kalshi.co/trade-api/v2", auth=auth)


def test_parse_events_response():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "Test",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [
                    {"ticker": "M1", "event_ticker": "E1", "title": "Out 1", "status": "active"},
                    {"ticker": "M2", "event_ticker": "E1", "title": "Out 2", "status": "active"},
                ],
            }
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 1
    assert events[0].event_ticker == "E1"
    assert len(events[0].markets) == 2
    assert events[0].mutually_exclusive is True


def test_parse_events_filters_non_mutually_exclusive():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "ME",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [
                    {"ticker": "M1", "event_ticker": "E1", "title": "O1", "status": "active"},
                    {"ticker": "M2", "event_ticker": "E1", "title": "O2", "status": "active"},
                ],
            },
            {
                "event_ticker": "E2",
                "title": "Not ME",
                "series_ticker": "S2",
                "mutually_exclusive": False,
                "markets": [
                    {"ticker": "M3", "event_ticker": "E2", "title": "O3", "status": "active"},
                    {"ticker": "M4", "event_ticker": "E2", "title": "O4", "status": "active"},
                ],
            },
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 1
    assert events[0].event_ticker == "E1"


def test_parse_events_filters_single_market():
    api = _make_api()
    raw = {
        "events": [
            {
                "event_ticker": "E1",
                "title": "Single",
                "series_ticker": "S1",
                "mutually_exclusive": True,
                "markets": [{"ticker": "M1", "event_ticker": "E1", "title": "O1", "status": "active"}],
            },
        ],
        "cursor": "",
    }
    events = api.parse_events(raw)
    assert len(events) == 0


def test_build_sell_order():
    api = _make_api()
    order = api.build_sell_order(ticker="M1", yes_price=0.55, quantity=10)
    assert order["ticker"] == "M1"
    assert order["action"] == "sell"
    assert order["side"] == "yes"
    assert order["type"] == "limit"
    assert order["yes_price"] == 55
    assert order["count"] == 10
