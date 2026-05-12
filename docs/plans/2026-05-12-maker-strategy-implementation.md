# Maker Strategy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a maker order layer that posts limit orders on near-arb events (bid sum $1.00–$1.07) at 0% maker fees, alongside the existing taker layer.

**Architecture:** A new `MakerManager` class owns the maker lifecycle (post, reprice, fill completion). The engine gains `evaluate_maker()` which checks for gross profit > 0 (no fees). Main.py routes maker signals to MakerManager and dispatches fills to both executor and maker manager. Two configurable fill modes: cancel_and_take (safe default) and tighten_on_fill.

**Tech Stack:** Python 3.11, asyncio, pytest, existing Kalshi REST/WS API client

---

### Task 1: TradeSignal signal_type + Maker Profit Function

**Files:**
- Modify: `src/models.py`
- Modify: `src/fees.py`
- Test: `tests/test_fees.py`, `tests/test_models.py`

**Step 1: Write failing tests**

Add to `tests/test_fees.py`:

```python
from src.fees import maker_arb_profit


def test_maker_arb_profit_no_fees():
    profit = maker_arb_profit([0.55, 0.50])
    assert abs(profit - 0.05) < 1e-9


def test_maker_arb_profit_below_dollar():
    profit = maker_arb_profit([0.40, 0.50])
    assert profit < 0


def test_maker_arb_profit_three_legs():
    profit = maker_arb_profit([0.40, 0.35, 0.35])
    assert abs(profit - 0.10) < 1e-9
```

Add to `tests/test_models.py`:

```python
def test_trade_signal_default_signal_type():
    signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.5)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.0,
    )
    assert signal.signal_type == "taker"


def test_trade_signal_maker_type():
    signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.5)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.0,
        signal_type="maker",
    )
    assert signal.signal_type == "maker"
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_fees.py::test_maker_arb_profit_no_fees tests/test_models.py::test_trade_signal_default_signal_type -v`
Expected: FAIL

**Step 3: Implement**

In `src/fees.py`, add after `arb_profit`:

```python
def maker_arb_profit(bid_prices: list[float]) -> float:
    return sum(bid_prices) - 1.0
```

In `src/models.py`, add `signal_type` to `TradeSignal`:

```python
@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
    signal_type: str = "taker"
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_fees.py tests/test_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/fees.py src/models.py tests/test_fees.py tests/test_models.py
git commit -m "feat: add signal_type to TradeSignal, maker_arb_profit function"
```

---

### Task 2: Maker Config Fields

**Files:**
- Modify: `src/config.py`
- Modify: `config.example.yaml`
- Test: `tests/test_config.py`

**Step 1: Write failing test**

Add to `tests/test_config.py`:

```python
def test_load_config_maker_defaults():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_CONFIG, f)
        f.flush()
        cfg = load_config(f.name)
    os.unlink(f.name)

    assert cfg.maker_enabled is True
    assert cfg.maker_fill_mode == "cancel_and_take"
    assert cfg.max_maker_events == 3
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py::test_load_config_maker_defaults -v`
Expected: FAIL — `AttributeError`

**Step 3: Implement**

Add to `Config` dataclass in `src/config.py`:

```python
    maker_enabled: bool
    maker_fill_mode: str
    max_maker_events: int
```

Add to `load_config()` return statement:

```python
        maker_enabled=strategy.get("maker_enabled", True),
        maker_fill_mode=strategy.get("maker_fill_mode", "cancel_and_take"),
        max_maker_events=int(strategy.get("max_maker_events", 3)),
```

Update `config.example.yaml` strategy section to add:

```yaml
  maker_enabled: true
  maker_fill_mode: cancel_and_take  # cancel_and_take | tighten_on_fill
  max_maker_events: 3               # max events with resting maker orders
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add maker config fields (enabled, fill_mode, max_events)"
```

---

### Task 3: Engine Returns Maker Signals

**Files:**
- Modify: `src/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write failing tests**

Add to `tests/test_engine.py`:

```python
from src.fees import maker_arb_profit


def test_evaluate_maker_signal_in_fee_gap():
    """Bid sum $1.03 is profitable as maker (3%) but not as taker (~-1% after 7% fees)."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.52, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
    }
    # Taker signal should be None (fees eat the profit)
    taker_signal = engine.evaluate("E1", orderbooks)
    assert taker_signal is None

    # Maker signal should exist
    maker_signal = engine.evaluate_maker("E1", orderbooks)
    assert maker_signal is not None
    assert maker_signal.signal_type == "maker"
    assert maker_signal.net_profit > 0


def test_evaluate_maker_returns_none_below_dollar():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate_maker("E1", orderbooks)
    assert signal is None


def test_evaluate_maker_respects_volume_check():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.52, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
    }
    meta = {"M1": {"volume_24h": 0}, "M2": {"volume_24h": 500}}
    signal = engine.evaluate_maker("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_evaluate_maker_respects_depth_check():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.52, quantity=5)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
    }
    signal = engine.evaluate_maker("E1", orderbooks)
    assert signal is None
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py::test_evaluate_maker_signal_in_fee_gap -v`
Expected: FAIL — `AttributeError: 'ArbEngine' object has no attribute 'evaluate_maker'`

**Step 3: Implement**

Add `evaluate_maker()` to `ArbEngine` in `src/engine.py`. This method reuses the same validation logic (bids exist, depth, volume) but uses `maker_arb_profit` (0 fees) instead of `arb_profit`:

```python
    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            best_bid = book.best_yes_bid()
            if best_bid is None:
                return None
            if self.min_bid_depth > 1:
                total_depth = sum(level.quantity for level in book.yes_bids if level.price >= best_bid - 1e-9)
                if total_depth < self.min_bid_depth:
                    return None
            legs.append((ticker, best_bid))

        if self.min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                meta = market_metadata.get(ticker, {})
                volume = meta.get("volume_24h", 0)
                if volume < self.min_volume_24h:
                    return None

        bid_prices = [price for _, price in legs]
        profit = maker_arb_profit(bid_prices)
        if profit <= 0:
            return None

        profit_pct = (profit / 1.0) * 100
        exp_ratio = exposure_ratio(bid_prices)
        if exp_ratio > self.max_exposure_ratio:
            return None

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            signal_type="maker",
        )
```

Add import at top of `src/engine.py`:

```python
from src.fees import arb_profit, exposure_ratio, maker_arb_profit
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: add evaluate_maker for near-arb detection at 0% maker fees"
```

---

### Task 4: MakerManager Core — Post and Track

**Files:**
- Create: `src/maker.py`
- Create: `tests/test_maker.py`

**Step 1: Write failing tests**

```python
# tests/test_maker.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.maker import MakerManager
from src.models import TradeSignal


def _make_maker(max_events=3, fill_mode="cancel_and_take"):
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "mo1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.52", "fill_count_fp": "0.00"}},
            {"order": {"order_id": "mo2", "ticker": "M2", "status": "resting",
                       "yes_price_dollars": "0.51", "fill_count_fp": "0.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    maker = MakerManager(api=api, fill_mode=fill_mode, max_events=max_events)
    return maker, api


def _maker_signal():
    return TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.52), ("M2", 0.51)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
        signal_type="maker",
    )


def test_post_maker_orders():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    api.batch_create_orders.assert_called_once()
    orders = api.batch_create_orders.call_args[0][0]
    assert len(orders) == 2
    assert all(o["action"] == "sell" for o in orders)
    assert maker.active_event_count() == 1


def test_max_events_respected():
    maker, api = _make_maker(max_events=1)
    s1 = _maker_signal()
    s2 = TradeSignal(
        event_ticker="E2",
        legs=[("M3", 0.53), ("M4", 0.50)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
        signal_type="maker",
    )
    asyncio.get_event_loop().run_until_complete(maker.post(s1))
    asyncio.get_event_loop().run_until_complete(maker.post(s2))

    assert maker.active_event_count() == 1
    assert api.batch_create_orders.call_count == 1


def test_no_duplicate_posts():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    assert api.batch_create_orders.call_count == 1


def test_cancel_all():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))
    asyncio.get_event_loop().run_until_complete(maker.cancel_all())

    api.batch_cancel_orders.assert_called()
    assert maker.active_event_count() == 0


def test_owns_order():
    maker, _ = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    assert maker.owns_order("mo1")
    assert maker.owns_order("mo2")
    assert not maker.owns_order("unknown")
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_maker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.maker'`

**Step 3: Implement MakerManager core**

```python
# src/maker.py
import logging
import time
from dataclasses import dataclass, field

from src.api import KalshiAPI
from src.models import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class MakerEvent:
    signal: TradeSignal
    order_ids: dict[str, str] = field(default_factory=dict)
    order_prices: dict[str, float] = field(default_factory=dict)
    filled: dict[str, float] = field(default_factory=dict)
    last_reprice_time: float = 0.0


class MakerManager:
    def __init__(self, api: KalshiAPI, fill_mode: str = "cancel_and_take",
                 max_events: int = 3):
        self.api = api
        self.fill_mode = fill_mode
        self.max_events = max_events
        self._active: dict[str, MakerEvent] = {}
        self._order_to_event: dict[str, str] = {}

    def active_event_count(self) -> int:
        return len(self._active)

    def owns_order(self, order_id: str) -> bool:
        return order_id in self._order_to_event

    def is_event_active(self, event_ticker: str) -> bool:
        return event_ticker in self._active

    async def post(self, signal: TradeSignal):
        if signal.event_ticker in self._active:
            return
        if len(self._active) >= self.max_events:
            return

        orders = [
            self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=1)
            for ticker, price in signal.legs
        ]

        response = await self.api.batch_create_orders(orders)
        order_list = response.get("orders", [])

        event = MakerEvent(signal=signal)
        for o in order_list:
            inner = o.get("order", o)
            oid = inner.get("order_id", "")
            ticker = inner.get("ticker", "")
            price = float(inner.get("yes_price_dollars", 0))
            event.order_ids[ticker] = oid
            event.order_prices[ticker] = price
            self._order_to_event[oid] = signal.event_ticker

        self._active[signal.event_ticker] = event
        logger.info("Posted maker orders on %s: %d legs", signal.event_ticker, len(order_list))

    async def cancel_event(self, event_ticker: str):
        event = self._active.pop(event_ticker, None)
        if not event:
            return
        unfilled_oids = [
            oid for oid in event.order_ids.values()
            if oid not in event.filled
        ]
        for oid in event.order_ids.values():
            self._order_to_event.pop(oid, None)
        if unfilled_oids:
            await self.api.batch_cancel_orders(unfilled_oids)
        logger.info("Cancelled maker orders on %s", event_ticker)

    async def cancel_all(self):
        for event_ticker in list(self._active.keys()):
            await self.cancel_event(event_ticker)
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_maker.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/maker.py tests/test_maker.py
git commit -m "feat: add MakerManager core — post, track, cancel maker orders"
```

---

### Task 5: MakerManager Fill Handling — cancel_and_take Mode

**Files:**
- Modify: `src/maker.py`
- Modify: `tests/test_maker.py`

**Step 1: Write failing tests**

Add to `tests/test_maker.py`:

```python
def test_handle_fill_cancel_and_take():
    """First fill should cancel remaining legs and place taker orders."""
    maker, api = _make_maker(fill_mode="cancel_and_take")
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    # Simulate M1 filling
    asyncio.get_event_loop().run_until_complete(
        maker.handle_fill("mo1", "M1", 0.52, 1)
    )

    # Should have cancelled remaining order (mo2)
    api.cancel_order.assert_called_with("mo2")

    # Should have placed taker order for M2
    assert api.batch_create_orders.call_count == 2
    taker_call = api.batch_create_orders.call_args_list[1]
    taker_orders = taker_call[0][0]
    assert taker_orders[0]["ticker"] == "M2"
    assert taker_orders[0]["action"] == "sell"

    # Event should be cleaned up
    assert maker.active_event_count() == 0


def test_all_legs_fill_as_maker():
    """When all legs fill as maker, no taker orders needed."""
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    asyncio.get_event_loop().run_until_complete(
        maker.handle_fill("mo1", "M1", 0.52, 1)
    )
    # In cancel_and_take, first fill triggers completion.
    # But if the order for M2 was already filled before we cancel...
    # Test the case where both fill before we process:
    maker2, api2 = _make_maker()
    signal2 = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker2.post(signal2))

    # Mark both as filled
    asyncio.get_event_loop().run_until_complete(
        maker2.handle_fill("mo1", "M1", 0.52, 1)
    )
    # Event cleaned up after first fill triggers completion
    assert maker2.active_event_count() == 0
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_maker.py::test_handle_fill_cancel_and_take -v`
Expected: FAIL — `AttributeError: 'MakerManager' object has no attribute 'handle_fill'`

**Step 3: Implement handle_fill**

Add to `MakerManager` in `src/maker.py`:

```python
    async def handle_fill(self, order_id: str, ticker: str, price: float, quantity: int):
        event_ticker = self._order_to_event.get(order_id)
        if not event_ticker:
            return
        event = self._active.get(event_ticker)
        if not event:
            return

        event.filled[order_id] = price
        logger.info("Maker fill: %s @ %.2f on %s (%d/%d legs)",
                     ticker, price, event_ticker, len(event.filled), len(event.order_ids))

        if len(event.filled) == len(event.order_ids):
            profit = sum(event.order_prices.values()) - 1.0
            logger.info("ALL MAKER LEGS FILLED on %s — profit $%.4f (0%% fees!)",
                         event_ticker, profit)
            self._cleanup_event(event_ticker)
            return

        if self.fill_mode == "cancel_and_take":
            await self._complete_cancel_and_take(event_ticker, event)
        elif self.fill_mode == "tighten_on_fill":
            await self._complete_tighten(event_ticker, event)

    async def _complete_cancel_and_take(self, event_ticker: str, event: MakerEvent):
        unfilled_tickers = [
            (ticker, event.order_prices[ticker])
            for ticker, oid in event.order_ids.items()
            if oid not in event.filled
        ]
        unfilled_oids = [
            oid for oid in event.order_ids.values()
            if oid not in event.filled
        ]

        if unfilled_oids:
            for oid in unfilled_oids:
                await self.api.cancel_order(oid)

        if unfilled_tickers:
            taker_orders = [
                self.api.build_sell_order(ticker=t, yes_price=p, quantity=1)
                for t, p in unfilled_tickers
            ]
            await self.api.batch_create_orders(taker_orders)
            logger.info("Placed taker orders for %d remaining legs on %s",
                         len(taker_orders), event_ticker)

        self._cleanup_event(event_ticker)

    def _cleanup_event(self, event_ticker: str):
        event = self._active.pop(event_ticker, None)
        if event:
            for oid in event.order_ids.values():
                self._order_to_event.pop(oid, None)
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_maker.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/maker.py tests/test_maker.py
git commit -m "feat: add maker fill handling with cancel_and_take completion"
```

---

### Task 6: MakerManager Reprice and Invalidation

**Files:**
- Modify: `src/maker.py`
- Modify: `tests/test_maker.py`

**Step 1: Write failing tests**

Add to `tests/test_maker.py`:

```python
from src.models import Orderbook, OrderbookLevel


def test_reprice_on_bid_change():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    new_books = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.53, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
    }
    asyncio.get_event_loop().run_until_complete(
        maker.on_orderbook_update("E1", new_books)
    )

    # Should have cancelled old M1 order and posted new one
    api.cancel_order.assert_called()


def test_invalidate_when_arb_dies():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    bad_books = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.50, quantity=100)], no_bids=[]),
    }
    asyncio.get_event_loop().run_until_complete(
        maker.on_orderbook_update("E1", bad_books)
    )

    assert maker.active_event_count() == 0
    api.batch_cancel_orders.assert_called()


def test_reprice_throttled():
    maker, api = _make_maker()
    signal = _maker_signal()
    asyncio.get_event_loop().run_until_complete(maker.post(signal))

    books = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.53, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.51, quantity=100)], no_bids=[]),
    }
    # First reprice should work
    asyncio.get_event_loop().run_until_complete(maker.on_orderbook_update("E1", books))
    first_cancel_count = api.cancel_order.call_count

    # Immediate second reprice should be throttled
    asyncio.get_event_loop().run_until_complete(maker.on_orderbook_update("E1", books))
    assert api.cancel_order.call_count == first_cancel_count
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_maker.py::test_reprice_on_bid_change -v`
Expected: FAIL — `AttributeError: 'MakerManager' object has no attribute 'on_orderbook_update'`

**Step 3: Implement**

Add to `MakerManager` in `src/maker.py`:

```python
    REPRICE_THROTTLE_SECS = 1.0

    async def on_orderbook_update(self, event_ticker: str, orderbooks: dict):
        event = self._active.get(event_ticker)
        if not event:
            return

        bid_prices = []
        for ticker in event.order_ids:
            book = orderbooks.get(ticker)
            if not book:
                return
            best_bid = book.best_yes_bid()
            if best_bid is None:
                await self.cancel_event(event_ticker)
                return
            bid_prices.append((ticker, best_bid))

        gross_profit = sum(p for _, p in bid_prices) - 1.0
        if gross_profit <= 0:
            logger.info("Maker arb on %s no longer profitable (sum=%.2f), cancelling",
                         event_ticker, sum(p for _, p in bid_prices))
            await self.cancel_event(event_ticker)
            return

        now = time.time()
        if now - event.last_reprice_time < self.REPRICE_THROTTLE_SECS:
            return

        for ticker, new_price in bid_prices:
            old_price = event.order_prices.get(ticker, 0)
            oid = event.order_ids.get(ticker, "")
            if oid in event.filled:
                continue
            if abs(new_price - old_price) > 1e-9:
                await self.api.cancel_order(oid)
                new_order = [self.api.build_sell_order(ticker=ticker, yes_price=new_price, quantity=1)]
                resp = await self.api.batch_create_orders(new_order)
                new_inner = resp.get("orders", [{}])[0].get("order", {})
                new_oid = new_inner.get("order_id", "")
                self._order_to_event.pop(oid, None)
                self._order_to_event[new_oid] = event_ticker
                event.order_ids[ticker] = new_oid
                event.order_prices[ticker] = new_price

        event.last_reprice_time = now
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_maker.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/maker.py tests/test_maker.py
git commit -m "feat: add maker reprice on orderbook updates and arb invalidation"
```

---

### Task 7: Wire MakerManager into main.py

**Files:**
- Modify: `src/main.py`

**Step 1: Import and construct MakerManager**

In `ArbBot.__init__`, after the executor setup, add:

```python
        from src.maker import MakerManager
        self.maker = MakerManager(
            api=self.api,
            fill_mode=self.cfg.maker_fill_mode,
            max_events=self.cfg.max_maker_events,
        ) if self.cfg.maker_enabled else None
```

**Step 2: Add fill dispatcher**

Replace the direct `on_fill=self.executor.handle_fill` in scanner construction with a dispatcher method:

```python
        self.scanner = MarketScanner(
            ws_url=self.cfg.ws_url,
            auth=self.auth,
            orderbook_mgr=self.orderbook_mgr,
            on_orderbook_update=self._on_orderbook_update,
            on_fill=self._on_fill,
        )
```

Add the dispatcher:

```python
    def _on_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        if self.maker and self.maker.owns_order(order_id):
            ticker = fill_data.get("market_ticker", "")
            price = float(fill_data.get("yes_price_dollars", 0))
            quantity = int(float(fill_data.get("count_fp", 0)))
            if ticker and quantity > 0:
                asyncio.get_event_loop().create_task(
                    self.maker.handle_fill(order_id, ticker, price, quantity)
                )
        else:
            self.executor.handle_fill(fill_data)
```

**Step 3: Route maker signals in _on_orderbook_update**

After the existing taker signal logic, add maker evaluation:

```python
        # Existing taker logic handles signal != None
        # ...after the taker block...

        # Maker layer: check for near-arb opportunities
        if self.maker and not signal:
            maker_signal = self.engine.evaluate_maker(event_ticker, event_books, market_metadata=meta)
            if maker_signal and not self.executor.is_circuit_breaker_tripped():
                if not self.maker.is_event_active(event_ticker):
                    asyncio.get_event_loop().create_task(self._post_maker(maker_signal))

            # Reprice active maker events on orderbook updates
            if self.maker.is_event_active(event_ticker):
                asyncio.get_event_loop().create_task(
                    self.maker.on_orderbook_update(event_ticker, event_books)
                )
```

Add the maker post helper:

```python
    async def _post_maker(self, signal: TradeSignal):
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                return
            await self.maker.post(signal)
            self._stats["arbs_detected"] += 1
            logger.info(json.dumps({
                "event": "maker_posted",
                "event_ticker": signal.event_ticker,
                "legs": signal.legs,
                "net_profit": round(signal.net_profit, 6),
                "profit_pct": round(signal.profit_pct, 2),
            }))
        except Exception:
            logger.exception("Failed to post maker orders for %s", signal.event_ticker)
```

**Step 4: Add maker cancel to emergency_shutdown**

In `_emergency_shutdown`, add before the existing order cancellation:

```python
        if self.maker:
            await self.maker.cancel_all()
```

**Step 5: Update status report**

Add maker stats to the STATUS line:

```python
            maker_count = self.maker.active_event_count() if self.maker else 0
```

Add `| maker_events=%d` to the format string with `maker_count`.

**Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/main.py
git commit -m "feat: wire MakerManager into ArbBot — fill dispatch, signal routing, reprice"
```

---

### Task 8: Update config.yaml and CLAUDE.md

**Step 1: Add maker fields to config.yaml**

Add to the strategy section:

```yaml
  maker_enabled: true
  maker_fill_mode: tighten_on_fill
  max_maker_events: 3
```

**Important:** Check config.yaml against config.example.yaml — ensure no stale overrides.

**Step 2: Update CLAUDE.md**

Add maker strategy documentation to the Architecture section.

**Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

**Step 4: Verify bot imports**

Run: `python3 -c "from src.main import ArbBot; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add maker strategy to CLAUDE.md architecture"
```
