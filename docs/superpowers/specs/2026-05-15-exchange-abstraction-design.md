# Exchange Abstraction Layer — Design Spec

**Date:** 2026-05-15
**Status:** Draft
**Goal:** Restructure the codebase to separate exchange-agnostic trading logic from Kalshi-specific API implementations, enabling future support for PredictIt and IBKR ForecastEx prediction contracts.

## Context

The bot currently works exclusively with Kalshi. Kalshi-specific field names, fee constants, API calls, and protocol details are woven through every module. All three target exchanges (Kalshi, PredictIt, IBKR ForecastEx) share the same fundamental model: binary-outcome prediction markets with $1 payoff. The differences are in fee structures, API protocols, position constraints, and market discovery.

**Scope:** Full restructure using ports & adapters architecture. This spec covers the refactor of the existing Kalshi bot — it does not implement PredictIt or IBKR connectors. Those are future work that this architecture enables.

**Phasing for multi-exchange:**
- Phase 1 (this spec): Restructure codebase, extract ports, create Kalshi adapters
- Phase 2 (future): Add PredictIt adapter — independent per-exchange strategies
- Phase 3 (future): Add IBKR ForecastEx adapter
- Phase 4 (future): Cross-exchange arbitrage layer (correlate equivalent markets across exchanges)

## Architecture: Ports & Adapters

### Directory Structure

```
src/
  core/                    # Exchange-agnostic logic
    engine.py              # ArbEngine — thin coordinator, delegates to strategies
    dispatch.py            # Dispatcher — routes orderbook updates, guards duplicates
    risk.py                # RiskProfile dataclass + loader
    models.py              # Orderbook, TradeSignal, Event, Market, Fill
    fees.py                # Generic profit functions that accept FeeModel
    recorder.py            # DataRecorder (SQLite, exchange-agnostic)
    replay.py              # ReplayEngine
    analytics.py           # Analytics
    positions.py           # PositionTracker
    orderbook_manager.py   # OrderbookManager (stores Orderbook state, exchange-agnostic)

  ports/                   # Abstract interfaces (Python Protocol classes)
    exchange.py            # ExchangeAPI protocol
    feed.py                # OrderbookFeed protocol
    discovery.py           # MarketDiscovery protocol
    order_builder.py       # OrderBuilder protocol
    fee_model.py           # FeeModel protocol
    constraints.py         # PositionConstraints protocol

  exchanges/
    __init__.py            # create_exchange() factory + EXCHANGES registry
    kalshi/
      __init__.py          # KalshiExchange facade
      api.py               # KalshiAPI (implements ExchangeAPI)
      auth.py              # KalshiAuth (RSA-PSS signing)
      scanner.py           # KalshiScanner (implements OrderbookFeed)
      discovery.py         # KalshiDiscovery (implements MarketDiscovery)
      fee_model.py         # KalshiFeeModel (7% taker, 0% maker, 0% profit)
      order_builder.py     # KalshiOrderBuilder (implements OrderBuilder)
      constraints.py       # KalshiConstraints (no hard caps)

  strategies/              # Strategy implementations
    taker.py               # evaluate_sell_side(), evaluate_buy_side()
    maker.py               # MakerManager class
    two_sided.py           # TwoSidedManager class
    near_expiry.py         # evaluate()
    monotone.py            # evaluate()

  executor.py              # ExecutionManager (uses ExchangeAPI + OrderBuilder ports)
  main.py                  # ArbBot (config loading, wiring, lifecycle)
  mcp_server.py            # MCP server (unchanged)
```

### Dependency Rule

`src/core/` and `src/strategies/` import only from `src/ports/` and each other. They never import from `src/exchanges/`. `src/exchanges/kalshi/` imports from `src/ports/` (to know the contracts) and `src/core/models.py` (to construct domain objects). `src/main.py` is the composition root that wires exchanges to core.

## Port Interfaces

### ExchangeAPI

```python
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

Thin I/O contract. Order construction is a separate port.

### OrderBuilder

```python
class OrderBuilder(Protocol):
    def build_sell_order(self, ticker: str, price: float, quantity: int) -> dict: ...
    def build_buy_order(self, ticker: str, price: float, quantity: int) -> dict: ...
    def build_close_order(self, ticker: str, quantity: int) -> dict: ...
    def unwrap_order(self, raw: dict) -> dict: ...
```

Separated from ExchangeAPI because order shape is exchange-specific but requires no I/O.

### FeeModel

```python
class FeeModel(Protocol):
    def taker_fee(self, price: float) -> float: ...
    def maker_fee(self, price: float) -> float: ...
    def profit_fee(self, gross_profit: float) -> float: ...
```

Kalshi: `taker_fee = 0.07 * p * (1-p)`, `maker_fee = 0`, `profit_fee = 0`.
PredictIt (future): `taker_fee = 0`, `maker_fee = 0`, `profit_fee = 0.10 * gross`.

### OrderbookFeed

```python
class OrderbookFeed(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self, market_tickers: list[str]) -> None: ...
    async def subscribe_fills(self) -> None: ...
    async def listen(self) -> None: ...
    async def close(self) -> None: ...
    def stop(self) -> None: ...
```

Fill and orderbook callbacks are passed at construction time, not part of the protocol.

### MarketDiscovery

```python
class MarketDiscovery(Protocol):
    async def full_scan(self) -> None: ...
    async def poll_loop(self, interval_secs: int) -> None: ...
    async def cleanup_loop(self) -> None: ...
    def register_events(self, events: list[Event]) -> list[str]: ...
    def cleanup_expired(self) -> set[str]: ...
```

### PositionConstraints

```python
class PositionConstraints(Protocol):
    def max_position_size(self, ticker: str) -> int | None: ...
    def max_total_exposure(self) -> float | None: ...
```

PredictIt: returns 850 for `max_position_size`. Kalshi: returns `None` (governed by risk profile).

## Core Fee Refactor

`core/fees.py` becomes generic functions parameterized by `FeeModel`:

```python
def arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.taker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))

def buy_side_arb_profit(ask_prices: list[float], fee_model: FeeModel) -> float:
    cost = sum(ask_prices)
    fees = sum(fee_model.taker_fee(p) for p in ask_prices)
    gross = 1.0 - cost - fees
    return gross - fee_model.profit_fee(max(gross, 0))

def maker_arb_profit(bid_prices: list[float], fee_model: FeeModel) -> float:
    gross = sum(bid_prices) - 1.0
    fees = sum(fee_model.maker_fee(p) for p in bid_prices)
    net = gross - fees
    return net - fee_model.profit_fee(max(net, 0))
```

`FeeModel` is threaded from exchange adapter → `ArbEngine.__init__` → every strategy call.

## Exchange Facade & Wiring

Each exchange provides a facade that bundles its components:

```python
class KalshiExchange:
    name = "kalshi"

    def __init__(self, config: dict):
        self.auth = KalshiAuth(config["api_key_id"], config["private_key_path"])
        self.api = KalshiAPI(config["base_url"], self.auth)
        self.fee_model = KalshiFeeModel()
        self.order_builder = KalshiOrderBuilder()
        self.constraints = KalshiConstraints()

    def create_feed(self, orderbook_mgr, on_update, on_fill) -> KalshiScanner:
        return KalshiScanner(self.ws_url, self.auth, orderbook_mgr, on_update, on_fill)

    def create_discovery(self, orderbook_mgr, scanner) -> KalshiDiscovery:
        return KalshiDiscovery(self.api, orderbook_mgr, scanner)
```

Factory registry in `src/exchanges/__init__.py`:

```python
EXCHANGES = {"kalshi": KalshiExchange}

def create_exchange(name: str, credentials: dict):
    return EXCHANGES[name](credentials)
```

`main.py` wires everything:

```python
class ArbBot:
    def __init__(self, config_path: str):
        cfg = load_config(config_path)
        exchange = create_exchange(cfg.get("exchange", "kalshi"), cfg["credentials"])
        
        self.engine = ArbEngine(risk_profile, exchange.fee_model)
        self.executor = ExecutionManager(
            api=exchange.api,
            order_builder=exchange.order_builder,
            constraints=exchange.constraints, ...)
        self.scanner = exchange.create_feed(self.orderbook_mgr, ...)
        self.discovery = exchange.create_discovery(self.orderbook_mgr, self.scanner)
```

## Strategy Extraction

`ArbEngine` becomes a thin coordinator. Each strategy moves to its own module:

| Current method | New location |
|---|---|
| `engine.evaluate()` | `strategies/taker.py:evaluate_sell_side()` |
| `engine.evaluate_buy_side()` | `strategies/taker.py:evaluate_buy_side()` |
| `engine.evaluate_near_expiry()` | `strategies/near_expiry.py:evaluate()` |
| `engine.evaluate_monotone_pair()` | `strategies/monotone.py:evaluate()` |
| `engine.evaluate_maker()` | `strategies/maker.py` (stays with MakerManager) |
| `engine.evaluate_two_sided()` | `strategies/two_sided.py` (stays with TwoSidedManager) |

All strategy functions receive `FeeModel`, `RiskProfile`, and optionally `PositionConstraints` as parameters.

Maker and two-sided stay as classes (they manage order lifecycle state) but receive `ExchangeAPI` + `OrderBuilder` ports instead of `KalshiAPI`.

## Data Model Changes

### Orderbook — standard bid/ask

```python
@dataclass
class Orderbook:
    bids: dict[float, float]     # price → quantity (buy side)
    asks: dict[float, float]     # price → quantity (sell side)
    
    def best_bid(self) -> float | None: ...
    def best_ask(self) -> float | None: ...
    def bid_depth_at(self, price: float) -> float: ...
    def ask_depth_at(self, price: float) -> float: ...
```

Replaces `yes_bids`/`no_bids`. The Kalshi scanner adapter does the `1.0 - no_bid_price` conversion when building the Orderbook.

### Event/Market — exchange field

```python
@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str
    close_time: str
    exchange: str = "kalshi"
    volume_24h: float = 0.0
    open_interest: float = 0.0
    liquidity: float = 0.0

@dataclass
class Event:
    event_ticker: str
    title: str
    markets: list[Market]
    exchange: str = "kalshi"
    mutually_exclusive: bool = True
    series_ticker: str = ""
```

### Normalized Fill

```python
@dataclass
class Fill:
    order_id: str
    ticker: str
    price: float
    quantity: int
    side: str           # "buy" | "sell"
    exchange: str
    timestamp: float
```

Replaces raw dicts with Kalshi-specific keys. Each exchange's feed adapter constructs Fill objects from its native format.

### TradeSignal — unchanged

Already exchange-agnostic.

## Config Changes

`config.yaml` gains an `exchange:` top-level key:

```yaml
exchange: kalshi          # kalshi | predictit | ibkr (future)
mode: demo
credentials:
  kalshi:
    demo:
      api_key_id: "..."
      private_key_path: "~/.kalshi/demo_private_key.pem"
    live:
      api_key_id: "..."
      private_key_path: "~/.kalshi/live_private_key.pem"
```

Backward compatible: missing `exchange:` defaults to `"kalshi"`. Existing flat `credentials.demo` is auto-mapped to `credentials.kalshi.demo` in the config loader.

## Migration Plan

### Phase 1: Create skeleton
- Create directory structure (`core/`, `ports/`, `exchanges/kalshi/`, `strategies/`)
- Write all Protocol classes in `ports/`
- `git mv` files to new locations
- Fix all imports

### Phase 2: Implement Kalshi adapters
- Wrap existing code to implement port protocols
- `KalshiFeeModel`, `KalshiOrderBuilder`, `KalshiConstraints`
- Scanner normalizes orderbook to `bids`/`asks` format
- Discovery implements `MarketDiscovery` protocol
- `KalshiExchange` facade

### Phase 3: Refactor core to use ports
- `core/fees.py` — accept `FeeModel` parameter
- `core/engine.py` — delegate to strategy modules
- `executor.py` — receive `ExchangeAPI` + `OrderBuilder`
- Strategy managers — port injection

### Phase 4: Wire up in main.py
- Config loader handles `exchange:` key
- `create_exchange()` factory
- `ArbBot.__init__` wires ports to core

### Phase 5: Update artifacts
- Update `CLAUDE.md` to reflect new architecture, directory structure, and module locations
- Update skills that reference specific file paths (`analyze-positions`, `live-test`, `post-run-analyst`, `strategy-review`, `strategy-tuning`, `dry-run`)
- Update `config.example.yaml` with `exchange:` field and new credential structure
- Update MCP server if tool implementations reference moved modules

## Testing

- **Import path fixes:** Existing tests update imports to new locations. Logic is unchanged.
- **Behavioral equivalence:** Run replay engine on recorded orderbook data before and after. Signal output must be identical — same trades, prices, quantities. This is the critical regression gate.
- **Protocol conformance:** Verify each Kalshi adapter satisfies its Protocol (call every method, check no errors).
- **Import isolation:** Automated test that verifies `src/core/` has zero imports from `src/exchanges/`. Enforces architectural boundary permanently.
- **Full test suite:** `python3 -m pytest tests/ -v` must pass with no regressions.

## What Doesn't Change

- Recorder schema (SQLite tables, column names)
- Analytics module (reads from recorder, exchange-agnostic)
- Replay engine (operates on recorded data)
- Risk profiles (RiskProfile dataclass, load_risk_profile)
- MCP server tools (may need import path updates but logic unchanged)
