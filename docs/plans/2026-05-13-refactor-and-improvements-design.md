# Refactor and Improvements Design

**Date:** 2026-05-13
**Scope:** Reliability fixes, architecture cleanup, strategy improvements

## Phase 1 — Reliability (fix what can lose money)

### 1a. Async orderbook callback

**Problem:** `_on_orderbook_update` runs synchronously inside `MarketScanner.listen()`. While `engine.evaluate()` itself is fast, the synchronous call blocks the WS read loop — meaning subsequent orderbook deltas and fill events queue up in the WS buffer. Under high update rates, this introduces latency between market state and bot reaction.

**Fix:** Replace the synchronous callback with an async queue pattern:
- `MarketScanner.listen()` puts update events onto an `asyncio.Queue`
- A separate `_process_orderbook_updates` coroutine drains the queue and calls evaluate/maker logic
- This decouples WS ingestion from evaluation, so fills and deltas are never delayed by strategy logic

**Constraint:** Must preserve ordering — updates for the same event must be processed in sequence. Cross-event ordering doesn't matter.

### 1b. Guard duplicate signal execution

**Problem:** Between `evaluate()` returning a signal and `execute()` setting `self._executing = True`, another orderbook update for the same event can trigger a second `evaluate()` → second `create_task(execute)`. The `_executing` flag prevents the second from running, but only after wasting an API validation call and a task creation.

**Fix:** Add a per-event lock or "pending execution" set:
- Before calling `evaluate()`, check if event is already pending execution
- Add event to pending set before `create_task`, remove in finally block
- This is lighter than `_executing` (which is global) and prevents evaluation waste

### 1c. Periodic expired event cleanup

**Problem:** Events that close/expire are never removed from `OrderbookManager._event_markets`, `_market_to_event`, `_books`, or `ArbBot._market_metadata`. Over a multi-day run, this accumulates thousands of dead market subscriptions.

**Fix:** Add a periodic cleanup task (~every 5 minutes):
- Check `_market_metadata` close times against `now()`
- Call `orderbook_mgr.unregister_event()` for expired events
- Remove from `_event_tickers` and `_market_metadata`
- WS subscriptions for dead markets are harmless (Kalshi stops sending updates) but the local bookkeeping matters for memory

### 1d. Broaden API retry

**Problem:** `_request()` only retries on 429. Transient 502/503/504 errors and connection resets abort the request, which can fail a batch order mid-execution.

**Fix:** Retry on `{429, 500, 502, 503, 504}` and `aiohttp.ClientConnectionError`. Keep the existing exponential backoff. Non-retryable errors (400, 401, 404) still raise immediately.

---

## Phase 2 — Architecture cleanup

### 2a. Extract dispatch layer (`src/dispatch.py`)

Move `_on_orderbook_update` and `_on_fill` logic out of `ArbBot` into a `Dispatcher` class. The dispatcher holds references to `engine`, `executor`, `maker` and routes events to the right handler. `ArbBot` creates the dispatcher and passes it to `MarketScanner`.

This reduces `main.py` by ~80 lines and makes the routing testable in isolation.

### 2b. Extract event discovery (`src/discovery.py`)

Move `_full_scan`, `_discover_events`, `_register_events`, `_market_metadata`, and the cleanup task (from 1c) into an `EventDiscovery` class. It owns the mapping from event tickers to market metadata.

`ArbBot.run()` becomes pure orchestration: create components, wire them together, gather tasks.

### 2c. Consolidate config wiring

Both `ExecutionManager` and `MakerManager` accept individual fields from `RiskProfile`. Instead, pass `RiskProfile` directly and let each manager extract what it needs. Removes fragile constructor parameter lists and prevents drift when new risk fields are added.

### 2d. Update CLAUDE.md

- Remove "No WebSocket reconnection logic" (reconnection was added)
- Document new modules (`dispatch.py`, `discovery.py`)
- Update architecture diagram

---

## Phase 3 — Strategy improvements

### 3a. Dynamic quantity sizing

**Currently:** Always trades 1 contract per leg, regardless of available depth.

**Improvement:** Size = `min(bid_depth_at_best_bid)` across all legs, capped by `max_contracts_per_arb` (new config field, default 1 for safety). This captures more profit when all legs have depth > 1.

**Constraint:** Fee calculation must scale correctly — `arb_profit` already works per-contract, so the profit check is unchanged. Only the quantity passed to `executor.execute()` changes.

### 3b. Fix PositionTracker for closes/unwinds

**Currently:** `record_fill` only adds to position quantity, never decrements. After an unwind buy-back, the tracker still shows the original sell position.

**Fix:** Track action direction:
- `sell` → increase position quantity
- `buy` → decrease position quantity (close/unwind)
- When quantity hits 0, remove the position

### 3c. Accurate PnL in status report

**Currently:** `_report_status` calculates `realized_pnl = sum(avg_price * quantity)` which is actually "premium collected on open positions," not PnL.

**Fix:** With the fixed tracker (3b), compute:
- **Realized PnL:** sum of closed position profits (sell price - buy price per contract)
- **Unrealized PnL:** premium collected on still-open positions minus mark-to-market exit cost
- Report both separately

### 3d. Use open_interest and liquidity for filtering

These fields are already parsed from the API into `Market` objects and stored in `_market_metadata`. Add optional thresholds to `RiskProfile`:
- `min_open_interest: float = 0` — reject legs with insufficient open interest
- `min_liquidity: float = 0` — reject illiquid markets

Default to 0 (disabled) so existing behavior is unchanged.

---

## Implementation order

Strict dependency chain:
1. Phase 1 items (1a → 1b → 1c → 1d) — each is independent, can be parallelized
2. Phase 2 items (2a → 2b → 2c → 2d) — 2a and 2b can be parallelized, 2c and 2d depend on both
3. Phase 3 items (3b → 3c, 3a and 3d are independent) — 3c depends on 3b

Estimated scope: ~12 tasks, each self-contained with tests.
