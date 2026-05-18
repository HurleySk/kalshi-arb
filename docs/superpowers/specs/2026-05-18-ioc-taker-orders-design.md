# IOC Taker Orders + Maker TTL

## Problem

All orders (taker and maker) are submitted as GTC limit orders with no `time_in_force` or `expiration_ts`. When the price moves before the order matches, orders sit on Kalshi's book indefinitely. The bot's `fill_timeout_secs` monitor only covers the active execution window — orphaned orders from crashes, restarts, or missed cleanups accumulate as phantom "pending" positions on the exchange.

## Solution

Use Kalshi's `time_in_force` and `expiration_ts` API fields to match order lifecycle to trading intent:

- **Taker orders** (sell-side, buy-side, near-expiry, monotone): `time_in_force: "immediate_or_cancel"` — fill instantly or die.
- **Maker orders**: `expiration_ts` set to `now + maker_order_ttl_secs` — rest briefly, auto-cancel if the bot loses track.
- **Two-sided orders**: Same `expiration_ts` approach as maker.
- **Unwind orders**: `expiration_ts` of 60 seconds per phase — safety net since phases already cancel-and-replace.
- **Boot close orders**: `expiration_ts` of 60 seconds — priced to fill immediately but bounded.

## Design

### 1. Order Builder (`exchanges/kalshi/order_builder.py`)

Add optional `time_in_force` and `expiration_ts` parameters to `build_sell_order` and `build_buy_order`. When provided, the field is included in the order dict. When omitted, the field is absent (Kalshi defaults to GTC).

```python
def build_sell_order(self, ticker, price, quantity, *,
                     time_in_force=None, expiration_ts=None) -> dict:
    order = {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(price * 100), "count": quantity,
    }
    if time_in_force:
        order["time_in_force"] = time_in_force
    if expiration_ts:
        order["expiration_ts"] = expiration_ts
    return order
```

Same pattern for `build_buy_order`. The `OrderBuilder` protocol in `src/ports/` gets the same optional kwargs so non-Kalshi adapters can implement or ignore them.

### 2. Taker Execution (`executor.py`)

**Batch path:** Pass `time_in_force="immediate_or_cancel"` when building taker orders. After the batch response:

- Each order is either `"executed"` (filled) or `"cancelled"` (IOC rejected). No "resting" state.
- Skip `_monitor_fills` entirely for IOC orders — fill status is known immediately from the batch response.
- Partial fill handling (some executed, some cancelled) triggers the same blacklist + unwind path as today, but instantly instead of after `fill_timeout_secs`.

**Sequential path:** Same — each leg submitted IOC. If status != "executed", abort immediately. Keep the existing cancel call as a no-op safety net (the exchange already cancelled the IOC order).

**Unwind orders:** Stay GTC but add `expiration_ts = int(time.time()) + 60` on each phase's order. Phases already cancel-and-replace, so the 60-second TTL is a safety net for crashes mid-unwind.

**How the executor knows it's IOC:** The executor always builds taker orders as IOC. It already knows whether it's running a taker signal (it's in `execute()` or `_execute_sequential()`). No flag needed — IOC is unconditional for all taker execution.

### 3. Maker Strategy (`strategies/maker.py`)

**Constructor:** Accept `maker_order_ttl_secs` (int, default 300).

**`post()`:** Pass `expiration_ts = int(time.time()) + self._order_ttl_secs` to each `build_sell_order` call.

**`on_orderbook_update()` (reprice):** Replacement orders get a fresh `expiration_ts = int(time.time()) + self._order_ttl_secs`.

**`_tighten_unfilled()` and `_complete_cancel_and_take()`:** These are post-fill completion orders trying to hit existing liquidity. Use `time_in_force="immediate_or_cancel"` instead of letting them rest.

### 4. Two-Sided Strategy (`strategies/two_sided.py`)

**Constructor:** Accept `maker_order_ttl_secs` (int, default 300).

**`post()`:** Both buy and sell legs get `expiration_ts = int(time.time()) + self._order_ttl_secs`. The existing `two_sided_timeout_secs` handles pair-level timeout; `expiration_ts` is the per-order safety net.

### 5. Boot Reconciliation (`main.py`)

No changes needed. The existing `_boot_reconcile` already cancels all resting orders on startup. With IOC taker orders, the orphan count drops to near zero (only maker/two-sided orders that outlived their TTL due to a crash between TTL set and exchange processing).

**`build_close_order`:** Add `expiration_ts = int(time.time()) + 60` to close orders used during boot reconciliation. These are priced at extreme values (99¢/1¢) to fill immediately, but the 60-second TTL prevents them from sitting forever if the market is halted.

### 6. Config

One new field in `config.yaml` under `strategy:`:

```yaml
maker_order_ttl_secs: 300    # How long maker/two-sided resting orders live (seconds)
```

Default: 300 (5 minutes). Threaded through `main.py` to `MakerManager` and `TwoSidedManager` constructors.

No config for taker IOC — unconditional. No config for unwind/close TTL — hardcoded at 60 seconds.

### 7. Port Interface (`src/ports/`)

The `OrderBuilder` protocol's `build_sell_order` and `build_buy_order` signatures get optional `time_in_force` and `expiration_ts` kwargs. Existing adapters (PredictIt) can ignore them if the exchange doesn't support these fields.

## Files Changed

| File | Change |
|------|--------|
| `src/exchanges/kalshi/order_builder.py` | Add `time_in_force` and `expiration_ts` params to build methods |
| `src/ports/order_builder.py` | Update protocol signatures with optional kwargs |
| `src/executor.py` | IOC for taker orders; skip `_monitor_fills` for IOC; `expiration_ts` on unwind orders |
| `src/strategies/maker.py` | Accept TTL config; `expiration_ts` on post/reprice; IOC on tighten/complete orders |
| `src/strategies/two_sided.py` | Accept TTL config; `expiration_ts` on post |
| `src/main.py` | Thread `maker_order_ttl_secs` to maker/two-sided; `expiration_ts` on boot close orders |
| `config.example.yaml` | Document `maker_order_ttl_secs` |

## Testing

- Unit tests for order builder: verify `time_in_force` and `expiration_ts` fields appear when passed, absent when not.
- Unit tests for executor: verify IOC orders skip `_monitor_fills`; verify partial IOC handling triggers unwind.
- Unit tests for maker: verify `expiration_ts` on post and reprice; verify IOC on tighten orders.
- Integration test: verify boot reconcile still cancels resting orders.
- Live validation: run in demo mode, confirm no orders remain resting on Kalshi after a bot session.

## What This Does NOT Change

- `_monitor_fills` still exists for any future non-IOC execution path. It just won't be called for standard taker signals.
- The fill timeout, unwind phases, and circuit breaker logic are unchanged.
- Boot reconciliation is unchanged — it's already correct.
- Capital guard reservation/release flow is unchanged.
