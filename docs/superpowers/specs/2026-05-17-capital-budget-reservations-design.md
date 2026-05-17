# Capital Budget & Position Reservations — Design Spec

**Date:** 2026-05-17
**Status:** Draft
**Goal:** Add per-exchange capital budgeting (virtual sub-account) and persistent position reservations (manual trades the bot must not touch).

## Context

The bot currently trades with whatever balance is available on the exchange. Two problems arise as we add exchanges:

1. **Over-deployment** — Exchange minimum deposits may exceed what you want the bot to risk. You might deposit $100 on Kalshi (their minimum) but only want the bot trading with $25 of it.
2. **Position conflicts** — You may hold manual positions (directional bets outside arb/maker strategies) on the same exchange. The bot's boot reconcile, emergency shutdown, and unwind logic currently treats all positions as its own.

## Feature 1: Per-Exchange Capital Budget

### Concept

A `CapitalGuard` acts as a middleware between signal generation and execution. It maintains a local ledger of deployed capital (filled positions + resting orders) and rejects new trades when headroom is exhausted.

### Ledger Model

```
deployed = sum of (price × quantity) for all bot-owned open positions
         + sum of (price × quantity) for all bot-owned resting orders (maker, two-sided)
```

Reserved positions (Feature 2) are excluded from the ledger — they belong to the user.

### Interface (`src/core/capital_guard.py`)

```python
class CapitalGuard:
    def __init__(self, budgets: dict[str, float], exchange_name: str): ...
    def can_execute(self, exchange: str, cost: float) -> bool: ...
    def commit(self, exchange: str, order_id: str, cost: float) -> None: ...
    def release(self, exchange: str, order_id: str) -> None: ...
    def headroom(self, exchange: str) -> float: ...
    def deployed(self, exchange: str) -> float: ...
```

- `can_execute` — Returns True if `deployed + cost <= budget`
- `commit` — Called when an order is placed (resting) or fills
- `release` — Called when an order is cancelled, position is closed, or unwind completes
- `headroom` — Returns `budget - deployed` (remaining available capital)
- `deployed` — Returns current total committed capital

### What Counts as Deployed

| Event | Ledger Effect |
|-------|---------------|
| Taker order fills | `+commit(fill_price × qty)` |
| Maker order posted (resting) | `+commit(limit_price × qty)` |
| Two-sided pair posted | `+commit(bid_price × qty + ask_price × qty)` |
| Order cancelled (maker reprice, timeout) | `-release` |
| Position closed (arb completion, unwind) | `-release` |
| Bot position settled at expiry | `-release` |

### Gating Point

In `ArbBot._execute_and_track()`, before calling `executor.execute()`:

```python
cost = sum(price * signal.quantity for _, price in signal.legs)
if not self.capital_guard.can_execute(self.cfg.exchange, cost):
    logger.info("capital_limit: skipping %s (need $%.4f, headroom $%.4f)",
                signal.event_ticker, cost, self.capital_guard.headroom(self.cfg.exchange))
    # Record as near-miss for observability
    self.dispatcher.mark_execution_complete(signal.event_ticker)
    return
```

Similarly, `MakerManager.post()` and `TwoSidedManager.post()` check headroom before placing resting orders.

### Boot Initialization

At startup, after boot reconcile identifies bot-owned positions (excluding reservations), the capital guard seeds its ledger:

```python
for ticker, qty in bot_positions:
    # Use the position's average price from the exchange API response (market_positions[].average_price_fp)
    price = avg_price_from_api
    self.capital_guard.commit(self.cfg.exchange, f"boot_{ticker}", price * qty)
```

### Config

```yaml
# In config.yaml — omit section or set to 0 for unlimited
capital_budget:
  kalshi: 25.00
  # predictit: 50.00
```

If an exchange is not listed in `capital_budget`, no cap is enforced (backward compatible).

### Observability

- STATUS line includes `capital_headroom=$X.XX` when a budget is configured
- Near-miss log: `capital_limit: skipping <event> (need $X.XX, headroom $Y.YY)`
- MCP tool `get_risk_profile` includes capital budget info in its response

## Feature 2: Position Reservations

### Concept

A `ReservationStore` persists user-declared position reservations to a JSON file. The bot respects these at every point where it might modify positions: boot reconcile, execution, unwind, and emergency shutdown.

### Data Model

```python
@dataclass
class Reservation:
    ticker: str          # market ticker (e.g., "KXBTC-25MAY16-T55000")
    side: str            # "yes" or "no"
    quantity: int        # contracts reserved
    exchange: str        # which exchange this is on
    created_at: str      # ISO 8601 timestamp
    note: str = ""       # optional user annotation
```

### Interface (`src/core/reservation_store.py`)

```python
class ReservationStore:
    def __init__(self, path: str = "data/reservations.json"): ...
    def reserve(self, ticker: str, side: str, quantity: int, exchange: str, note: str = "") -> None: ...
    def release(self, ticker: str) -> None: ...
    def is_reserved(self, ticker: str) -> bool: ...
    def get_reserved_quantity(self, ticker: str, side: str) -> int: ...
    def list_all(self) -> list[Reservation]: ...
```

### Persistence

- Storage: `data/reservations.json`
- Write strategy: atomic (write to temp file, then `os.replace` to target path)
- Read: loaded into memory at startup, kept in sync on every mutation
- Format: JSON array of reservation objects

### Where Reservations Are Checked

| Component | Behavior |
|-----------|----------|
| **Boot reconcile** | Position on reserved ticker: skip (don't close as orphan, don't load into bot tracker beyond reserved qty) |
| **Executor unwind** | Subtract reserved qty from unwind target on that ticker |
| **Emergency shutdown** | Skip reserved tickers entirely when closing all positions |
| **MCP `close_all_positions`** | Skip reserved tickers |
| **MCP `close_position`** | Warn if ticker is reserved; require explicit override |

### Partial Reservation

If a position has 5 contracts and 3 are reserved, the bot owns 2. At boot:
- Load 2 into PositionTracker (bot-owned)
- Reserve 3 is untouchable
- Capital guard ledger includes only the 2 bot-owned contracts

### MCP Tools

Added to `src/mcp_server.py`:

**`reserve_position`**
- Params: `ticker` (required), `side` (required), `quantity` (required), `exchange` (optional, defaults to current), `note` (optional)
- Persists immediately to disk
- Returns confirmation with current reservation list

**`release_position`**
- Params: `ticker` (required)
- Removes reservation, persists to disk
- Returns confirmation

**`list_reservations`**
- No params
- Returns all active reservations with their details

## Integration: Composition Root

In `src/main.py`, the wiring order:

```python
self.reservations = ReservationStore(path="data/reservations.json")
self.capital_guard = CapitalGuard(
    budgets=self.cfg.capital_budgets,
    exchange_name=self.cfg.exchange,
)
```

Both are passed to boot reconcile, and `capital_guard` is checked in `_execute_and_track` and maker/two-sided posting paths.

## Config Changes Summary

```yaml
# config.example.yaml additions:

# Capital budget per exchange (omit or set to 0 for unlimited)
# Bot will not deploy more than this amount on a given exchange.
# Resting orders (maker, two-sided) count toward the budget.
capital_budget:
  kalshi: 25.00
  # predictit: 50.00
```

No config for reservations — they're managed entirely via MCP tools and persisted in `data/reservations.json`.

## File Changes

| File | Change |
|------|--------|
| `src/core/capital_guard.py` | New — CapitalGuard class |
| `src/core/reservation_store.py` | New — ReservationStore class |
| `src/main.py` | Wire CapitalGuard + ReservationStore; gate execution; modify boot reconcile |
| `src/executor.py` | Callbacks for commit/release on fill/cancel |
| `src/strategies/maker.py` | Check headroom before posting; commit/release on post/cancel |
| `src/strategies/two_sided.py` | Check headroom before posting; commit/release on post/cancel |
| `src/mcp_server.py` | Add reserve_position, release_position, list_reservations tools |
| `src/config.py` | Parse `capital_budget` section |
| `config.example.yaml` | Document capital_budget section |
| `tests/test_capital_guard.py` | New — unit tests for CapitalGuard |
| `tests/test_reservation_store.py` | New — unit tests for ReservationStore |

## Non-Goals

- Cross-exchange capital pooling (each exchange is independent)
- Auto-detecting manual positions (user must explicitly reserve via MCP)
- Real-time API balance checks (local ledger only, reconciled at boot)
- Reserved positions affecting bot's capital budget (they're independent)
