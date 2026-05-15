# Buy-Side Arb Partial Fill Fixes

## Problem

On 2026-05-15, a buy-side arb on KXKBOGAME-26MAY150530KIASAM (2 outcomes, asks at $0.24 and $0.66, sum=$0.90, 7.15% profit) resulted in a $0.65 loss. The KIA leg at $0.24 went "resting" (no YES-side liquidity — the ask was synthetic, derived from a NO bid). The SAM leg filled at $0.66. After 30s timeout, unwind sold SAM at $0.01 (phase 3 fallback).

Three failures compounded:
1. The executor waited 30s for a resting buy-side leg that would never fill
2. Unwind jumped from $0.60 to $0.01 with no intermediate prices
3. Emergency shutdown failed due to 429 rate limiting with no retry

## Fix 1: Immediate Cancellation for Resting Buy-Side Legs

**File:** `src/executor.py` — `execute()`

After receiving the batch response, if the signal is buy-side (all leg_actions == "buy") and any leg has status "resting", skip `_monitor_fills`. Instead: cancel all resting legs immediately, then unwind any filled legs.

Rationale: buy-side legs target the YES ask. If the ask wasn't real (synthetic from NO bid), the order will rest indefinitely. Waiting 30s only lets the filled leg lose value.

This does NOT apply to sell-side arbs, where resting is expected (posting at best bid).

## Fix 2: Graduated Unwind Pricing

**File:** `src/executor.py` — `_unwind_partial_fill()`

Replace the 3-phase unwind with 5 graduated phases.

### Closing a long (buy-side partial fill, unwind by selling):

| Phase | Price | Wait before |
|-------|-------|-------------|
| 1 | fill_price - 1*step | 0s (immediate) |
| 2 | fill_price - 2*step | phase1_secs (15s) |
| 3 | fill_price - 4*step | phase2_secs - phase1_secs (15s) |
| 4 | max(fill_price * 0.5, 0.01) | phase2_secs (30s) |
| 5 | 0.01 | phase2_secs (30s) |

Example for fill at $0.66, step=$0.03: $0.63 → $0.60 → $0.54 → $0.33 → $0.01

### Closing a short (sell-side partial fill, unwind by buying):

| Phase | Price | Wait before |
|-------|-------|-------------|
| 1 | fill_price + 1*step | 0s (immediate) |
| 2 | fill_price + 2*step | phase1_secs |
| 3 | fill_price + 4*step | phase2_secs - phase1_secs |
| 4 | min(fill_price + (1 - fill_price) * 0.5, 0.99) | phase2_secs |
| 5 | 0.99 | phase2_secs |

The key change: phase 3 now uses 4*step instead of jumping to the floor/ceiling, and phase 4 uses 50% of remaining distance. The absolute fallback ($0.01 / $0.99) is phase 5, reached only after ~90s total instead of ~30s.

## Fix 3: Emergency Shutdown Retry Loop

**File:** `src/main.py` — `_emergency_shutdown()`

Wrap the shutdown in a 3-attempt retry loop with exponential backoff. Split into two independent operations (cancel orders, then close positions) so a failure in one doesn't block the other. If all retries exhausted, log CRITICAL with "manual intervention required".

```
for attempt in range(3):
    try:
        cancel all resting orders
    except: log warning, continue

    try:
        close all positions
    except:
        if last attempt: log CRITICAL
        else: sleep(2^attempt + 1), retry
```
