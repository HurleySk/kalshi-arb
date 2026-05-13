import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

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


def test_retry_on_502():
    """502 errors should be retried, not raised immediately."""
    api = _make_api()

    call_count = 0
    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count < 3:
            resp.status = 502
            resp.text = AsyncMock(return_value="Bad Gateway")
        else:
            resp.status = 200
            resp.json = AsyncMock(return_value={"ok": True})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await api._request("GET", "/test")
        assert result == {"ok": True}
        assert call_count == 3

    asyncio.run(run())


def test_no_retry_on_400():
    """400 errors (bad request) should raise immediately, not retry."""
    api = _make_api()

    def mock_request(method, url, **kwargs):
        resp = MagicMock()
        resp.status = 400
        resp.text = AsyncMock(return_value="Bad Request")
        resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=400, message="Bad Request"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        try:
            await api._request("GET", "/test")
            assert False, "Should have raised"
        except aiohttp.ClientResponseError as e:
            assert e.status == 400

    asyncio.run(run())


def test_retry_on_connection_error():
    """Connection errors should be retried."""
    api = _make_api()

    call_count = 0
    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise aiohttp.ClientConnectionError("Connection reset")
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"ok": True})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await api._request("GET", "/test")
        assert result == {"ok": True}
        assert call_count == 2

    asyncio.run(run())


def test_all_retries_exhausted_raises_last_status():
    """When all 3 attempts return 5xx, raise with the actual last status, not 429."""
    api = _make_api()

    def mock_request(method, url, **kwargs):
        resp = MagicMock()
        resp.status = 503
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        with patch("asyncio.sleep", new_callable=AsyncMock):
            try:
                await api._request("GET", "/test")
                assert False, "Should have raised"
            except aiohttp.ClientResponseError as e:
                assert e.status == 503, f"Expected 503, got {e.status}"

    asyncio.run(run())
