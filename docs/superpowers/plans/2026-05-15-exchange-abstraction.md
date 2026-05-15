# Exchange Abstraction Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the codebase into a ports & adapters architecture, separating exchange-agnostic trading logic from Kalshi-specific API implementations.

**Architecture:** Ports & adapters. Core logic (`src/core/`) depends only on abstract Protocol interfaces (`src/ports/`). Kalshi-specific code lives in `src/exchanges/kalshi/`. Strategies move to `src/strategies/`. `src/main.py` is the composition root.

**Tech Stack:** Python 3.12+, asyncio, typing.Protocol, dataclasses, aiohttp, websockets, pytest

**Spec:** `docs/superpowers/specs/2026-05-15-exchange-abstraction-design.md`

---

## File Structure

### New files to create:
- `src/core/__init__.py`
- `src/core/models.py` — refactored Orderbook (bids/asks), Fill dataclass, existing Market/Event/TradeSignal with exchange field
- `src/core/fees.py` — generic fee functions accepting FeeModel
- `src/core/engine.py` — thin ArbEngine coordinator
- `src/core/dispatch.py` — Dispatcher (moved from src/dispatch.py)
- `src/core/risk.py` — RiskProfile + load_risk_profile (moved from src/risk.py)
- `src/core/recorder.py` — DataRecorder (moved from src/recorder.py)
- `src/core/replay.py` — ReplayEngine (moved from src/replay.py)
- `src/core/analytics.py` — Analytics (moved from src/analytics.py)
- `src/core/positions.py` — PositionTracker (moved from src/positions.py)
- `src/core/orderbook_manager.py` — OrderbookManager (extracted from src/scanner.py)
- `src/ports/__init__.py`
- `src/ports/exchange.py` — ExchangeAPI Protocol
- `src/ports/feed.py` — OrderbookFeed Protocol
- `src/ports/discovery.py` — MarketDiscovery Protocol
- `src/ports/order_builder.py` — OrderBuilder Protocol
- `src/ports/fee_model.py` — FeeModel Protocol
- `src/ports/constraints.py` — PositionConstraints Protocol
- `src/exchanges/__init__.py` — create_exchange() factory
- `src/exchanges/kalshi/__init__.py` — KalshiExchange facade
- `src/exchanges/kalshi/api.py` — KalshiAPI (moved from src/api.py)
- `src/exchanges/kalshi/auth.py` — KalshiAuth (moved from src/auth.py)
- `src/exchanges/kalshi/scanner.py` — KalshiScanner (MarketScanner from src/scanner.py)
- `src/exchanges/kalshi/discovery.py` — KalshiDiscovery (from src/discovery.py)
- `src/exchanges/kalshi/fee_model.py` — KalshiFeeModel
- `src/exchanges/kalshi/order_builder.py` — KalshiOrderBuilder
- `src/exchanges/kalshi/constraints.py` — KalshiConstraints
- `src/strategies/__init__.py`
- `src/strategies/taker.py` — evaluate_sell_side(), evaluate_buy_side()
- `src/strategies/maker.py` — MakerManager (moved from src/maker.py)
- `src/strategies/two_sided.py` — TwoSidedManager (moved from src/two_sided.py)
- `src/strategies/near_expiry.py` — evaluate()
- `src/strategies/monotone.py` — evaluate()
- `tests/test_ports.py` — Protocol conformance tests
- `tests/test_import_isolation.py` — architectural boundary enforcement
- `tests/core/` — tests for core modules (moved from tests/)
- `tests/exchanges/kalshi/` — tests for Kalshi adapter
- `tests/strategies/` — tests for strategy modules

### Files to modify:
- `src/executor.py` — receive ExchangeAPI + OrderBuilder ports instead of KalshiAPI
- `src/main.py` — use exchange factory, new wiring
- `src/config.py` — add exchange field, nested credentials
- `src/mcp_server.py` — update imports
- `config.example.yaml` — add exchange field, document nested credentials
- `CLAUDE.md` — update architecture docs, module paths, data flow
- `.claude/skills/` — update skills referencing old paths

### Files to delete (after moving):
- `src/api.py`, `src/auth.py`, `src/scanner.py`, `src/discovery.py`, `src/maker.py`, `src/two_sided.py`
- `src/models.py`, `src/fees.py`, `src/risk.py`, `src/recorder.py`, `src/replay.py`, `src/analytics.py`, `src/positions.py`, `src/dispatch.py`
- `src/engine.py` (replaced by core/engine.py + strategies/)

---

## Task 1: Port Interfaces

**Files:**
- Create: `src/ports/__init__.py`
- Create: `src/ports/fee_model.py`
- Create: `src/ports/exchange.py`
- Create: `src/ports/order_builder.py`
- Create: `src/ports/feed.py`
- Create: `src/ports/discovery.py`
- Create: `src/ports/constraints.py`
- Test: `tests/test_ports.py`

- [ ] **Step 1: Create ports package with FeeModel protocol**

```python
# src/ports/__init__.py
from src.ports.fee_model import FeeModel
from src.ports.exchange import ExchangeAPI
from src.ports.order_builder import OrderBuilder
from src.ports.feed import OrderbookFeed
from src.ports.discovery import MarketDiscovery
from src.ports.constraints import PositionConstraints

__all__ = [
    "FeeModel", "ExchangeAPI", "OrderBuilder",
    "OrderbookFeed", "MarketDiscovery", "PositionConstraints",
]
```

```python
# src/ports/fee_model.py
from typing import Protocol


class FeeModel(Protocol):
    def taker_fee(self, price: float) -> float: ...
    def maker_fee(self, price: float) -> float: ...
    def profit_fee(self, gross_profit: float) -> float: ...
```

- [ ] **Step 2: Create ExchangeAPI protocol**

```python
# src/ports/exchange.py
from typing import Protocol


class ExchangeAPI(Protocol):
    async def batch_create_orders(self, orders: list[dict]) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...
    async def batch_cancel_orders(self, order_ids: list[str]) -> dict: ...
    async def get_positions(self) -> dict: ...
    async def get_open_orders(self) -> dict: ...
    async def get_balance(self) -> dict: ...
    async def get_market_trades(self, ticker: str, limit: int = 10) -> dict: ...
    async def close(self) -> None: ...
```

- [ ] **Step 3: Create OrderBuilder protocol**

```python
# src/ports/order_builder.py
from typing import Protocol


class OrderBuilder(Protocol):
    def build_sell_order(self, ticker: str, price: float, quantity: int) -> dict: ...
    def build_buy_order(self, ticker: str, price: float, quantity: int) -> dict: ...
    def build_close_order(self, ticker: str, quantity: int) -> dict: ...
    def unwrap_order(self, raw: dict) -> dict: ...
```

- [ ] **Step 4: Create OrderbookFeed protocol**

```python
# src/ports/feed.py
from typing import Protocol


class OrderbookFeed(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self, market_tickers: list[str]) -> None: ...
    async def subscribe_fills(self) -> None: ...
    async def listen(self) -> None: ...
    async def close(self) -> None: ...
    def stop(self) -> None: ...
```

- [ ] **Step 5: Create MarketDiscovery protocol**

```python
# src/ports/discovery.py
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.core.models import Event


class MarketDiscovery(Protocol):
    async def full_scan(self) -> None: ...
    async def poll_loop(self, interval_secs: int) -> None: ...
    async def cleanup_loop(self) -> None: ...
    def register_events(self, events: list[Event]) -> list[str]: ...
    def cleanup_expired(self) -> set[str]: ...
```

- [ ] **Step 6: Create PositionConstraints protocol**

```python
# src/ports/constraints.py
from typing import Protocol


class PositionConstraints(Protocol):
    def max_position_size(self, ticker: str) -> int | None: ...
    def max_total_exposure(self) -> float | None: ...
```

- [ ] **Step 7: Write protocol type-check test**

```python
# tests/test_ports.py
"""Verify that Protocol classes are importable and structurally sound."""
from src.ports import (
    FeeModel, ExchangeAPI, OrderBuilder,
    OrderbookFeed, MarketDiscovery, PositionConstraints,
)


def test_protocols_importable():
    assert FeeModel is not None
    assert ExchangeAPI is not None
    assert OrderBuilder is not None
    assert OrderbookFeed is not None
    assert MarketDiscovery is not None
    assert PositionConstraints is not None
```

- [ ] **Step 8: Run tests**

Run: `python3 -m pytest tests/test_ports.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/ports/ tests/test_ports.py
git commit -m "feat: add port interfaces for exchange abstraction"
```

---

## Task 2: Core Models Refactor

**Files:**
- Create: `src/core/__init__.py`
- Create: `src/core/models.py`
- Test: `tests/test_models.py` (update existing)

- [ ] **Step 1: Write failing tests for new Orderbook (bids/asks)**

Add to `tests/test_models.py`:

```python
def test_orderbook_bids_asks():
    """New bid/ask model replaces yes_bids/no_bids."""
    from src.core.models import Orderbook
    book = Orderbook(
        bids={55: 10.0, 50: 20.0},
        asks={57: 5.0, 60: 15.0},
    )
    assert book.best_bid() == 0.55
    assert book.best_ask() == 0.57
    assert book.bid_depth_at(0.50) == 30.0
    assert book.bid_depth_at(0.55) == 10.0
    assert book.ask_depth_at(0.60) == 15.0
    assert book.ask_depth_at(0.57) == 20.0  # 5 + 15


def test_orderbook_empty():
    from src.core.models import Orderbook
    book = Orderbook()
    assert book.best_bid() is None
    assert book.best_ask() is None
    assert book.bid_depth_at(0.50) == 0.0
    assert book.ask_depth_at(0.50) == 0.0


def test_fill_dataclass():
    from src.core.models import Fill
    fill = Fill(
        order_id="abc", ticker="T-1", price=0.55,
        quantity=1, side="sell", exchange="kalshi", timestamp=1000.0,
    )
    assert fill.exchange == "kalshi"
    assert fill.side == "sell"


def test_event_exchange_field():
    from src.core.models import Event, Market
    m = Market(ticker="T-1", event_ticker="E-1", title="M1", status="active", exchange="kalshi")
    e = Event(event_ticker="E-1", title="Ev", series_ticker="", mutually_exclusive=True, markets=[m], exchange="kalshi")
    assert e.exchange == "kalshi"
    assert m.exchange == "kalshi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_models.py::test_orderbook_bids_asks tests/test_models.py::test_fill_dataclass tests/test_models.py::test_event_exchange_field -v`
Expected: FAIL (ImportError — src.core.models doesn't exist yet)

- [ ] **Step 3: Create `src/core/__init__.py`**

```python
# src/core/__init__.py
```

- [ ] **Step 4: Create `src/core/models.py` with refactored Orderbook, Fill, and updated Event/Market**

```python
# src/core/models.py
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Orderbook:
    bids: dict[int, float] = field(default_factory=dict)
    asks: dict[int, float] = field(default_factory=dict)

    def best_bid(self) -> float | None:
        if not self.bids:
            return None
        return max(self.bids) / 100.0

    def best_ask(self) -> float | None:
        if not self.asks:
            return None
        return min(self.asks) / 100.0

    def bid_depth_at(self, price: float) -> float:
        return sum(
            qty for cents, qty in self.bids.items()
            if cents >= round(price * 100)
        )

    def ask_depth_at(self, price: float) -> float:
        return sum(
            qty for cents, qty in self.asks.items()
            if cents <= round(price * 100)
        )


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str
    close_time: str = ""
    expected_expiration_time: str = ""
    exchange: str = "kalshi"
    volume_24h: float = 0.0
    open_interest: float = 0.0
    liquidity: float = 0.0


@dataclass
class Event:
    event_ticker: str
    title: str
    series_ticker: str
    mutually_exclusive: bool
    markets: list[Market] = field(default_factory=list)
    total_market_count: int = 0
    exchange: str = "kalshi"

    def market_tickers(self) -> list[str]:
        return [m.ticker for m in self.markets]


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"


@dataclass
class Order:
    order_id: str
    ticker: str
    action: str
    side: str
    price: float
    quantity: float
    status: OrderStatus
    filled_quantity: float = 0.0


@dataclass
class Position:
    ticker: str
    side: str
    quantity: float
    avg_price: float


@dataclass
class Fill:
    order_id: str
    ticker: str
    price: float
    quantity: int
    side: str
    exchange: str
    timestamp: float


@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
    signal_type: str = "taker"
    quantity: int = 1
    leg_actions: list[str] | None = None

    def __post_init__(self):
        if self.leg_actions is not None and len(self.leg_actions) != len(self.legs):
            raise ValueError(
                f"leg_actions length {len(self.leg_actions)} must match legs length {len(self.legs)}"
            )
```

- [ ] **Step 5: Run new tests**

Run: `python3 -m pytest tests/test_models.py::test_orderbook_bids_asks tests/test_models.py::test_orderbook_empty tests/test_models.py::test_fill_dataclass tests/test_models.py::test_event_exchange_field -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/ tests/test_models.py
git commit -m "feat: add core models with bid/ask orderbook, Fill dataclass, exchange field"
```

---

## Task 3: Core Fee Functions with FeeModel Injection

**Files:**
- Create: `src/core/fees.py`
- Test: `tests/test_fees.py` (update existing)

- [ ] **Step 1: Write failing tests for parameterized fee functions**

Add to `tests/test_fees.py`:

```python
class MockFeeModel:
    """Kalshi-equivalent fee model for testing."""
    def taker_fee(self, price: float) -> float:
        return 0.07 * price * (1.0 - price)
    def maker_fee(self, price: float) -> float:
        return 0.0
    def profit_fee(self, gross_profit: float) -> float:
        return 0.0


class ProfitTaxFeeModel:
    """PredictIt-style: 10% profit fee, no per-trade fees."""
    def taker_fee(self, price: float) -> float:
        return 0.0
    def maker_fee(self, price: float) -> float:
        return 0.0
    def profit_fee(self, gross_profit: float) -> float:
        return 0.10 * gross_profit


def test_core_arb_profit_kalshi():
    from src.core.fees import arb_profit
    fm = MockFeeModel()
    profit = arb_profit([0.40, 0.40, 0.40], fm)
    expected_gross = 1.20 - 1.0
    expected_fees = 3 * 0.07 * 0.40 * 0.60
    assert abs(profit - (expected_gross - expected_fees)) < 1e-9


def test_core_arb_profit_predictit():
    from src.core.fees import arb_profit
    fm = ProfitTaxFeeModel()
    profit = arb_profit([0.40, 0.40, 0.40], fm)
    gross = 0.20
    expected = gross - 0.10 * gross  # 10% profit tax
    assert abs(profit - expected) < 1e-9


def test_core_buy_side_arb_profit():
    from src.core.fees import buy_side_arb_profit
    fm = MockFeeModel()
    profit = buy_side_arb_profit([0.20, 0.30, 0.40], fm)
    cost = 0.90
    fees = sum(0.07 * p * (1 - p) for p in [0.20, 0.30, 0.40])
    expected = 1.0 - cost - fees
    assert abs(profit - expected) < 1e-9


def test_core_maker_arb_profit():
    from src.core.fees import maker_arb_profit
    fm = MockFeeModel()
    profit = maker_arb_profit([0.40, 0.40, 0.40], fm)
    assert abs(profit - 0.20) < 1e-9  # 0% maker fees


def test_core_exposure_ratio():
    from src.core.fees import exposure_ratio
    fm = MockFeeModel()
    ratio = exposure_ratio([0.40, 0.40, 0.40], fm)
    assert ratio > 0
    assert ratio < float("inf")


def test_core_monotone_pair_profit():
    from src.core.fees import monotone_pair_profit
    fm = MockFeeModel()
    profit = monotone_pair_profit(0.60, 0.40, fm)
    gross = 0.60 - 0.40
    fees = fm.taker_fee(0.60) + fm.taker_fee(0.40)
    assert abs(profit - (gross - fees)) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_fees.py::test_core_arb_profit_kalshi -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `src/core/fees.py`**

```python
# src/core/fees.py
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ports.fee_model import FeeModel


def taker_fee(price: float, fee_model: FeeModel) -> float:
    return fee_model.taker_fee(price)


def arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.taker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def maker_arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.maker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def maker_exposure_ratio(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.maker_fee(p) for p in bid_prices)
    net_premium = gross - fees
    if net_premium <= 0:
        return float("inf")
    worst_loss = max(0.0, 1.0 - (sum(bid_prices) - max(bid_prices)))
    return worst_loss / net_premium


def monotone_pair_profit(upper_bid: float, lower_ask: float, fee_model: FeeModel) -> float:
    gross = upper_bid - lower_ask
    fees = fee_model.taker_fee(upper_bid) + fee_model.taker_fee(lower_ask)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def buy_side_arb_profit(ask_prices: list[float], fee_model: FeeModel) -> float:
    gross = 1.0 - sum(ask_prices)
    fees = sum(fee_model.taker_fee(p) for p in ask_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))


def exposure_ratio(bid_prices: list[float], fee_model: FeeModel) -> float:
    premiums = sum(bid_prices)
    fees = sum(fee_model.taker_fee(p) for p in bid_prices)
    net_premium = premiums - 1.0 - fees
    if net_premium <= 0:
        return float("inf")
    filled_fees = fees - fee_model.taker_fee(max(bid_prices))
    worst_loss = max(0.0, 1.0 - (premiums - max(bid_prices)) + filled_fees)
    return worst_loss / net_premium
```

- [ ] **Step 4: Run all new fee tests**

Run: `python3 -m pytest tests/test_fees.py::test_core_arb_profit_kalshi tests/test_fees.py::test_core_arb_profit_predictit tests/test_fees.py::test_core_buy_side_arb_profit tests/test_fees.py::test_core_maker_arb_profit tests/test_fees.py::test_core_exposure_ratio tests/test_fees.py::test_core_monotone_pair_profit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/fees.py tests/test_fees.py
git commit -m "feat: add core fee functions with FeeModel injection"
```

---

## Task 4: Kalshi Fee Model, Order Builder, and Constraints

**Files:**
- Create: `src/exchanges/__init__.py`
- Create: `src/exchanges/kalshi/__init__.py`
- Create: `src/exchanges/kalshi/fee_model.py`
- Create: `src/exchanges/kalshi/order_builder.py`
- Create: `src/exchanges/kalshi/constraints.py`
- Test: `tests/test_ports.py` (add conformance tests)

- [ ] **Step 1: Write failing conformance tests**

Add to `tests/test_ports.py`:

```python
def test_kalshi_fee_model_conforms():
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    fm = KalshiFeeModel()
    assert abs(fm.taker_fee(0.50) - 0.07 * 0.50 * 0.50) < 1e-9
    assert fm.maker_fee(0.50) == 0.0
    assert fm.profit_fee(1.0) == 0.0


def test_kalshi_order_builder_conforms():
    from src.exchanges.kalshi.order_builder import KalshiOrderBuilder
    ob = KalshiOrderBuilder()
    sell = ob.build_sell_order("T-1", 0.55, 1)
    assert sell["ticker"] == "T-1"
    assert sell["action"] == "sell"
    assert sell["yes_price"] == 55

    buy = ob.build_buy_order("T-1", 0.40, 2)
    assert buy["action"] == "buy"
    assert buy["yes_price"] == 40
    assert buy["count"] == 2

    close_long = ob.build_close_order("T-1", 1)
    assert close_long["action"] == "sell"
    assert close_long["yes_price"] == 1

    close_short = ob.build_close_order("T-1", -1)
    assert close_short["action"] == "buy"
    assert close_short["yes_price"] == 99

    unwrapped = ob.unwrap_order({"order": {"order_id": "abc"}})
    assert unwrapped == {"order_id": "abc"}


def test_kalshi_constraints_conforms():
    from src.exchanges.kalshi.constraints import KalshiConstraints
    c = KalshiConstraints()
    assert c.max_position_size("T-1") is None
    assert c.max_total_exposure() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ports.py::test_kalshi_fee_model_conforms -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Create exchange package and Kalshi fee model**

```python
# src/exchanges/__init__.py
```

```python
# src/exchanges/kalshi/__init__.py
```

```python
# src/exchanges/kalshi/fee_model.py
class KalshiFeeModel:
    TAKER_FEE_RATE = 0.07

    def taker_fee(self, price: float) -> float:
        return self.TAKER_FEE_RATE * price * (1.0 - price)

    def maker_fee(self, price: float) -> float:
        return 0.0

    def profit_fee(self, gross_profit: float) -> float:
        return 0.0
```

- [ ] **Step 4: Create Kalshi order builder**

```python
# src/exchanges/kalshi/order_builder.py
class KalshiOrderBuilder:
    def build_sell_order(self, ticker: str, price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }

    def build_buy_order(self, ticker: str, price: float, quantity: int) -> dict:
        return {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }

    def build_close_order(self, ticker: str, quantity: int) -> dict:
        if quantity < 0:
            return {
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": abs(quantity),
            }
        return {
            "ticker": ticker, "action": "sell", "side": "yes",
            "type": "limit", "yes_price": 1, "count": quantity,
        }

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)
```

- [ ] **Step 5: Create Kalshi constraints**

```python
# src/exchanges/kalshi/constraints.py
class KalshiConstraints:
    def max_position_size(self, ticker: str) -> int | None:
        return None

    def max_total_exposure(self) -> float | None:
        return None
```

- [ ] **Step 6: Run conformance tests**

Run: `python3 -m pytest tests/test_ports.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/exchanges/ tests/test_ports.py
git commit -m "feat: add Kalshi fee model, order builder, and constraints adapters"
```

---

## Task 5: Core OrderbookManager Extraction

**Files:**
- Create: `src/core/orderbook_manager.py`
- Test: `tests/test_scanner.py` (update imports for OrderbookManager tests)

- [ ] **Step 1: Write failing test for core OrderbookManager with new Orderbook model**

Add to `tests/test_scanner.py`:

```python
def test_core_orderbook_manager_snapshot():
    """OrderbookManager builds Orderbook with bids/asks from raw snapshot data."""
    from src.core.orderbook_manager import OrderbookManager
    mgr = OrderbookManager()
    mgr.register_event("E-1", ["T-1"])
    mgr.apply_snapshot("T-1", {
        "bids": {55: 10.0, 50: 20.0},
        "asks": {57: 5.0, 60: 15.0},
    })
    book = mgr.get_orderbook("T-1")
    assert book is not None
    assert book.best_bid() == 0.55
    assert book.best_ask() == 0.57


def test_core_orderbook_manager_delta():
    from src.core.orderbook_manager import OrderbookManager
    mgr = OrderbookManager()
    mgr.register_event("E-1", ["T-1"])
    mgr.apply_snapshot("T-1", {"bids": {55: 10.0}, "asks": {57: 5.0}})
    mgr.apply_delta("T-1", {"price_cents": 55, "delta_qty": -3.0, "side": "bid"})
    book = mgr.get_orderbook("T-1")
    assert book.bids[55] == 7.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scanner.py::test_core_orderbook_manager_snapshot -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `src/core/orderbook_manager.py`**

This is the exchange-agnostic orderbook store. It works with the new `Orderbook` (bids/asks) model. Exchange-specific feed adapters are responsible for converting their native format into `{bids: {cents: qty}, asks: {cents: qty}}` before calling `apply_snapshot`.

```python
# src/core/orderbook_manager.py
import time

from src.core.models import Orderbook


class OrderbookManager:
    def __init__(self):
        self._books: dict[str, Orderbook] = {}
        self._event_markets: dict[str, list[str]] = {}
        self._market_to_event: dict[str, str] = {}
        self._last_update_ts: dict[str, float] = {}

    def register_event(self, event_ticker: str, market_tickers: list[str]):
        self._event_markets[event_ticker] = market_tickers
        for t in market_tickers:
            self._market_to_event[t] = event_ticker

    def unregister_event(self, event_ticker: str):
        tickers = self._event_markets.pop(event_ticker, [])
        for t in tickers:
            self._market_to_event.pop(t, None)
            self._books.pop(t, None)
            self._last_update_ts.pop(t, None)

    def get_event_for_market(self, market_ticker: str) -> str | None:
        return self._market_to_event.get(market_ticker)

    def apply_snapshot(self, ticker: str, snapshot: dict):
        self._books[ticker] = Orderbook(
            bids=dict(snapshot.get("bids", {})),
            asks=dict(snapshot.get("asks", {})),
        )
        self._last_update_ts[ticker] = time.time()

    def apply_delta(self, ticker: str, delta: dict):
        book = self._books.get(ticker)
        if book is None:
            return
        price_cents = delta["price_cents"]
        delta_qty = delta["delta_qty"]
        side = delta["side"]
        levels = book.bids if side == "bid" else book.asks

        new_qty = levels.get(price_cents, 0) + delta_qty
        if new_qty <= 0:
            levels.pop(price_cents, None)
        else:
            levels[price_cents] = new_qty
        self._last_update_ts[ticker] = time.time()

    def market_age(self, ticker: str) -> float:
        ts = self._last_update_ts.get(ticker)
        if ts is None:
            return float("inf")
        return time.time() - ts

    def get_orderbook(self, ticker: str) -> Orderbook | None:
        return self._books.get(ticker)

    def get_event_markets(self, event_ticker: str) -> list[str]:
        return self._event_markets.get(event_ticker, [])

    def get_registered_market_count(self, event_ticker: str) -> int:
        return len(self._event_markets.get(event_ticker, []))

    def get_event_orderbooks(self, event_ticker: str) -> dict[str, Orderbook]:
        tickers = self._event_markets.get(event_ticker, [])
        result = {}
        for t in tickers:
            book = self._books.get(t)
            if book:
                result[t] = book
        return result
```

- [ ] **Step 4: Run new tests**

Run: `python3 -m pytest tests/test_scanner.py::test_core_orderbook_manager_snapshot tests/test_scanner.py::test_core_orderbook_manager_delta -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/orderbook_manager.py tests/test_scanner.py
git commit -m "feat: extract exchange-agnostic OrderbookManager to core"
```

---

## Task 6: Strategy Extraction — Taker

**Files:**
- Create: `src/strategies/__init__.py`
- Create: `src/strategies/taker.py`
- Test: `tests/test_engine.py` (add tests that call strategy functions directly)

- [ ] **Step 1: Write failing test for sell-side strategy**

Add to `tests/test_engine.py`:

```python
def test_taker_evaluate_sell_side():
    from src.core.models import Orderbook
    from src.core.risk import RiskProfile, load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_sell_side

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    books = {
        "T-1": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
        "T-2": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = evaluate_sell_side("E-1", books, meta, fm, rp)
    assert signal is not None
    assert signal.signal_type == "taker"
    assert signal.net_profit > 0


def test_taker_evaluate_sell_side_no_arb():
    from src.core.models import Orderbook
    from src.core.risk import RiskProfile, load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.taker import evaluate_sell_side

    fm = KalshiFeeModel()
    rp = load_risk_profile("conservative", {})
    books = {
        "T-1": Orderbook(bids={30: 10.0}, asks={32: 5.0}),
        "T-2": Orderbook(bids={30: 10.0}, asks={32: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = evaluate_sell_side("E-1", books, meta, fm, rp)
    assert signal is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py::test_taker_evaluate_sell_side -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Create strategies package and implement taker.py**

```python
# src/strategies/__init__.py
```

Extract `evaluate()` and `evaluate_buy_side()` from `src/engine.py` into `src/strategies/taker.py`. The logic is identical but receives `FeeModel` instead of calling hardcoded `src.fees` functions, and uses the new `Orderbook.best_bid()` / `Orderbook.best_ask()` methods instead of `best_yes_bid()` / `best_yes_ask()`.

```python
# src/strategies/taker.py
import logging
from datetime import datetime, timezone

from src.core.models import Orderbook, TradeSignal
from src.core.fees import arb_profit, buy_side_arb_profit, exposure_ratio
from src.core.risk import RiskProfile
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate_sell_side(
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None,
    fee_model: FeeModel,
    risk_profile: RiskProfile,
    recorder=None,
    max_contracts_per_arb: int = 1,
) -> TradeSignal | None:
    if market_metadata is None:
        market_metadata = {}

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        bid = book.best_bid()
        if bid is None:
            return None
        legs.append((ticker, bid))

    for ticker, price in legs:
        meta = market_metadata.get(ticker, {})
        depth = orderbooks[ticker].bid_depth_at(price)
        if depth < risk_profile.min_bid_depth:
            return None
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.min_volume_24h:
            return None

    bid_prices = [p for _, p in legs]
    profit = arb_profit(bid_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices, fee_model)
    if exp_ratio > risk_profile.max_exposure_ratio:
        return None

    days = _days_to_expiry(market_metadata)
    if days is not None and days > risk_profile.near_term_hours / 24.0:
        annualized = (profit_pct / days) * 365
        if annualized < risk_profile.hurdle_rate_annual_pct:
            return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="taker",
        quantity=max_contracts_per_arb,
    )


def evaluate_buy_side(
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None,
    fee_model: FeeModel,
    risk_profile: RiskProfile,
    expected_market_count: int | None = None,
    recorder=None,
    max_contracts_per_arb: int = 1,
) -> TradeSignal | None:
    if market_metadata is None:
        market_metadata = {}

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        ask = book.best_ask()
        if ask is None:
            return None
        meta = market_metadata.get(ticker, {})
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.min_volume_24h:
            return None
        legs.append((ticker, ask))

    if expected_market_count is not None and len(legs) < expected_market_count:
        return None

    ask_prices = [p for _, p in legs]
    ask_sum = sum(ask_prices)
    max_ask = max(ask_prices)

    if ask_sum < 0.60:
        return None
    if max_ask < 0.20:
        return None
    if risk_profile.min_buy_side_coverage > 0 and ask_sum < risk_profile.min_buy_side_coverage:
        return None

    profit = buy_side_arb_profit(ask_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.min_profit_pct:
        return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="buy_side_taker",
        quantity=max_contracts_per_arb,
        leg_actions=["buy"] * len(legs),
    )


def _days_to_expiry(market_metadata: dict[str, dict]) -> float | None:
    now = datetime.now(timezone.utc)
    earliest = None
    for meta in market_metadata.values():
        close_str = meta.get("close_time", "")
        if not close_str:
            continue
        try:
            close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            days = (close - now).total_seconds() / 86400
            if earliest is None or days < earliest:
                earliest = days
        except (ValueError, TypeError):
            continue
    return earliest
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_engine.py::test_taker_evaluate_sell_side tests/test_engine.py::test_taker_evaluate_sell_side_no_arb -v`
Expected: PASS

Note: this requires `src/core/risk.py` to exist. If it doesn't yet, first copy `src/risk.py` to `src/core/risk.py` unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/strategies/ src/core/risk.py tests/test_engine.py
git commit -m "feat: extract taker strategies (sell-side, buy-side) to strategies/taker.py"
```

---

## Task 7: Strategy Extraction — Near-Expiry and Monotone

**Files:**
- Create: `src/strategies/near_expiry.py`
- Create: `src/strategies/monotone.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_engine.py`:

```python
def test_near_expiry_evaluate():
    from src.core.models import Orderbook
    from src.core.risk import load_risk_profile
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.near_expiry import evaluate
    from datetime import datetime, timezone, timedelta

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {"near_expiry_window_minutes": 120})
    close = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    books = {
        "T-1": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
        "T-2": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
    }
    meta = {
        "T-1": {"close_time": close, "volume_24h": 0},
        "T-2": {"close_time": close, "volume_24h": 0},
    }
    signal = evaluate("E-1", books, meta, fm, rp)
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"


def test_monotone_evaluate():
    from src.core.models import Orderbook
    from src.exchanges.kalshi.fee_model import KalshiFeeModel
    from src.strategies.monotone import evaluate

    fm = KalshiFeeModel()
    upper = Orderbook(bids={70: 10.0}, asks={72: 5.0})
    lower = Orderbook(bids={50: 10.0}, asks={40: 15.0})
    signal = evaluate("T-UPPER", upper, "T-LOWER", lower, fm)
    assert signal is not None
    assert signal.signal_type == "monotone"
    assert len(signal.legs) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py::test_near_expiry_evaluate tests/test_engine.py::test_monotone_evaluate -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `src/strategies/near_expiry.py`**

Extract `evaluate_near_expiry()` from `src/engine.py`. Uses relaxed thresholds from `risk_profile.near_expiry_*` fields.

```python
# src/strategies/near_expiry.py
import logging

from src.core.models import Orderbook, TradeSignal
from src.core.fees import arb_profit, exposure_ratio
from src.core.risk import RiskProfile
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate(
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None,
    fee_model: FeeModel,
    risk_profile: RiskProfile,
    recorder=None,
    max_contracts_per_arb: int = 1,
) -> TradeSignal | None:
    if market_metadata is None:
        market_metadata = {}

    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        bid = book.best_bid()
        if bid is None:
            return None
        legs.append((ticker, bid))

    for ticker, price in legs:
        meta = market_metadata.get(ticker, {})
        depth = orderbooks[ticker].bid_depth_at(price)
        if depth < risk_profile.near_expiry_min_bid_depth:
            return None
        vol = meta.get("volume_24h", 0)
        if vol < risk_profile.near_expiry_min_volume_24h:
            return None

    bid_prices = [p for _, p in legs]
    profit = arb_profit(bid_prices, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0
    if profit_pct < risk_profile.near_expiry_min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices, fee_model)
    if exp_ratio > risk_profile.max_exposure_ratio:
        return None

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="near_expiry_taker",
        quantity=max_contracts_per_arb,
    )
```

- [ ] **Step 4: Implement `src/strategies/monotone.py`**

Extract `evaluate_monotone_pair()` from `src/engine.py`.

```python
# src/strategies/monotone.py
import logging

from src.core.models import Orderbook, TradeSignal
from src.core.fees import monotone_pair_profit
from src.ports.fee_model import FeeModel

logger = logging.getLogger(__name__)


def evaluate(
    upper_ticker: str,
    upper_book: Orderbook,
    lower_ticker: str,
    lower_book: Orderbook,
    fee_model: FeeModel,
) -> TradeSignal | None:
    upper_bid = upper_book.best_bid()
    lower_ask = lower_book.best_ask()
    if upper_bid is None or lower_ask is None:
        return None

    profit = monotone_pair_profit(upper_bid, lower_ask, fee_model)
    if profit <= 0:
        return None

    profit_pct = profit * 100.0

    return TradeSignal(
        event_ticker=f"{upper_ticker}|{lower_ticker}",
        legs=[(upper_ticker, upper_bid), (lower_ticker, lower_ask)],
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="monotone",
        leg_actions=["sell", "buy"],
    )
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_engine.py::test_near_expiry_evaluate tests/test_engine.py::test_monotone_evaluate -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/strategies/near_expiry.py src/strategies/monotone.py tests/test_engine.py
git commit -m "feat: extract near-expiry and monotone strategies"
```

---

## Task 8: Core Engine as Thin Coordinator

**Files:**
- Create: `src/core/engine.py`

- [ ] **Step 1: Write test for coordinator**

Add to `tests/test_engine.py`:

```python
def test_core_engine_delegates_to_taker():
    from src.core.models import Orderbook
    from src.core.risk import load_risk_profile
    from src.core.engine import ArbEngine
    from src.exchanges.kalshi.fee_model import KalshiFeeModel

    fm = KalshiFeeModel()
    rp = load_risk_profile("aggressive", {})
    engine = ArbEngine(fee_model=fm, risk_profile=rp)
    books = {
        "T-1": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
        "T-2": Orderbook(bids={55: 10.0}, asks={57: 5.0}),
    }
    meta = {
        "T-1": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
        "T-2": {"close_time": "2099-01-01T00:00:00Z", "volume_24h": 100},
    }
    signal = engine.evaluate(event_ticker="E-1", orderbooks=books, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "taker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::test_core_engine_delegates_to_taker -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/core/engine.py`**

```python
# src/core/engine.py
import logging

from src.core.models import Orderbook, TradeSignal
from src.core.risk import RiskProfile
from src.ports.fee_model import FeeModel
from src.ports.constraints import PositionConstraints
from src.strategies import taker, near_expiry, monotone

logger = logging.getLogger(__name__)


class ArbEngine:
    def __init__(
        self,
        fee_model: FeeModel,
        risk_profile: RiskProfile,
        constraints: PositionConstraints | None = None,
        maker_max_horizon_hours: float = 4.0,
        max_contracts_per_arb: int = 1,
        recorder=None,
    ):
        self.fee_model = fee_model
        self.risk_profile = risk_profile
        self.constraints = constraints
        self.maker_max_horizon_hours = maker_max_horizon_hours
        self.max_contracts_per_arb = max_contracts_per_arb
        self.recorder = recorder

    def evaluate(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        return taker.evaluate_sell_side(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_buy_side(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
        expected_market_count: int | None = None,
    ) -> TradeSignal | None:
        return taker.evaluate_buy_side(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            expected_market_count=expected_market_count,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_near_expiry(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        return near_expiry.evaluate(
            event_ticker, orderbooks, market_metadata,
            self.fee_model, self.risk_profile,
            recorder=self.recorder,
            max_contracts_per_arb=self.max_contracts_per_arb,
        )

    def evaluate_monotone_pair(
        self,
        upper_ticker: str,
        upper_book: Orderbook,
        lower_ticker: str,
        lower_book: Orderbook,
    ) -> TradeSignal | None:
        return monotone.evaluate(
            upper_ticker, upper_book,
            lower_ticker, lower_book,
            self.fee_model,
        )

    def evaluate_maker(
        self,
        event_ticker: str,
        orderbooks: dict[str, Orderbook],
        market_metadata: dict[str, dict] | None = None,
    ) -> TradeSignal | None:
        # Maker evaluation stays here temporarily — it uses maker_arb_profit
        # and has horizon logic tightly coupled. Will move to strategies/maker.py
        # when MakerManager is migrated in Task 10.
        from src.core.fees import maker_arb_profit, maker_exposure_ratio
        if market_metadata is None:
            market_metadata = {}

        legs: list[tuple[str, float]] = []
        for ticker, book in orderbooks.items():
            bid = book.best_bid()
            if bid is None:
                return None
            legs.append((ticker, bid))

        for ticker, price in legs:
            meta = market_metadata.get(ticker, {})
            depth = orderbooks[ticker].bid_depth_at(price)
            if depth < self.risk_profile.min_bid_depth:
                return None
            vol = meta.get("volume_24h", 0)
            if vol < self.risk_profile.maker_min_volume_24h:
                return None

        bid_prices = [p for _, p in legs]
        profit = maker_arb_profit(bid_prices, self.fee_model)
        if profit <= 0:
            return None

        profit_pct = profit * 100.0
        exp_ratio = maker_exposure_ratio(bid_prices, self.fee_model)

        return TradeSignal(
            event_ticker=event_ticker,
            legs=legs,
            net_profit=profit,
            profit_pct=profit_pct,
            exposure_ratio=exp_ratio,
            signal_type="maker",
        )

    def evaluate_two_sided(
        self,
        ticker: str,
        book: Orderbook,
        volume_24h: float = 0.0,
    ) -> TradeSignal | None:
        # Two-sided evaluation stays here temporarily — will move to
        # strategies/two_sided.py when TwoSidedManager is migrated in Task 10.
        if self.risk_profile.two_sided_max_inventory <= 0:
            return None
        if volume_24h < self.risk_profile.two_sided_min_volume_24h:
            return None

        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return None

        spread_cents = round((best_ask - best_bid) * 100)
        min_spread = self.risk_profile.two_sided_min_spread_cents + 2
        if spread_cents < min_spread:
            return None

        our_bid = best_bid + 0.01
        our_ask = best_ask - 0.01

        return TradeSignal(
            event_ticker=ticker,
            legs=[(ticker, our_bid), (ticker, our_ask)],
            net_profit=our_ask - our_bid,
            profit_pct=(our_ask - our_bid) * 100.0,
            exposure_ratio=0.0,
            signal_type="two_sided",
            leg_actions=["buy", "sell"],
        )
```

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/test_engine.py::test_core_engine_delegates_to_taker -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/engine.py tests/test_engine.py
git commit -m "feat: add core ArbEngine as thin coordinator over strategy modules"
```

---

## Task 9: Kalshi Scanner Adapter (OrderbookFeed)

**Files:**
- Create: `src/exchanges/kalshi/scanner.py`
- Create: `src/exchanges/kalshi/auth.py`

- [ ] **Step 1: Copy `src/auth.py` to `src/exchanges/kalshi/auth.py` unchanged**

```bash
cp src/auth.py src/exchanges/kalshi/auth.py
```

- [ ] **Step 2: Create Kalshi scanner adapter**

The Kalshi scanner wraps the existing MarketScanner logic but converts Kalshi's `yes_dollars_fp`/`no_dollars_fp` snapshots into the core `{bids: {}, asks: {}}` format before calling `OrderbookManager.apply_snapshot()`.

```python
# src/exchanges/kalshi/scanner.py
import asyncio
import inspect
import json
import logging
import time
from typing import Callable

import websockets

from src.exchanges.kalshi.auth import KalshiAuth
from src.core.orderbook_manager import OrderbookManager

logger = logging.getLogger(__name__)


class KalshiScanner:
    def __init__(
        self,
        ws_url: str,
        auth: KalshiAuth,
        orderbook_mgr: OrderbookManager,
        on_orderbook_update: Callable[[str], None] | None = None,
        on_fill: Callable[[dict], None] | None = None,
    ):
        self.ws_url = ws_url
        self.auth = auth
        self.orderbook_mgr = orderbook_mgr
        self._on_orderbook_update = on_orderbook_update
        self._on_fill = on_fill
        self._ws = None
        self._sub_id = 0
        self._running = False
        self._stopping = False
        self._subscribed_tickers: set[str] = set()
        self._fills_subscribed = False

    async def connect(self):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                headers = self.auth.build_headers("GET", "/trade-api/ws/v2")
                self._ws = await asyncio.wait_for(
                    websockets.connect(self.ws_url, additional_headers=headers, max_size=10 * 1024 * 1024),
                    timeout=30,
                )
                self._running = True
                self._stopping = False
                logger.info("WebSocket connected")
                return
            except Exception:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("WS connect attempt %d/%d failed, retrying in %ds", attempt + 1, max_retries, wait)
                await asyncio.sleep(wait)

    async def subscribe(self, market_tickers: list[str], chunk_size: int = 500):
        if not self._ws:
            return
        new_tickers = [t for t in market_tickers if t not in self._subscribed_tickers]
        if not new_tickers:
            return
        for i in range(0, len(new_tickers), chunk_size):
            chunk = new_tickers[i:i + chunk_size]
            self._sub_id += 1
            msg = {"id": self._sub_id, "cmd": "subscribe", "params": {"channels": ["orderbook_delta"], "market_tickers": chunk}}
            await self._ws.send(json.dumps(msg))
            self._subscribed_tickers.update(chunk)
        logger.info("Subscribed to %d markets (%d new)", len(self._subscribed_tickers), len(new_tickers))

    async def subscribe_fills(self):
        if not self._ws or self._fills_subscribed:
            return
        self._sub_id += 1
        msg = {"id": self._sub_id, "cmd": "subscribe", "params": {"channels": ["fill"]}}
        await self._ws.send(json.dumps(msg))
        self._fills_subscribed = True
        logger.info("Subscribed to fills channel")

    def stop(self):
        self._stopping = True

    async def listen(self):
        while self._running and not self._stopping:
            try:
                if not self._ws:
                    await self._reconnect()
                    continue
                msg_raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
                msg = json.loads(msg_raw)
                msg_type = msg.get("type")

                if msg_type == "orderbook_snapshot":
                    ticker = msg["msg"]["market_ticker"]
                    snapshot = msg["msg"]
                    bids = {round(float(p) * 100): float(q) for p, q in snapshot.get("yes_dollars_fp", [])}
                    no_bids = {round(float(p) * 100): float(q) for p, q in snapshot.get("no_dollars_fp", [])}
                    asks = {round((100 - cents)): qty for cents, qty in no_bids.items()}
                    self.orderbook_mgr.apply_snapshot(ticker, {"bids": bids, "asks": asks})
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "orderbook_delta":
                    ticker = msg["msg"]["market_ticker"]
                    delta_msg = msg["msg"]
                    price_dollars = float(delta_msg["price_dollars"])
                    delta_qty = float(delta_msg["delta_fp"])
                    side = delta_msg["side"]
                    if side == "yes":
                        core_side = "bid"
                        price_cents = round(price_dollars * 100)
                    else:
                        core_side = "ask"
                        price_cents = round((1.0 - price_dollars) * 100)
                    self.orderbook_mgr.apply_delta(ticker, {
                        "price_cents": price_cents,
                        "delta_qty": delta_qty,
                        "side": core_side,
                    })
                    await self._fire_orderbook_update(ticker)

                elif msg_type == "fill":
                    if self._on_fill:
                        fill_msg = msg["msg"]
                        normalized = {
                            "order_id": fill_msg.get("order_id", ""),
                            "ticker": fill_msg.get("market_ticker", ""),
                            "price": float(fill_msg.get("yes_price_dollars", 0)),
                            "quantity": int(fill_msg.get("count_fp", fill_msg.get("count", 0))),
                            "side": fill_msg.get("action", ""),
                            "exchange": "kalshi",
                        }
                        self._on_fill(normalized)

            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                self._ws = None
                await self._reconnect()
            except Exception:
                logger.exception("Error in WS listen loop")
                await asyncio.sleep(1)

    async def _reconnect(self):
        try:
            await asyncio.wait_for(self.connect(), timeout=30)
            if self._subscribed_tickers:
                await self.subscribe(list(self._subscribed_tickers))
            if self._fills_subscribed:
                self._fills_subscribed = False
                await self.subscribe_fills()
        except Exception:
            logger.exception("Reconnect failed, retrying in 5s")
            await asyncio.sleep(5)

    async def _fire_orderbook_update(self, ticker: str):
        if self._on_orderbook_update:
            result = self._on_orderbook_update(ticker)
            if inspect.isawaitable(result):
                await result

    async def close(self):
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
```

- [ ] **Step 3: Commit**

```bash
git add src/exchanges/kalshi/auth.py src/exchanges/kalshi/scanner.py
git commit -m "feat: add Kalshi scanner adapter with orderbook normalization"
```

---

## Task 10: Kalshi Discovery Adapter and API Move

**Files:**
- Create: `src/exchanges/kalshi/api.py`
- Create: `src/exchanges/kalshi/discovery.py`

- [ ] **Step 1: Copy `src/api.py` to `src/exchanges/kalshi/api.py`**

Update the import of `KalshiAuth` to use the new location:

```bash
cp src/api.py src/exchanges/kalshi/api.py
```

Then change the import in `src/exchanges/kalshi/api.py`:
```python
# Change: from src.auth import KalshiAuth
# To:     from src.exchanges.kalshi.auth import KalshiAuth
```

And change the import of models:
```python
# Change: from src.models import Event, Market
# To:     from src.core.models import Event, Market
```

- [ ] **Step 2: Create Kalshi discovery adapter**

Extract from `src/discovery.py`. Updates: import `Event` from `src.core.models`, import `OrderbookManager` from `src.core.orderbook_manager`, import `KalshiAPI` from `src.exchanges.kalshi.api`.

```python
# src/exchanges/kalshi/discovery.py
import asyncio
import logging
import re
from datetime import datetime, timezone

from src.core.models import Event
from src.core.orderbook_manager import OrderbookManager

logger = logging.getLogger(__name__)

_THRESHOLD_PATTERN = re.compile(
    r"(above|below|at least|at most|under|over|more than|less than|≥|≤|>|<)\s*"
    r"(\$?[\d,]+\.?\d*%?)",
    re.IGNORECASE,
)


class MonotoneFamilyRegistry:
    def __init__(self):
        self._families: dict[str, list[dict]] = {}

    def try_register(self, event_ticker: str, market_ticker: str, title: str) -> str | None:
        m = _THRESHOLD_PATTERN.search(title)
        if not m:
            return None
        direction = m.group(1).lower()
        threshold_str = m.group(2).replace("$", "").replace(",", "").replace("%", "")
        try:
            threshold = float(threshold_str)
        except ValueError:
            return None
        template_key = _THRESHOLD_PATTERN.sub("__THRESH__", title).strip()
        family_key = f"{event_ticker}:{template_key}"
        entry = {"event_ticker": event_ticker, "market_ticker": market_ticker, "threshold": threshold, "direction": direction}
        self._families.setdefault(family_key, []).append(entry)
        return family_key

    def get_families(self) -> dict[str, list[dict]]:
        return {k: v for k, v in self._families.items() if len(v) >= 2}

    def unregister_event(self, event_ticker: str):
        to_remove = []
        for key, members in self._families.items():
            self._families[key] = [m for m in members if m["event_ticker"] != event_ticker]
            if not self._families[key]:
                to_remove.append(key)
        for key in to_remove:
            del self._families[key]


class KalshiDiscovery:
    def __init__(self, api, orderbook_mgr: OrderbookManager, scanner):
        self.api = api
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}
        self.event_total_markets: dict[str, int] = {}
        self.monotone_registry = MonotoneFamilyRegistry()

    def register_events(self, events: list[Event]) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker in self.event_tickers:
                continue
            self.event_tickers.add(event.event_ticker)
            market_tickers = event.market_tickers()
            self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
            self.event_total_markets[event.event_ticker] = event.total_market_count
            new_tickers.extend(market_tickers)
            for m in event.markets:
                self.market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.expected_expiration_time,
                    "volume_24h": m.volume_24h,
                    "open_interest": m.open_interest,
                    "liquidity": m.liquidity,
                }
                self.monotone_registry.try_register(event.event_ticker, m.ticker, m.title)
        return new_tickers

    def _collect_expired_events(self, now: datetime) -> set[str]:
        expired = set()
        for event_ticker in list(self.event_tickers):
            market_tickers = self.orderbook_mgr.get_event_markets(event_ticker)
            if not market_tickers:
                expired.add(event_ticker)
                continue
            all_expired = True
            for mt in market_tickers:
                meta = self.market_metadata.get(mt, {})
                close_str = meta.get("expected_expiration_time") or meta.get("close_time", "")
                if not close_str:
                    all_expired = False
                    continue
                try:
                    close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if close > now:
                        all_expired = False
                except (ValueError, TypeError):
                    all_expired = False
            if all_expired:
                expired.add(event_ticker)
        return expired

    def cleanup_expired(self) -> set[str]:
        now = datetime.now(timezone.utc)
        expired = self._collect_expired_events(now)
        for event_ticker in expired:
            self.event_tickers.discard(event_ticker)
            self.orderbook_mgr.unregister_event(event_ticker)
            self.monotone_registry.unregister_event(event_ticker)
            for mt in list(self.market_metadata):
                if self.orderbook_mgr.get_event_for_market(mt) is None:
                    self.market_metadata.pop(mt, None)
            self.event_total_markets.pop(event_ticker, None)
        if expired:
            logger.info("Cleaned up %d expired events: %s", len(expired), expired)
        return expired

    async def full_scan(self):
        all_events = []
        cursor = ""
        for page in range(200):
            events, cursor = await self.api.fetch_events_page(cursor)
            all_events.extend(events)
            if not cursor:
                break
            logger.debug("Discovery page %d: %d events (cursor=%s)", page + 1, len(events), cursor[:20])

        all_events.sort(key=lambda e: min((m.close_time for m in e.markets if m.close_time), default="9999"))
        new_tickers = self.register_events(all_events)
        if new_tickers:
            await self.scanner.subscribe(new_tickers)
        logger.info("Full scan complete: %d events, %d new market tickers", len(all_events), len(new_tickers))

    async def poll_loop(self, interval_secs: int):
        await self.full_scan()
        while True:
            await asyncio.sleep(interval_secs)
            try:
                events, _ = await self.api.fetch_events_page()
                new_tickers = self.register_events(events)
                if new_tickers:
                    await self.scanner.subscribe(new_tickers)
            except Exception:
                logger.exception("Error in discovery poll")

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(300)
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("Error in cleanup loop")
```

- [ ] **Step 3: Commit**

```bash
git add src/exchanges/kalshi/api.py src/exchanges/kalshi/discovery.py
git commit -m "feat: add Kalshi API and discovery adapters"
```

---

## Task 11: KalshiExchange Facade and Factory

**Files:**
- Create: `src/exchanges/kalshi/__init__.py` (update)
- Modify: `src/exchanges/__init__.py` (update)

- [ ] **Step 1: Write test for exchange factory**

Add to `tests/test_ports.py`:

```python
def test_exchange_factory():
    from src.exchanges import create_exchange
    # Should raise for unknown exchange
    import pytest
    with pytest.raises(KeyError):
        create_exchange("nonexistent", {})
```

- [ ] **Step 2: Implement KalshiExchange facade**

```python
# src/exchanges/kalshi/__init__.py
from src.exchanges.kalshi.auth import KalshiAuth
from src.exchanges.kalshi.api import KalshiAPI
from src.exchanges.kalshi.fee_model import KalshiFeeModel
from src.exchanges.kalshi.order_builder import KalshiOrderBuilder
from src.exchanges.kalshi.constraints import KalshiConstraints
from src.exchanges.kalshi.scanner import KalshiScanner
from src.exchanges.kalshi.discovery import KalshiDiscovery
from src.core.orderbook_manager import OrderbookManager


class KalshiExchange:
    name = "kalshi"

    def __init__(self, config: dict):
        self.auth = KalshiAuth(config["api_key_id"], config["private_key_path"])
        self.api = KalshiAPI(config["base_url"], self.auth)
        self.ws_url = config["ws_url"]
        self.fee_model = KalshiFeeModel()
        self.order_builder = KalshiOrderBuilder()
        self.constraints = KalshiConstraints()

    def create_feed(self, orderbook_mgr: OrderbookManager, on_update=None, on_fill=None) -> KalshiScanner:
        return KalshiScanner(self.ws_url, self.auth, orderbook_mgr, on_update, on_fill)

    def create_discovery(self, orderbook_mgr: OrderbookManager, scanner) -> KalshiDiscovery:
        return KalshiDiscovery(self.api, orderbook_mgr, scanner)
```

- [ ] **Step 3: Implement exchange factory**

```python
# src/exchanges/__init__.py
from src.exchanges.kalshi import KalshiExchange

EXCHANGES = {
    "kalshi": KalshiExchange,
}


def create_exchange(name: str, config: dict):
    return EXCHANGES[name](config)
```

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/test_ports.py::test_exchange_factory -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/exchanges/ tests/test_ports.py
git commit -m "feat: add KalshiExchange facade and exchange factory"
```

---

## Task 12: Move Remaining Core Modules

**Files:**
- Create: `src/core/dispatch.py` (copy from `src/dispatch.py`, update imports)
- Create: `src/core/risk.py` (if not already done in Task 6)
- Create: `src/core/recorder.py` (copy from `src/recorder.py`)
- Create: `src/core/replay.py` (copy from `src/replay.py`, update imports)
- Create: `src/core/analytics.py` (copy from `src/analytics.py`, update imports)
- Create: `src/core/positions.py` (copy from `src/positions.py`)

- [ ] **Step 1: Copy and update core modules**

For each module, copy to `src/core/` and update imports:
- `from src.models import ...` → `from src.core.models import ...`
- `from src.fees import ...` → `from src.core.fees import ...` (note: fee functions now require `fee_model` parameter)
- `from src.risk import ...` → `from src.core.risk import ...`
- `from src.engine import ...` → `from src.core.engine import ...`
- `from src.scanner import OrderbookManager` → `from src.core.orderbook_manager import OrderbookManager`
- `from src.executor import ...` → `from src.executor import ...` (executor stays at top level)

**Key change in `src/core/dispatch.py`:** The Dispatcher imports `ArbEngine` from `src.core.engine` and `OrderbookManager` from `src.core.orderbook_manager`.

**Key change in `src/core/recorder.py`:** No exchange-specific imports — already exchange-agnostic.

**`src/core/positions.py`:** No changes needed — already exchange-agnostic.

- [ ] **Step 2: Run full test suite to verify no regressions from core copies**

Run: `python3 -m pytest tests/ -v --tb=short`

Note: existing tests will still import from the old locations (`src.models`, `src.fees`, etc.). They should continue to pass since we haven't deleted the old files yet. The new core modules are additive at this point.

- [ ] **Step 3: Commit**

```bash
git add src/core/
git commit -m "feat: copy remaining modules to src/core/ with updated imports"
```

---

## Task 13: Update Executor to Use Ports

**Files:**
- Modify: `src/executor.py`

- [ ] **Step 1: Update executor imports and constructor**

Change `src/executor.py`:
```python
# Old:
from src.api import KalshiAPI

# New:
from src.ports.exchange import ExchangeAPI
from src.ports.order_builder import OrderBuilder
```

Update `__init__` signature:
```python
# Old:
def __init__(self, api: KalshiAPI, positions: PositionTracker, ...)

# New:
def __init__(self, api: ExchangeAPI, order_builder: OrderBuilder, positions: PositionTracker, ...)
```

Update `build_orders` to use the injected `order_builder` instead of `self.api.build_sell_order()`:
```python
# Old:
order = self.api.build_sell_order(ticker, price, quantity)

# New:
order = self.order_builder.build_sell_order(ticker, price, quantity)
```

Similarly update all `build_buy_order`, `build_close_order`, `unwrap_order` calls to use `self.order_builder`.

Also update model imports:
```python
# Old:
from src.models import TradeSignal
from src.positions import PositionTracker
from src.risk import RiskProfile

# New:
from src.core.models import TradeSignal
from src.core.positions import PositionTracker
from src.core.risk import RiskProfile
```

- [ ] **Step 2: Update executor tests**

In `tests/test_executor.py`, update mock creation to provide an `order_builder` parameter:

```python
# Add to mock setup:
mock_order_builder = MagicMock()
mock_order_builder.build_sell_order.side_effect = lambda t, p, q: {"ticker": t, "action": "sell", "yes_price": round(p * 100), "count": q}
mock_order_builder.build_buy_order.side_effect = lambda t, p, q: {"ticker": t, "action": "buy", "yes_price": round(p * 100), "count": q}
mock_order_builder.build_close_order.side_effect = lambda t, q: {"ticker": t, "action": "sell" if q > 0 else "buy", "count": abs(q)}
mock_order_builder.unwrap_order.side_effect = lambda raw: raw.get("order", raw)

# Update ExecutionManager construction:
executor = ExecutionManager(api=mock_api, order_builder=mock_order_builder, positions=tracker, ...)
```

- [ ] **Step 3: Run executor tests**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "refactor: executor uses ExchangeAPI and OrderBuilder ports"
```

---

## Task 14: Update main.py Wiring

**Files:**
- Modify: `src/main.py`
- Modify: `src/config.py`

- [ ] **Step 1: Update config to support exchange field**

Add to `Config` dataclass:
```python
exchange: str = "kalshi"
```

Update `load_config` to read `exchange:` key:
```python
exchange = raw.get("exchange", "kalshi")
```

Update credential loading to support nested format:
```python
# Support both flat (backward compat) and nested credentials
creds_section = raw["credentials"]
if exchange in creds_section and isinstance(creds_section[exchange], dict):
    # Nested: credentials.kalshi.demo
    if mode not in creds_section[exchange]:
        raise ValueError(f"No credentials for exchange {exchange!r} mode {mode!r}")
    creds = creds_section[exchange][mode]
elif mode in creds_section:
    # Flat (backward compat): credentials.demo
    creds = creds_section[mode]
else:
    raise ValueError(f"No credentials for mode {mode!r}")
```

Pass exchange into Config constructor:
```python
return Config(exchange=exchange, mode=mode, ...)
```

- [ ] **Step 2: Rewire `src/main.py` to use exchange factory**

Replace direct Kalshi imports with exchange factory:

```python
# Old:
from src.auth import KalshiAuth
from src.api import KalshiAPI
from src.scanner import MarketScanner, OrderbookManager
from src.discovery import EventDiscovery

# New:
from src.exchanges import create_exchange
from src.core.orderbook_manager import OrderbookManager
from src.core.engine import ArbEngine
from src.core.dispatch import Dispatcher
from src.core.positions import PositionTracker
from src.core.risk import load_risk_profile
from src.core.recorder import DataRecorder
```

Update `ArbBot.__init__`:

```python
# Old:
auth = KalshiAuth(cfg.api_key_id, str(cfg.private_key_path))
self.api = KalshiAPI(cfg.rest_base_url, auth)
self.scanner = MarketScanner(cfg.ws_url, auth, self.orderbook_mgr, ...)
self.discovery = EventDiscovery(self.api, self.orderbook_mgr, self.scanner)

# New:
exchange_config = {
    "api_key_id": cfg.api_key_id,
    "private_key_path": str(cfg.private_key_path),
    "base_url": cfg.rest_base_url,
    "ws_url": cfg.ws_url,
}
self.exchange = create_exchange(cfg.exchange, exchange_config)
self.api = self.exchange.api
self.engine = ArbEngine(
    fee_model=self.exchange.fee_model,
    risk_profile=risk_profile,
    maker_max_horizon_hours=cfg.maker_max_horizon_hours,
    max_contracts_per_arb=cfg.max_contracts_per_arb,
    recorder=self.recorder,
)
self.executor = ExecutionManager(
    api=self.exchange.api,
    order_builder=self.exchange.order_builder,
    positions=self.positions,
    ...
)
self.scanner = self.exchange.create_feed(self.orderbook_mgr, self._on_orderbook_update, self._on_fill)
self.discovery = self.exchange.create_discovery(self.orderbook_mgr, self.scanner)
```

- [ ] **Step 3: Run main tests**

Run: `python3 -m pytest tests/test_main.py tests/test_config.py -v`
Expected: PASS (may need test fixture updates)

- [ ] **Step 4: Commit**

```bash
git add src/main.py src/config.py tests/test_main.py tests/test_config.py
git commit -m "refactor: main.py uses exchange factory for wiring"
```

---

## Task 15: Move Strategy Managers (Maker, Two-Sided)

**Files:**
- Create: `src/strategies/maker.py` (from `src/maker.py`, update imports)
- Create: `src/strategies/two_sided.py` (from `src/two_sided.py`, update imports)

- [ ] **Step 1: Copy maker and two-sided to strategies/**

Copy `src/maker.py` to `src/strategies/maker.py` and update imports:
```python
# Old:
from src.api import KalshiAPI
from src.models import TradeSignal

# New:
from src.ports.exchange import ExchangeAPI
from src.ports.order_builder import OrderBuilder
from src.core.models import TradeSignal
```

Update `MakerManager.__init__` to accept ports:
```python
# Old:
def __init__(self, api: KalshiAPI, ...)

# New:
def __init__(self, api: ExchangeAPI, order_builder: OrderBuilder, ...)
```

Update all `self.api.build_sell_order(...)` → `self.order_builder.build_sell_order(...)` calls.
Update all `self.api.build_buy_order(...)` → `self.order_builder.build_buy_order(...)` calls.

Same pattern for `src/strategies/two_sided.py`.

- [ ] **Step 2: Update maker/two-sided tests**

Update `tests/test_maker.py` and `tests/test_two_sided.py` to pass `order_builder` mock and import from new locations.

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_maker.py tests/test_two_sided.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/strategies/maker.py src/strategies/two_sided.py tests/test_maker.py tests/test_two_sided.py
git commit -m "refactor: move maker and two-sided managers to strategies/ with port injection"
```

---

## Task 16: Delete Old Files and Fix All Imports

**Files:**
- Delete: `src/models.py`, `src/fees.py`, `src/engine.py`, `src/risk.py`, `src/scanner.py`, `src/discovery.py`, `src/dispatch.py`, `src/api.py`, `src/auth.py`, `src/maker.py`, `src/two_sided.py`, `src/recorder.py`, `src/replay.py`, `src/analytics.py`, `src/positions.py`
- Modify: all remaining `src/` and `tests/` files to use new import paths

- [ ] **Step 1: Create compatibility shims (temporary)**

Before deleting, create thin re-export shims at the old locations so nothing breaks during the transition. Each old file becomes a one-line re-export:

```python
# src/models.py (shim)
from src.core.models import *  # noqa: F401,F403
```

```python
# src/fees.py (shim)
from src.core.fees import *  # noqa: F401,F403
# Also re-export old API for tests that haven't been updated:
from src.exchanges.kalshi.fee_model import KalshiFeeModel
TAKER_FEE_RATE = 0.07
def taker_fee(price: float) -> float:
    return TAKER_FEE_RATE * price * (1.0 - price)
```

Actually — per the spec, we're doing a full restructure, not maintaining backward compat shims. Instead:

- [ ] **Step 1: Update ALL test imports in one pass**

Use find-and-replace across all test files:
```
from src.models import → from src.core.models import
from src.fees import → from src.core.fees import  (note: functions now need fee_model param)
from src.engine import → from src.core.engine import
from src.risk import → from src.core.risk import
from src.scanner import OrderbookManager → from src.core.orderbook_manager import OrderbookManager
from src.scanner import MarketScanner → from src.exchanges.kalshi.scanner import KalshiScanner as MarketScanner
from src.discovery import → from src.exchanges.kalshi.discovery import
from src.dispatch import → from src.core.dispatch import
from src.api import → from src.exchanges.kalshi.api import
from src.auth import → from src.exchanges.kalshi.auth import
from src.maker import → from src.strategies.maker import
from src.two_sided import → from src.strategies.two_sided import
from src.recorder import → from src.core.recorder import
from src.positions import → from src.core.positions import
from src.replay import → from src.core.replay import
from src.analytics import → from src.core.analytics import
```

- [ ] **Step 2: Update test assertions for new Orderbook model**

Tests that create `Orderbook(yes_bids=..., no_bids=...)` need updating to `Orderbook(bids=..., asks=...)`. Tests that call `best_yes_bid()` need updating to `best_bid()`.

This is the most mechanical but labor-intensive part. Each test file must be reviewed and updated.

- [ ] **Step 3: Update fee function calls in tests**

Tests that call `arb_profit([0.40, 0.40, 0.40])` must be updated to `arb_profit([0.40, 0.40, 0.40], fee_model)` where `fee_model` is a `KalshiFeeModel()` instance.

- [ ] **Step 4: Delete old source files**

```bash
git rm src/models.py src/fees.py src/engine.py src/risk.py src/scanner.py src/discovery.py src/dispatch.py src/api.py src/auth.py src/maker.py src/two_sided.py src/recorder.py src/replay.py src/analytics.py src/positions.py
```

- [ ] **Step 5: Update `src/mcp_server.py` imports**

```python
# Old:
from src.config import load_config
from src.auth import KalshiAuth
from src.api import KalshiAPI

# New:
from src.config import load_config
from src.exchanges.kalshi.auth import KalshiAuth
from src.exchanges.kalshi.api import KalshiAPI
```

- [ ] **Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -v --tb=short`
Expected: ALL PASS

This is the critical step. If any test fails, fix the import or model usage before proceeding.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete old source files, update all imports to new architecture"
```

---

## Task 17: Import Isolation Test

**Files:**
- Create: `tests/test_import_isolation.py`

- [ ] **Step 1: Write architectural boundary enforcement test**

```python
# tests/test_import_isolation.py
"""Verify that src/core/ never imports from src/exchanges/."""
import ast
import os


def test_core_does_not_import_exchanges():
    core_dir = os.path.join("src", "core")
    violations = []

    for filename in os.listdir(core_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(core_dir, filename)
        with open(filepath) as f:
            tree = ast.parse(f.read(), filepath)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.exchanges"):
                        violations.append(f"{filepath}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src.exchanges"):
                    violations.append(f"{filepath}: from {node.module} import ...")

    assert not violations, f"Core imports exchanges:\n" + "\n".join(violations)


def test_strategies_does_not_import_exchanges():
    strat_dir = os.path.join("src", "strategies")
    violations = []

    for filename in os.listdir(strat_dir):
        if not filename.endswith(".py"):
            continue
        filepath = os.path.join(strat_dir, filename)
        with open(filepath) as f:
            tree = ast.parse(f.read(), filepath)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.exchanges"):
                        violations.append(f"{filepath}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src.exchanges"):
                    violations.append(f"{filepath}: from {node.module} import ...")

    assert not violations, f"Strategies imports exchanges:\n" + "\n".join(violations)
```

- [ ] **Step 2: Run isolation test**

Run: `python3 -m pytest tests/test_import_isolation.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_import_isolation.py
git commit -m "test: add architectural boundary enforcement tests"
```

---

## Task 18: Update config.example.yaml

**Files:**
- Modify: `config.example.yaml`

- [ ] **Step 1: Add exchange field and document new credential structure**

```yaml
exchange: kalshi              # kalshi (predictit, ibkr planned for future)

mode: demo

credentials:
  # Nested format (recommended for multi-exchange):
  # kalshi:
  #   demo:
  #     api_key_id: "your-demo-key-id"
  #     private_key_path: "~/.kalshi/demo_private_key.pem"
  #   live:
  #     api_key_id: "your-live-key-id"
  #     private_key_path: "~/.kalshi/live_private_key.pem"

  # Flat format (backward compatible, Kalshi-only):
  demo:
    api_key_id: "your-demo-key-id"
    private_key_path: "~/.kalshi/demo_private_key.pem"
  live:
    api_key_id: "your-live-key-id"
    private_key_path: "~/.kalshi/live_private_key.pem"

# ... rest unchanged
```

- [ ] **Step 2: Commit**

```bash
git add config.example.yaml
git commit -m "docs: add exchange field to config.example.yaml"
```

---

## Task 19: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update architecture section**

Rewrite the Architecture section to reflect the new directory structure:

- Update module descriptions to use new paths (`src/core/engine.py`, `src/exchanges/kalshi/api.py`, `src/strategies/taker.py`, etc.)
- Update "Key modules" list with new paths
- Update the data flow diagram with new module locations
- Add a note about the ports & adapters pattern and the dependency rule
- Update the Orderbook description (bids/asks instead of yes_bids/no_bids)
- Add the `exchange: kalshi` config field to the Config section
- Update the Fee Math section to mention FeeModel injection

- [ ] **Step 2: Verify CLAUDE.md accuracy**

Read through the updated CLAUDE.md and confirm every module path mentioned actually exists.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for ports & adapters architecture"
```

---

## Task 20: Update Skills and MCP Server

**Files:**
- Modify: `.claude/skills/` files that reference old paths
- Modify: `src/mcp_server.py` (if not already done)
- Modify: `.claude/settings.local.json` (if MCP config references old paths)

- [ ] **Step 1: Identify skills that need path updates**

Check all skill files in `.claude/skills/` and any skill definitions that reference:
- `src/engine.py` → `src/core/engine.py`
- `src/executor.py` → `src/executor.py` (unchanged)
- `src/scanner.py` → `src/exchanges/kalshi/scanner.py` + `src/core/orderbook_manager.py`
- `src/discovery.py` → `src/exchanges/kalshi/discovery.py`
- `src/api.py` → `src/exchanges/kalshi/api.py`
- `src/maker.py` → `src/strategies/maker.py`
- `src/two_sided.py` → `src/strategies/two_sided.py`
- `src/fees.py` → `src/core/fees.py`
- `src/models.py` → `src/core/models.py`
- `src/dispatch.py` → `src/core/dispatch.py`

- [ ] **Step 2: Update skill files**

For each skill that references old paths, update to new paths. Skills to check: `analyze-positions`, `live-test`, `post-run-analyst`, `strategy-review`, `strategy-tuning`, `dry-run`.

- [ ] **Step 3: Verify MCP server works**

The MCP server (`src/mcp_server.py`) creates its own `KalshiAPI` instance. Ensure its imports point to `src.exchanges.kalshi.api` and `src.exchanges.kalshi.auth`.

- [ ] **Step 4: Commit**

```bash
git add .claude/ src/mcp_server.py
git commit -m "chore: update skills and MCP server for new module paths"
```

---

## Task 21: Full Regression Test

**Files:** None (test-only)

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run import isolation tests**

Run: `python3 -m pytest tests/test_import_isolation.py -v`
Expected: PASS

- [ ] **Step 3: Run replay equivalence check (if recorded data exists)**

If `data/arb_history.db` exists with recorded orderbook data:

Run: `python3 -m src.core.replay --sweep`

Compare output to a pre-refactor run. Signal counts and profit calculations should be identical.

- [ ] **Step 4: Verify bot starts successfully in demo mode**

Run: `timeout 10 python3 -m src.main || true`

Should see: logging setup, WebSocket connection attempt, discovery scan start. No import errors.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "test: verify full regression suite passes after exchange abstraction refactor"
```
