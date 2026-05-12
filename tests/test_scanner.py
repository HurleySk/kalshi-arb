from src.scanner import OrderbookManager
from src.models import OrderbookLevel


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
    assert book.yes_bids[0].quantity == 70.0


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
