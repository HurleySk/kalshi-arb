# Execution Fidelity — Defense in Depth

**Date:** 2026-05-15
**Status:** Approved
**Motivation:** A live trading session produced a 10-minute bot freeze when a partial fill unwind hung on an unresponsive API call. The unwind had no timeout, blocked the entire executor, prevented 3 other profitable arbs from firing, and left $1.22 in unrecovered losses. Investigation revealed 12 gaps across every layer of the execution stack where hanging API calls or stale data could cause similar failures.

## Scope

Eight layers of protection, each independent — any single layer protects against the failure mode even if others miss it.

## Layer 1: HTTP Transport Timeout

**File:** `src/api.py`
**Change:** Add `aiohttp.ClientTimeout(total=30, connect=10, sock_read=15)` to the `ClientSession` constructor in `_ensure_session()`.

This is the backstop. No HTTP request can hang longer than 30 seconds regardless of what the caller does. Protects every API call site automatically without per-call changes.

**Timeout values:**
- `total=30` — absolute maximum for any request including retries
- `connect=10` — TCP connection establishment
- `sock_read=15` — waiting for response data after connection

## Layer 2: Executor API Call Timeouts

**File:** `src/executor.py`
**Changes:** Wrap remaining unprotected API calls in `asyncio.wait_for`:

| Call site | Method | Timeout | On timeout |
|-----------|--------|---------|------------|
| `execute()` | `batch_create_orders` (initial arb) | 15s | Log error, mark as failed |
| `execute()` | `batch_cancel_orders` (buy-side cancel) | 10s | Log warning, proceed |
| `execute()` | `get_balance` (pre-flight) | 10s | Log warning, proceed anyway |
| `_monitor_fills()` | `batch_cancel_orders` (timeout cancel) | 10s | Log warning, proceed to unwind |

These are defense-in-depth on top of Layer 1 — they fire faster than the 30s transport timeout and provide call-site-specific error handling.

## Layer 3: Emergency Shutdown Timeout

**File:** `src/main.py`
**Change:** Wrap `_emergency_shutdown()` in `asyncio.wait_for(timeout=60)`.

Inside, wrap each API call with individual 15s timeouts:
- `maker.cancel_all()` — 15s
- `api.get_open_orders()` — 15s
- `api.batch_cancel_orders()` — 15s
- `api.get_positions()` — 15s
- `api.batch_create_orders()` (close orders) — 15s

If the overall 60s fires, log CRITICAL and proceed. Leaving orders open is better than hanging forever — the operator can reconcile manually.

## Layer 4: Boot Reconcile Timeout

**File:** `src/main.py`
**Change:** Wrap `_boot_reconcile()` in `asyncio.wait_for(timeout=60)`.

On timeout, log WARNING and proceed. The bot can still trade without reconciling stale orders — it just won't cancel or close leftover positions from a prior session.

## Layer 5: WebSocket Reconnect Timeout

**File:** `src/scanner.py`
**Change:** Wrap `connect()` inside `_reconnect()` with `asyncio.wait_for(timeout=15)`.

On timeout, retry with exponential backoff (1s, 2s, 4s) up to 3 attempts. If all attempts fail, re-raise — the outer reconnect loop will handle it.

## Layer 6: Orderbook Staleness Detection

**Files:** `src/scanner.py`, `src/dispatch.py`

**Scanner changes:**
- Add `_last_update_ts: dict[str, float]` to `OrderbookManager` — maps market ticker to `time.time()` of last snapshot or delta
- Update `apply_snapshot()` and `apply_delta()` to record the timestamp
- Add `market_age(ticker) -> float` method returning seconds since last update

**Dispatcher changes:**
- Before evaluating signals in `process_orderbook_update()`, check `orderbook_mgr.market_age(ticker)` for each market in the event
- If any market's data is older than 5.0 seconds, skip signal evaluation for that event
- Log stale events at WARNING: `"stale orderbook for %s: age=%.1fs — skipping signal evaluation"`

**Threshold:** 5 seconds, hardcoded. In normal operation orderbooks update sub-second. 5s definitively indicates a connectivity issue.

## Layer 7: SIGTERM Handler

**File:** `src/main.py`
**Change:** Register signal handlers in the event loop for `SIGTERM` and `SIGINT`:

```python
loop = asyncio.get_event_loop()
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
```

`_graceful_shutdown()`:
1. Cancel all running tasks (scanner, dispatcher, snapshot loop, balance loop)
2. Call `executor.cancel_unwinds()` to await/cancel active unwind tasks
3. Run `_emergency_shutdown()` with its 60s timeout (Layer 3)
4. Close scanner, recorder, API session

## Layer 8: Recent Trades Timeout

**File:** `src/main.py` (`_validate_recent_trades` at line 172)
**Change:** Wrap each `api.get_market_trades(ticker)` call in `asyncio.wait_for(timeout=10)`.

On timeout, treat as "no recent trades" — this is conservative (rejects the signal rather than trading blind on unverified data).

## Testing Strategy

- Unit tests for each timeout: mock API to raise `asyncio.TimeoutError`, verify the caller handles it correctly
- Unit test for staleness: set `_last_update_ts` to old value, verify signals are suppressed
- Regression test: verify the existing 248 tests still pass (timeouts should never fire under mocked conditions)

## Non-Goals

- Changing the graduated unwind phase logic (already fixed in prior commit)
- Adding circuit breaker improvements beyond what was fixed in the review commit
- Changing fee math, risk profiles, or strategy parameters
- Adding retry logic to timed-out calls (the transport layer already retries; app-level retries add complexity without clear benefit)
