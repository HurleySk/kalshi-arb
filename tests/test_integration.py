from unittest.mock import AsyncMock, MagicMock

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import Orderbook
from src.positions import PositionTracker
from src.risk import load_risk_profile
from src.scanner import OrderbookManager


def test_full_pipeline_detects_and_builds_orders():
    """Wire real components, feed orderbook data, verify arb detection and order building."""
    orderbook_mgr = OrderbookManager()
    engine = ArbEngine(load_risk_profile("aggressive", {"min_profit_pct": 1.0, "max_exposure_ratio": 10.0}))
    positions = PositionTracker()

    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
    })

    executor = ExecutionManager(api=api, positions=positions, fill_timeout_secs=5)

    orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])

    orderbook_mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.4000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3500", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M3", {
        "market_ticker": "M3",
        "yes_dollars_fp": [["0.3500", "100.00"]],
        "no_dollars_fp": [],
    })

    event_books = orderbook_mgr.get_event_orderbooks("E1")
    signal = engine.evaluate("E1", event_books)

    assert signal is not None
    assert signal.net_profit > 0

    orders = executor.build_orders(signal, quantity=10)
    assert len(orders) == 3
    tickers = {o["ticker"] for o in orders}
    assert tickers == {"M1", "M2", "M3"}


def test_full_pipeline_no_arb():
    """Verify pipeline correctly rejects non-profitable events."""
    orderbook_mgr = OrderbookManager()
    engine = ArbEngine(load_risk_profile("aggressive", {"min_profit_pct": 2.0, "max_exposure_ratio": 3.0}))

    orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])

    orderbook_mgr.apply_snapshot("M1", {
        "market_ticker": "M1",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M2", {
        "market_ticker": "M2",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })
    orderbook_mgr.apply_snapshot("M3", {
        "market_ticker": "M3",
        "yes_dollars_fp": [["0.3000", "100.00"]],
        "no_dollars_fp": [],
    })

    event_books = orderbook_mgr.get_event_orderbooks("E1")
    signal = engine.evaluate("E1", event_books)
    assert signal is None
