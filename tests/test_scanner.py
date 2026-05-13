import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
import websockets

from src.scanner import OrderbookManager, MarketScanner


# ── MarketScanner.connect() retry tests ──────────────────────────────────────

def _make_scanner():
    auth = MagicMock()
    auth.build_headers.return_value = {}
    return MarketScanner(
        ws_url="wss://fake",
        auth=auth,
        orderbook_mgr=OrderbookManager(),
    )


def test_connect_retries_on_failure():
    """connect() retries until it succeeds, returning on first success."""
    scanner = _make_scanner()
    fake_ws = MagicMock()

    connect_calls = 0
    async def fake_connect(url, additional_headers):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls < 3:
            raise OSError("connection refused")
        return fake_ws

    async def run():
        with patch("websockets.connect", side_effect=fake_connect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await scanner.connect()

    asyncio.run(run())
    assert connect_calls == 3
    assert scanner._ws is fake_ws
    assert scanner._running is True


def test_connect_stops_when_stopping():
    """connect() exits without connecting when _stopping is set."""
    scanner = _make_scanner()
    scanner._stopping = True

    async def run():
        with patch("websockets.connect", new_callable=AsyncMock) as mock_ws:
            await scanner.connect()
            mock_ws.assert_not_called()

    asyncio.run(run())
    assert scanner._ws is None
    assert scanner._running is False


def test_subscribe_tracks_tickers_when_disconnected():
    """subscribe() records tickers even when _ws is None (reconnect will use them)."""
    scanner = _make_scanner()
    assert scanner._ws is None

    asyncio.run(scanner.subscribe(["M1", "M2", "M3"]))

    assert scanner._subscribed_tickers == {"M1", "M2", "M3"}


def test_reconnect_resubscribes_all_channels():
    """_reconnect() calls connect(), subscribe_fills(), and subscribe() with all known tickers."""
    scanner = _make_scanner()
    scanner._subscribed_tickers = {"M1", "M2"}
    scanner._fills_subscribed = True

    connect_mock = AsyncMock()
    subscribe_mock = AsyncMock()
    subscribe_fills_mock = AsyncMock()

    async def fake_connect():
        scanner._ws = MagicMock()
        scanner._running = True

    async def run():
        with patch.object(scanner, "connect", side_effect=fake_connect):
            with patch.object(scanner, "subscribe", subscribe_mock):
                with patch.object(scanner, "subscribe_fills", subscribe_fills_mock):
                    await scanner._reconnect()

    asyncio.run(run())
    subscribe_fills_mock.assert_called_once()
    subscribe_mock.assert_called_once()
    called_tickers = set(subscribe_mock.call_args[0][0])
    assert called_tickers == {"M1", "M2"}


def test_apply_snapshot():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"], ["0.3500", "50.00"]],
        "no_dollars_fp": [["0.6000", "80.00"]],
    }
    mgr.apply_snapshot("M1", snapshot)
    book = mgr.get_orderbook("M1")
    assert book is not None
    assert len(book.yes_bids) == 2
    assert book.best_yes_bid() == 0.40


def test_apply_delta_add():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.3500",
        "delta_fp": "50.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 2


def test_apply_delta_remove():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.4000",
        "delta_fp": "-100.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 0
    assert book.best_yes_bid() is None


def test_apply_delta_update_quantity():
    mgr = OrderbookManager()
    snapshot = {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    }
    mgr.apply_snapshot("M1", snapshot)
    delta = {
        "market_ticker": "M1",
        "price_dollars": "0.4000",
        "delta_fp": "-30.00",
        "side": "yes",
    }
    mgr.apply_delta("M1", delta)
    book = mgr.get_orderbook("M1")
    assert len(book.yes_bids) == 1
    assert book.yes_bids[40] == 70.0


def test_get_event_orderbooks():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    })
    mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3500", "50.00"]],
        "no_dollars_fp": [],
    })
    event_books = mgr.get_event_orderbooks("E1")
    assert len(event_books) == 2
    assert "M1" in event_books
    assert "M2" in event_books


def test_market_to_event_mapping():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    assert mgr.get_event_for_market("M1") == "E1"
    assert mgr.get_event_for_market("M2") == "E1"
    assert mgr.get_event_for_market("M3") is None


def test_listen_with_async_callback():
    """listen() should support async on_orderbook_update callbacks."""
    auth = MagicMock()
    auth.build_headers.return_value = {}

    received_tickers = []

    async def async_callback(ticker: str):
        received_tickers.append(ticker)

    scanner = MarketScanner(
        ws_url="wss://fake",
        auth=auth,
        orderbook_mgr=OrderbookManager(),
        on_orderbook_update=async_callback,
    )

    scanner.orderbook_mgr.register_event("E1", ["M1"])

    messages = [
        '{"type": "orderbook_snapshot", "msg": {"market_ticker": "M1", "yes_dollars_fp": [["0.40", "100"]], "no_dollars_fp": []}}',
    ]
    msg_iter = iter(messages)

    fake_ws = MagicMock()

    async def fake_recv():
        try:
            return next(msg_iter)
        except StopIteration:
            scanner._running = False
            scanner._stopping = True
            raise websockets.ConnectionClosed(None, None)

    fake_ws.recv = fake_recv
    scanner._ws = fake_ws
    scanner._running = True

    asyncio.run(scanner.listen())
    assert "M1" in received_tickers
