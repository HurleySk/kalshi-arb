import asyncio
from unittest.mock import MagicMock, AsyncMock

from src.core.models import Orderbook
from src.core.orderbook_manager import OrderbookManager
from src.exchanges.predictit.scanner import PredictItScanner


def make_scanner(orderbook_mgr=None, on_update=None, on_fill=None):
    if orderbook_mgr is None:
        orderbook_mgr = OrderbookManager()
    scraper = MagicMock()
    return PredictItScanner(
        scraper=scraper,
        orderbook_mgr=orderbook_mgr,
        on_orderbook_update=on_update,
        on_fill=on_fill,
    )


def test_subscribe_tracks_tickers():
    scanner = make_scanner()
    asyncio.get_event_loop().run_until_complete(
        scanner.subscribe(["PI-100-1", "PI-100-2"])
    )
    assert "PI-100-1" in scanner._subscribed_tickers
    assert "PI-100-2" in scanner._subscribed_tickers


def test_build_synthetic_orderbook():
    scanner = make_scanner()
    contract = {
        "bestBuyYesCost": 0.54,
        "bestSellYesCost": 0.52,
        "bestBuyNoCost": 0.48,
        "bestSellNoCost": 0.46,
    }
    book = scanner._build_orderbook(contract)
    assert isinstance(book, Orderbook)
    assert 52 in book.bids
    assert 54 in book.asks


def test_build_synthetic_orderbook_null_prices():
    scanner = make_scanner()
    contract = {
        "bestBuyYesCost": None,
        "bestSellYesCost": None,
        "bestBuyNoCost": None,
        "bestSellNoCost": None,
    }
    book = scanner._build_orderbook(contract)
    assert len(book.bids) == 0
    assert len(book.asks) == 0


def test_detect_changes():
    scanner = make_scanner()
    old = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    new = {"PI-100-1": {"bestBuyYesCost": 0.55, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(old, new)
    assert "PI-100-1" in changed


def test_detect_changes_no_change():
    scanner = make_scanner()
    data = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(data, data)
    assert len(changed) == 0


def test_detect_changes_new_ticker():
    scanner = make_scanner()
    old = {}
    new = {"PI-100-1": {"bestBuyYesCost": 0.54, "bestSellYesCost": 0.52}}
    scanner._subscribed_tickers = {"PI-100-1"}
    changed = scanner._detect_changes(old, new)
    assert "PI-100-1" in changed


def test_stop_sets_flag():
    scanner = make_scanner()
    scanner.stop()
    assert scanner._stopping is True
