# Execution Hardening & Strategy Pruning

## Context

Two live test sessions produced losses, both from the same root cause: partial fills on multi-leg batch orders leaving unhedged exposure.

- **Session 1 — DOTA2 phantom short ($0.89):** Taker sell-side arb. Unwind order IDs weren't tracked in `_processed_fill_ids`, so WS fill handler double-counted, creating a phantom short. Fixed by tracking unwind order IDs immediately.
- **Session 2 — KXHIGHTOKC partial fill ($0.11):** Buy-side arb. Sent 6-leg batch buy order; 4 tail-outcome legs filled ($0.01–$0.03 each), 2 high-value legs (B90.5 @ $0.75, B92.5 @ $0.08) went resting because the asks evaporated in the ~800ms between evaluation and execution. Bot force-killed during unwind.

The DOTA2 bug was a code defect (now fixed). The KXHIGHTOKC loss is a **structural problem** with buy-side arbs: asks can be pulled faster than orders reach the exchange, and the most valuable legs are exactly the ones most likely to fail.

## Strategy Changes

| Strategy | Action | Rationale |
|----------|--------|-----------|
| Taker (sell-side) | Keep, harden | Core strategy. Resting bids are structurally sticky — market makers leave them up longer than asks. |
| Buy-side taker | **Disable** | Two live losses from asks evaporating. The high-probability-outcome leg is always the one that fails — structural, not fixable with depth guards. |
| Near-expiry | Keep, harden | Same execution path as taker. Benefits from all taker hardening. |
| Maker | Keep as-is | Limit-order-based. Single-direction risk. No issues observed. |
| Monotone | Keep as-is | Pair-based, structurally different risk profile. |
| Two-sided | **Disable** | Most complex strategy (paired orders, inventory tracking, timeout/unwind). Untested in live. Re-enable once execution layer is proven reliable. |

### Config defaults

```yaml
strategy:
  enable_buy_side_arb: false
  two_sided_max_inventory: 0    # 0 = disabled
```

Risk profile presets updated: `enable_buy_side_arb: false` and `two_sided_max_inventory: 0` across all three presets (conservative, moderate, aggressive).

## Hardening: Three Layers

### Layer 1 — Tighter Staleness Threshold

**Change:** `Dispatcher.STALE_THRESHOLD_SECS` from `5.0` to `2.0`.

**Why:** The KXHIGHTOKC incident showed markets going from 17.9s stale → fresh snapshot → signal fire in 1ms. A 2s threshold ensures orderbook data is very recent, reducing the window for price drift between evaluation and execution.

**Location:** `src/dispatch.py` line 15, single constant change.

**Trade-off:** More frequent stale rejections during periods of low WS activity. Acceptable — the staleness guard exists precisely to prevent trading on outdated data.

### Layer 2 — Ask-Side Depth Check for Taker Sells

**Problem:** The bot checks bid depth before selling (ensuring resting bids exist at the target price) but doesn't verify ask-side conditions. A market with bids but no asks is one-sided — our sell order at the best bid may go resting if the counterparty has pulled their ask.

**Change:** Add `min_ask_depth` check to `ArbEngine._validate_legs()`. For each leg, verify that the best yes ask exists and has at least `min_ask_depth` contracts of depth. If the ask book is empty or too thin, reject the signal.

**Specifics:**
- New `RiskProfile` field: `min_ask_depth: int` (default: same as `min_bid_depth` per preset)
- In `_validate_legs`, after the bid depth check: call `book.best_yes_ask()`. If `None`, reject — no ask means a one-sided market. Then check `book.yes_ask_depth_at(best_ask)` against `min_ask_depth`.
- This validates that the market is two-sided — both bid and ask liquidity exist. A one-sided market (bids only, no asks) is a red flag for stale/phantom bids that will go resting when we try to sell.

**Preset values:**
- Conservative: `min_ask_depth: 5`
- Moderate: `min_ask_depth: 2`
- Aggressive: `min_ask_depth: 1`

### Layer 3 — Sequential Leg Execution (Highest Price First)

**Problem:** Batch execution sends all legs in one API call. If any leg goes resting, we've already committed capital on the filled legs and must unwind at a loss.

**Change:** Execute legs one at a time, ordered by price descending (most expensive first). The most expensive leg is hardest to fill — if it fails, we abort before committing any capital on cheaper legs.

**Flow:**
1. Sort legs by price descending
2. Send the first (most expensive) leg as a single-order batch call
3. If `status != "executed"`: cancel it, abort — zero exposure, zero loss
4. If filled: record fill, track order ID, send next leg
5. Continue until all legs fill (success) or one fails (unwind only already-filled legs)
6. On failure at step N: the filled legs are cheap tail outcomes (by sort order), minimizing unwind cost

**Configuration:**
- New config field: `sequential_execution: bool` (default: `true`)
- New `RiskProfile` field: `sequential_execution: bool`
- When `false`, falls back to current batch execution (for backward compat or when latency matters)

**Latency trade-off:** Adds ~100-150ms per leg (one API round-trip each). For a 6-leg event: ~600-900ms total vs ~150ms for batch. Acceptable because:
- Arb opportunities on Kalshi last seconds to minutes, not milliseconds
- The latency cost is far less than the unwind cost of a partial fill
- Sequential execution eliminates the worst-case scenario (multiple expensive legs filled, key leg resting)

**Implementation in `ExecutionManager.execute()`:**
- When `sequential_execution` is enabled, replace the single `batch_create_orders(orders)` call with a loop
- Sort orders by `yes_price` descending before the loop
- Each iteration: send one order, check status, proceed or abort
- On abort: cancel the failed order, launch unwind for any previously filled legs
- The existing `_launch_unwind` / `_unwind_partial_fill` machinery handles the unwind — no changes needed there

## Files to Modify

| File | Changes |
|------|---------|
| `src/risk.py` | Add `min_ask_depth` and `sequential_execution` to `RiskProfile`. Update all three presets. Set `enable_buy_side_arb: false` and `two_sided_max_inventory: 0` in all presets. |
| `src/engine.py` | Add ask-depth check to `_validate_legs()`. Pass `min_ask_depth` from risk profile. |
| `src/dispatch.py` | Change `STALE_THRESHOLD_SECS` to `2.0`. |
| `src/executor.py` | Add sequential execution mode to `execute()`. Sort legs by price desc, send one at a time, abort on first resting order. |
| `src/config.py` | Add `sequential_execution` config field. |
| `src/main.py` | Wire `sequential_execution` config to `ExecutionManager`. |
| `config.example.yaml` | Update defaults and add comments for new fields. |
| `CLAUDE.md` | Update strategy docs and execution fidelity section. |
| `tests/test_engine.py` | Add tests for ask-depth rejection. |
| `tests/test_executor.py` | Add tests for sequential execution (mock API, verify leg ordering and abort-on-resting). |
| `tests/test_staleness.py` | Update `STALE_THRESHOLD_SECS` references if hardcoded. |

## Verification

1. `python3 -m pytest tests/ -v` — all tests pass
2. Verify buy-side arb is disabled: grep config for `enable_buy_side_arb: false`
3. Verify two-sided is disabled: grep config for `two_sided_max_inventory: 0`
4. Run dry-run with `--ws-race-rate 1.0` to verify sequential execution doesn't break fault injection tests
5. Live test (30 min minimum): confirm no signals fire on thin books, staleness rejections logged at DEBUG level, sequential execution logs each leg individually
