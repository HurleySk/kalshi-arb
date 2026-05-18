# IOC Taker Orders + Maker TTL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate phantom pending positions by using IOC for taker orders and expiration TTL for maker/two-sided resting orders.

**Architecture:** Add `time_in_force` and `expiration_ts` optional params to the order builder interface. Taker execution unconditionally uses IOC. Maker and two-sided strategies pass `expiration_ts` for resting orders. Unwind and boot close orders get short TTLs as safety nets.

**Tech Stack:** Python asyncio, Kalshi REST API `time_in_force` field, existing ports & adapters pattern.

**Spec:** `docs/superpowers/specs/2026-05-18-ioc-taker-orders-design.md`

---

## File Structure

| File | Role | Action |
|------|------|--------|
| `src/ports/order_builder.py` | Protocol interface for order building | Modify: add `**kwargs` to signatures |
| `src/exchanges/kalshi/order_builder.py` | Kalshi-specific order formatting | Modify: accept and pass through `time_in_force`, `expiration_ts` |
| `src/executor.py` | Taker order execution | Modify: build IOC orders, skip `_monitor_fills` for IOC, add `expiration_ts` to unwind |
| `src/strategies/maker.py` | Maker order posting and repricing | Modify: accept TTL config, pass `expiration_ts`, IOC on tighten/complete |
| `src/strategies/two_sided.py` | Two-sided market making | Modify: accept TTL config, pass `expiration_ts` |
| `src/config.py` | Config loading | Modify: add `maker_order_ttl_secs` field |
| `src/main.py` | Composition root | Modify: thread TTL config to managers, `expiration_ts` on boot close orders |
| `config.example.yaml` | Example config | Modify: document `maker_order_ttl_secs` |
| `tests/test_order_builder.py` | New: order builder tests | Create |
| `tests/test_executor.py` | Executor tests | Modify: add IOC-specific tests |
| `tests/test_maker.py` | Maker tests | Modify: add TTL/IOC tests |
| `tests/test_two_sided.py` | Two-sided tests | Modify: add TTL tests |

---

### Task 1: Order Builder — Add `time_in_force` and `expiration_ts`

**Files:**
- Create: `tests/test_order_builder.py`
- Modify: `src/exchanges/kalshi/order_builder.py`
- Modify: `src/ports/order_builder.py`

- [ ] **Step 1: Write failing tests for Kalshi order builder**

Create `tests/test_order_builder.py`:

```python
from src.exchanges.kalshi.order_builder import KalshiOrderBuilder


def test_build_sell_order_default_no_tif():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1)
    assert order["ticker"] == "TICKER"
    assert order["yes_price"] == 55
    assert order["count"] == 1
    assert order["action"] == "sell"
    assert "time_in_force" not in order
    assert "expiration_ts" not in order


def test_build_sell_order_with_ioc():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1, time_in_force="immediate_or_cancel")
    assert order["time_in_force"] == "immediate_or_cancel"
    assert "expiration_ts" not in order


def test_build_sell_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_sell_order("TICKER", 0.55, 1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert "time_in_force" not in order


def test_build_buy_order_default_no_tif():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2)
    assert order["ticker"] == "TICKER"
    assert order["yes_price"] == 40
    assert order["count"] == 2
    assert order["action"] == "buy"
    assert "time_in_force" not in order
    assert "expiration_ts" not in order


def test_build_buy_order_with_ioc():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2, time_in_force="immediate_or_cancel")
    assert order["time_in_force"] == "immediate_or_cancel"


def test_build_buy_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_buy_order("TICKER", 0.40, 2, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000


def test_build_close_order_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", 1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert order["action"] == "sell"
    assert order["yes_price"] == 1


def test_build_close_order_negative_with_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", -1, expiration_ts=1716000000)
    assert order["expiration_ts"] == 1716000000
    assert order["action"] == "buy"
    assert order["yes_price"] == 99


def test_build_close_order_default_no_expiration():
    ob = KalshiOrderBuilder()
    order = ob.build_close_order("TICKER", 1)
    assert "expiration_ts" not in order
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_order_builder.py -v`
Expected: FAIL — `build_sell_order()` got unexpected keyword argument `time_in_force`

- [ ] **Step 3: Update `KalshiOrderBuilder`**

Edit `src/exchanges/kalshi/order_builder.py` to accept the new optional params:

```python
class KalshiOrderBuilder:
    def build_sell_order(self, ticker: str, price: float, quantity: int, *,
                         time_in_force: str | None = None,
                         expiration_ts: int | None = None) -> dict:
        order = {
            "ticker": ticker,
            "action": "sell",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }
        if time_in_force:
            order["time_in_force"] = time_in_force
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    def build_buy_order(self, ticker: str, price: float, quantity: int, *,
                        time_in_force: str | None = None,
                        expiration_ts: int | None = None) -> dict:
        order = {
            "ticker": ticker,
            "action": "buy",
            "side": "yes",
            "type": "limit",
            "yes_price": round(price * 100),
            "count": quantity,
        }
        if time_in_force:
            order["time_in_force"] = time_in_force
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    def build_close_order(self, ticker: str, quantity: int, *,
                          expiration_ts: int | None = None) -> dict:
        if quantity < 0:
            order = {
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": abs(quantity),
            }
        else:
            order = {
                "ticker": ticker, "action": "sell", "side": "yes",
                "type": "limit", "yes_price": 1, "count": quantity,
            }
        if expiration_ts:
            order["expiration_ts"] = expiration_ts
        return order

    @staticmethod
    def unwrap_order(raw: dict) -> dict:
        return raw.get("order", raw)
```

- [ ] **Step 4: Update the `OrderBuilder` protocol**

Edit `src/ports/order_builder.py`:

```python
from typing import Protocol


class OrderBuilder(Protocol):
    def build_sell_order(self, ticker: str, price: float, quantity: int, **kwargs) -> dict: ...
    def build_buy_order(self, ticker: str, price: float, quantity: int, **kwargs) -> dict: ...
    def build_close_order(self, ticker: str, quantity: int, **kwargs) -> dict: ...
    def unwrap_order(self, raw: dict) -> dict: ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_order_builder.py -v`
Expected: All 11 tests PASS

- [ ] **Step 6: Run full test suite for regressions**

Run: `python3 -m pytest tests/ -v --timeout=30`
Expected: All existing tests still PASS. The new kwargs are keyword-only with defaults, so existing callers are unaffected.

- [ ] **Step 7: Commit**

```bash
git add tests/test_order_builder.py src/exchanges/kalshi/order_builder.py src/ports/order_builder.py
git commit -m "feat: add time_in_force and expiration_ts to order builder"
```

---

### Task 2: Config — Add `maker_order_ttl_secs`

**Files:**
- Modify: `src/config.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for new config field**

Add to `tests/test_config.py`:

```python
def test_maker_order_ttl_secs_default():
    """maker_order_ttl_secs defaults to 300 when not specified."""
    cfg = load_config("config.yaml")
    assert cfg.maker_order_ttl_secs == 300


def test_maker_order_ttl_secs_override(tmp_path):
    """maker_order_ttl_secs can be overridden in config."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: test
    private_key_path: /dev/null
strategy:
  risk_mode: conservative
  maker_order_ttl_secs: 600
""")
    cfg = load_config(str(config_file))
    assert cfg.maker_order_ttl_secs == 600
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py::test_maker_order_ttl_secs_default tests/test_config.py::test_maker_order_ttl_secs_override -v`
Expected: FAIL — `Config` has no attribute `maker_order_ttl_secs`

- [ ] **Step 3: Add field to Config dataclass and load_config**

Edit `src/config.py`. Add field to the `Config` dataclass (after `maker_max_horizon_hours`):

```python
    maker_order_ttl_secs: int
```

Add to the `return Config(...)` call in `load_config` (after the `maker_max_horizon_hours` line):

```python
        maker_order_ttl_secs=int(strategy.get("maker_order_ttl_secs", 300)),
```

- [ ] **Step 4: Update `config.example.yaml`**

Add after the `maker_max_horizon_hours` line:

```yaml
  maker_order_ttl_secs: 300             # How long maker/two-sided resting orders live before exchange auto-cancels (seconds)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All config tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add maker_order_ttl_secs config field (default 300s)"
```

---

### Task 3: Executor — IOC for Taker Orders

**Files:**
- Modify: `src/executor.py`
- Modify: `tests/test_executor.py`

This is the core change. The executor builds all taker orders as IOC, which means after the batch response we know immediately which legs filled. The `_monitor_fills` loop becomes unnecessary for IOC orders.

- [ ] **Step 1: Write failing test — IOC orders include time_in_force**

Add to `tests/test_executor.py`. First, update `_make_executor` so the mock `build_sell_order` accepts `**kwargs` and includes them in the output:

```python
def _make_executor(fill_timeout=0):
    api = MagicMock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "open"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "open"}},
            {"order": {"order_id": "o3", "ticker": "M3", "status": "open"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    order_builder = MagicMock()
    order_builder.unwrap_order = lambda raw: raw.get("order", raw)
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity, **kwargs: {
        "ticker": ticker,
        "action": "sell",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
        **kwargs,
    })
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity, **kwargs: {
        "ticker": ticker,
        "action": "buy",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
        **kwargs,
    })
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(api=api, order_builder=order_builder, positions=positions, fill_timeout_secs=fill_timeout, timeouts=_FAST_TIMEOUTS), api, positions
```

Then add the test:

```python
def test_taker_orders_use_ioc():
    """Taker orders should include time_in_force=immediate_or_cancel."""
    executor, api, _ = _make_executor()
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
    )
    orders = executor.build_orders(signal, quantity=1)
    for order in orders:
        assert order.get("time_in_force") == "immediate_or_cancel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_executor.py::test_taker_orders_use_ioc -v`
Expected: FAIL — `time_in_force` not in order dict

- [ ] **Step 3: Update `build_orders` to pass IOC**

Edit `src/executor.py`, method `build_orders` (around line 90):

```python
    def build_orders(self, signal: TradeSignal, quantity: int) -> list[dict]:
        orders = []
        for i, (ticker, price) in enumerate(signal.legs):
            action = signal.leg_actions[i] if signal.leg_actions else "sell"
            if action == "buy":
                orders.append(self.order_builder.build_buy_order(
                    ticker, price, quantity,
                    time_in_force="immediate_or_cancel"))
            else:
                orders.append(self.order_builder.build_sell_order(
                    ticker, price, quantity,
                    time_in_force="immediate_or_cancel"))
        return orders
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_executor.py::test_taker_orders_use_ioc -v`
Expected: PASS

- [ ] **Step 5: Write test — IOC full fill skips monitor**

Add to `tests/test_executor.py`:

```python
def test_ioc_full_fill_skips_monitor():
    """When all IOC orders fill immediately, _monitor_fills should not be called."""
    executor, api, positions = _make_executor(fill_timeout=30)
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "executed",
                       "yes_price_dollars": "0.40", "fill_count_fp": "1", "side": "yes", "action": "sell"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.35", "fill_count_fp": "1", "side": "yes", "action": "sell"}},
        ]
    })
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))
    # If _monitor_fills were called with 30s timeout, this test would hang.
    # Passing quickly proves it was skipped.
    assert not executor._executing
```

- [ ] **Step 6: Write test — IOC partial fill triggers unwind immediately**

```python
def test_ioc_partial_fill_triggers_unwind():
    """When some IOC orders are cancelled by the exchange, trigger unwind for filled legs."""
    executor, api, positions = _make_executor(fill_timeout=30)
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "executed",
                       "yes_price_dollars": "0.40", "fill_count_fp": "1", "side": "yes", "action": "sell"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "cancelled",
                       "yes_price_dollars": "0.35", "fill_count_fp": "0", "side": "yes", "action": "sell"}},
        ]
    })
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))
    assert executor.is_event_blacklisted("E1")
```

- [ ] **Step 7: Write test — IOC zero fills is clean no-op**

```python
def test_ioc_zero_fills_clean():
    """When all IOC orders are cancelled (arb gone), no unwind needed."""
    executor, api, positions = _make_executor(fill_timeout=30)
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "cancelled",
                       "yes_price_dollars": "0.40", "fill_count_fp": "0", "side": "yes", "action": "sell"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "cancelled",
                       "yes_price_dollars": "0.35", "fill_count_fp": "0", "side": "yes", "action": "sell"}},
        ]
    })
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))
    assert not executor.is_event_blacklisted("E1")
    assert not executor._executing
```

- [ ] **Step 8: Run new tests to verify they fail**

Run: `python3 -m pytest tests/test_executor.py::test_ioc_full_fill_skips_monitor tests/test_executor.py::test_ioc_partial_fill_triggers_unwind tests/test_executor.py::test_ioc_zero_fills_clean -v`
Expected: Some may PASS already (full fill case), others will FAIL because the executor still calls `_monitor_fills` for non-fully-filled IOC orders and doesn't handle the "cancelled" status.

- [ ] **Step 9: Update `execute()` batch path to handle IOC responses**

Edit `src/executor.py`, in the `execute()` method. After processing the batch response and recording fills (after the `for i, o in enumerate(order_list)` loop, around line 167), replace the buy-side resting check and the `_monitor_fills` call with IOC-aware logic:

Replace everything from the `is_buy_side` check (line 168) through `await self._monitor_fills(execution)` (line 188) with:

```python
            # IOC: all orders are either executed or cancelled — no resting state
            filled_count = len(execution.filled)
            total_count = len(execution.order_ids)

            if filled_count == total_count:
                logger.info("All %d IOC legs filled for %s", total_count, signal.event_ticker)
            elif filled_count > 0:
                cancelled_count = total_count - filled_count
                logger.error(
                    "PARTIAL IOC FILL on %s: %d/%d legs filled, %d cancelled by exchange — UNHEDGED EXPOSURE",
                    signal.event_ticker, filled_count, total_count, cancelled_count,
                )
                self._failed_events.add(signal.event_ticker)
                if self.recorder:
                    self.recorder.record_execution(
                        event_ticker=signal.event_ticker,
                        strategy=signal.signal_type,
                        legs=[{
                            "ticker": t,
                            "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                            "price": p,
                            "quantity": quantity,
                        } for i, (t, p) in enumerate(signal.legs)],
                        result="partial_fill",
                        fill_details={oid: price for oid, price in execution.filled.items()},
                        unwind_cost=0.0,
                    )
                self._executing = False
                self._active = None
                self._launch_unwind(execution)
                return
            else:
                logger.info("All IOC legs cancelled for %s — arb opportunity gone", signal.event_ticker)
```

Also remove the now-dead `execution._needs_unwind` check block that follows (lines 190-208), since IOC orders never go through `_monitor_fills`.

- [ ] **Step 10: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: All executor tests PASS (both new IOC tests and existing tests)

- [ ] **Step 11: Update `_execute_unwind_phase` to add `expiration_ts`**

Edit `src/executor.py`, method `_execute_unwind_phase` (around line 279). Add `expiration_ts` to the unwind order:

```python
    async def _execute_unwind_phase(self, ticker: str, price_cents: int, qty: int,
                                    prev_oid: str | None, action: str = "buy") -> tuple[bool, float, str]:
        try:
            if prev_oid:
                await asyncio.wait_for(self.api.cancel_order(prev_oid), timeout=self._timeouts.batch_cancel)
        except (asyncio.TimeoutError, Exception):
            logger.warning("Failed to cancel previous unwind order %s — proceeding", prev_oid)
        build = self.order_builder.build_buy_order if action == "buy" else self.order_builder.build_sell_order
        order = [build(ticker, price_cents / 100, qty, expiration_ts=int(time.time()) + 60)]
        resp = await asyncio.wait_for(self.api.batch_create_orders(order), timeout=self._timeouts.batch_create)
        inner = self.order_builder.unwrap_order(resp.get("orders", [{}])[0])
        status = inner.get("status", "")
        unwind_price = float(inner.get("yes_price_dollars", 0))
        oid = inner.get("order_id", "")
        if oid:
            self._track_fill_id(oid)
        return status == "executed", unwind_price, oid
```

- [ ] **Step 12: Run full test suite**

Run: `python3 -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 13: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "feat: IOC for taker orders, skip monitor_fills, expiration_ts on unwinds"
```

---

### Task 4: Maker Strategy — TTL on Resting Orders, IOC on Completion

**Files:**
- Modify: `src/strategies/maker.py`
- Modify: `tests/test_maker.py`

- [ ] **Step 1: Write failing test — maker post includes expiration_ts**

Add to `tests/test_maker.py`. First update `_make_maker` to accept `order_ttl_secs` and update the mock to accept `**kwargs`:

```python
def _make_maker(max_events=3, fill_mode="cancel_and_take", order_ttl_secs=300):
    api = MagicMock()
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
    order_builder = MagicMock()
    order_builder.unwrap_order = MagicMock(side_effect=lambda raw: raw.get("order", raw))
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, price, quantity, **kwargs: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(price * 100), "count": quantity,
        **kwargs,
    })
    maker = MakerManager(api=api, order_builder=order_builder, fill_mode=fill_mode,
                         max_events=max_events, maker_order_ttl_secs=order_ttl_secs)
    return maker, api, order_builder
```

Then add the test:

```python
def test_post_includes_expiration_ts():
    """Maker orders should include expiration_ts based on TTL."""
    maker, api, order_builder = _make_maker(order_ttl_secs=300)
    signal = _maker_signal()
    import time
    before = int(time.time())
    asyncio.run(maker.post(signal))
    after = int(time.time())

    calls = order_builder.build_sell_order.call_args_list
    for call in calls:
        kwargs = call.kwargs if call.kwargs else {}
        # Positional kwargs via **kwargs in the side_effect
        exp_ts = kwargs.get("expiration_ts")
        assert exp_ts is not None, "expiration_ts should be set on maker orders"
        assert before + 300 <= exp_ts <= after + 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_maker.py::test_post_includes_expiration_ts -v`
Expected: FAIL — `MakerManager.__init__()` got unexpected keyword argument `maker_order_ttl_secs`

- [ ] **Step 3: Update `MakerManager.__init__` and `post`**

Edit `src/strategies/maker.py`.

Add `maker_order_ttl_secs: int = 300` to `__init__` params (after `exchange_name`):

```python
    def __init__(self, api, order_builder=None, fill_mode: str = "cancel_and_take",
                 max_events: int = 3, risk_profile=None,
                 tighten_phase1_secs: int = 15, tighten_phase2_secs: int = 30,
                 tighten_step_cents: int = 3, track_fill_id=None,
                 capital_guard=None, exchange_name: str = "kalshi",
                 maker_order_ttl_secs: int = 300):
```

Store it: `self._order_ttl_secs = maker_order_ttl_secs` (add after `self._exchange_name = exchange_name`).

Update `post()` — change the order-building loop (around line 98):

```python
                exp_ts = int(time.time()) + self._order_ttl_secs
                orders = [
                    self.order_builder.build_sell_order(ticker, price, 1,
                                                       expiration_ts=exp_ts)
                    for ticker, price in signal.legs
                ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_maker.py::test_post_includes_expiration_ts -v`
Expected: PASS

- [ ] **Step 5: Write test — reprice includes fresh expiration_ts**

```python
def test_reprice_includes_fresh_expiration_ts():
    """Repriced maker orders should get a fresh expiration_ts."""
    maker, api, order_builder = _make_maker(order_ttl_secs=300)
    signal = _maker_signal()
    asyncio.run(maker.post(signal))

    order_builder.build_sell_order.reset_mock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "mo3", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.54"}},
        ]
    })

    books = {
        "M1": Orderbook(bids=[(0.54, 5)], asks=[(0.56, 5)]),
        "M2": Orderbook(bids=[(0.51, 5)], asks=[(0.53, 5)]),
    }
    import time
    maker._active["E1"].last_reprice_time = 0  # force reprice
    before = int(time.time())
    asyncio.run(maker.on_orderbook_update("E1", books))
    after = int(time.time())

    if order_builder.build_sell_order.called:
        for call in order_builder.build_sell_order.call_args_list:
            kwargs = call.kwargs if call.kwargs else {}
            exp_ts = kwargs.get("expiration_ts")
            assert exp_ts is not None
            assert before + 300 <= exp_ts <= after + 300
```

- [ ] **Step 6: Update `on_orderbook_update` to pass expiration_ts**

Edit `src/strategies/maker.py`, method `on_orderbook_update` (around line 340). Change the `build_sell_order` call:

```python
                    exp_ts = int(time.time()) + self._order_ttl_secs
                    new_order = [self.order_builder.build_sell_order(ticker, new_price, 1,
                                                                    expiration_ts=exp_ts)]
```

- [ ] **Step 7: Write test — tighten/complete orders use IOC**

```python
def test_complete_cancel_and_take_uses_ioc():
    """When completing an arb after a fill, the taker orders should use IOC."""
    maker, api, order_builder = _make_maker()
    signal = _maker_signal()
    asyncio.run(maker.post(signal))

    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "to1", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.51"}},
        ]
    })

    order_builder.build_sell_order.reset_mock()
    asyncio.run(maker.handle_fill("mo1", "M1", 0.52, 1))

    if order_builder.build_sell_order.called:
        for call in order_builder.build_sell_order.call_args_list:
            kwargs = call.kwargs if call.kwargs else {}
            tif = kwargs.get("time_in_force")
            assert tif == "immediate_or_cancel", f"Completion taker orders should be IOC, got {tif}"
```

- [ ] **Step 8: Update `_complete_cancel_and_take` and `_tighten_unfilled` to use IOC**

Edit `src/strategies/maker.py`.

In `_complete_cancel_and_take` (around line 238), change the taker order build:

```python
            taker_orders = [
                self.order_builder.build_sell_order(t, p, 1,
                                                   time_in_force="immediate_or_cancel")
                for t, p in unfilled_tickers
            ]
```

In `_tighten_unfilled` (around line 261), change the tighten order build:

```python
            new_order = [self.order_builder.build_sell_order(ticker, new_price, 1,
                                                            time_in_force="immediate_or_cancel")]
```

- [ ] **Step 9: Run all maker tests**

Run: `python3 -m pytest tests/test_maker.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add src/strategies/maker.py tests/test_maker.py
git commit -m "feat: expiration_ts on maker orders, IOC on completion/tighten"
```

---

### Task 5: Two-Sided Strategy — TTL on Resting Orders

**Files:**
- Modify: `src/strategies/two_sided.py`
- Modify: `tests/test_two_sided.py`

- [ ] **Step 1: Write failing test — two-sided post includes expiration_ts**

Add to `tests/test_two_sided.py`. First check how the existing test helper works and update to accept `order_ttl_secs` and `**kwargs`:

Read the existing `_make_two_sided` helper in `tests/test_two_sided.py` to match its pattern, then add:

```python
def test_post_includes_expiration_ts():
    """Two-sided orders should include expiration_ts."""
    # Build a TwoSidedManager with order_ttl_secs=300
    # (update the constructor call in _make_two_sided or create directly)
    import time
    from src.core.risk import load_risk_profile
    api = MagicMock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "b1", "ticker": "M1"}},
            {"order": {"order_id": "s1", "ticker": "M1"}},
        ]
    })
    order_builder = MagicMock()
    order_builder.build_buy_order = MagicMock(side_effect=lambda ticker, price, quantity, **kwargs: {
        "ticker": ticker, "action": "buy", **kwargs,
    })
    order_builder.build_sell_order = MagicMock(side_effect=lambda ticker, price, quantity, **kwargs: {
        "ticker": ticker, "action": "sell", **kwargs,
    })
    rp = load_risk_profile("aggressive", {"two_sided_max_inventory": 10})
    ts = TwoSidedManager(api=api, risk_profile=rp, order_builder=order_builder,
                         maker_order_ttl_secs=300)

    signal = TradeSignal(
        event_ticker="M1",
        legs=[("M1", 0.40), ("M1", 0.46)],
        net_profit=0.02, profit_pct=2.0, exposure_ratio=1.0,
        quantity=1,
    )
    before = int(time.time())
    asyncio.run(ts.post(signal))
    after = int(time.time())

    for call in order_builder.build_buy_order.call_args_list + order_builder.build_sell_order.call_args_list:
        kwargs = call.kwargs if call.kwargs else {}
        exp_ts = kwargs.get("expiration_ts")
        assert exp_ts is not None
        assert before + 300 <= exp_ts <= after + 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_two_sided.py::test_post_includes_expiration_ts -v`
Expected: FAIL — `TwoSidedManager.__init__()` got unexpected keyword argument `maker_order_ttl_secs`

- [ ] **Step 3: Update `TwoSidedManager.__init__` and `post`**

Edit `src/strategies/two_sided.py`.

Add `maker_order_ttl_secs: int = 300` to `__init__` (after `exchange_name`):

```python
    def __init__(self, api, risk_profile: RiskProfile, order_builder=None,
                 capital_guard=None, exchange_name: str = "kalshi",
                 maker_order_ttl_secs: int = 300):
```

Store it: `self._order_ttl_secs = maker_order_ttl_secs` (add after `self._exchange_name = exchange_name`).

Update `post()` — change the order-building (around line 45):

```python
        exp_ts = int(time.time()) + self._order_ttl_secs
        buy_leg, sell_leg = signal.legs
        orders = [
            self.order_builder.build_buy_order(buy_leg[0], buy_leg[1], quantity,
                                               expiration_ts=exp_ts),
            self.order_builder.build_sell_order(sell_leg[0], sell_leg[1], quantity,
                                                expiration_ts=exp_ts),
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_two_sided.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/strategies/two_sided.py tests/test_two_sided.py
git commit -m "feat: expiration_ts on two-sided resting orders"
```

---

### Task 6: Main — Wire Config, Boot Close TTL

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Thread `maker_order_ttl_secs` to MakerManager**

Edit `src/main.py`. In the `MakerManager(...)` constructor call (around line 87), add:

```python
            maker_order_ttl_secs=self.cfg.maker_order_ttl_secs,
```

After the `exchange_name=self.cfg.exchange,` line.

- [ ] **Step 2: Thread `maker_order_ttl_secs` to TwoSidedManager**

Edit `src/main.py`. In the `TwoSidedManager(...)` constructor call (around line 97), add:

```python
            maker_order_ttl_secs=self.cfg.maker_order_ttl_secs,
```

After the `exchange_name=self.cfg.exchange,` line.

- [ ] **Step 3: Add expiration_ts to boot close orders**

Edit `src/main.py`. In `_boot_reconcile_inner`, find the close order line (around line 521):

Change:
```python
                    order = self.order_builder.build_close_order(ticker, -1)
```
To:
```python
                    order = self.order_builder.build_close_order(ticker, -1,
                                                                 expiration_ts=int(time.time()) + 60)
```

- [ ] **Step 4: Add expiration_ts to emergency shutdown close orders**

Search `_emergency_shutdown` for any `build_close_order` calls and add `expiration_ts=int(time.time()) + 60` to them as well. If emergency shutdown uses `build_close_order`, update it. If it doesn't (uses a different mechanism), skip this step.

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/main.py
git commit -m "feat: wire maker_order_ttl_secs config, expiration_ts on boot close orders"
```

---

### Task 7: Integration Smoke Test

**Files:**
- Modify: `tests/test_integration.py` (or create a focused test)

- [ ] **Step 1: Write integration test — full signal-to-execution with IOC**

This test verifies the full pipeline: a signal is generated, passed to the executor, built with IOC, and handled correctly when the exchange returns a mix of filled and cancelled orders.

Add to `tests/test_integration.py` (or the appropriate integration test file):

```python
def test_ioc_taker_signal_to_execution():
    """Full pipeline: signal → executor builds IOC orders → handles mixed fill/cancel response."""
    from unittest.mock import AsyncMock, MagicMock
    from src.executor import ExecutionManager, TimeoutConfig
    from src.exchanges.kalshi.order_builder import KalshiOrderBuilder
    from src.core.models import TradeSignal
    from src.core.positions import PositionTracker

    order_builder = KalshiOrderBuilder()
    api = MagicMock()
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "executed",
                       "yes_price_dollars": "0.40", "fill_count_fp": "1",
                       "side": "yes", "action": "sell", "ticker": "M1"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.35", "fill_count_fp": "1",
                       "side": "yes", "action": "sell", "ticker": "M2"}},
            {"order": {"order_id": "o3", "ticker": "M3", "status": "executed",
                       "yes_price_dollars": "0.33", "fill_count_fp": "1",
                       "side": "yes", "action": "sell", "ticker": "M3"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    positions = PositionTracker()
    executor = ExecutionManager(
        api=api, order_builder=order_builder, positions=positions,
        fill_timeout_secs=30,
        timeouts=TimeoutConfig(batch_create=5, batch_cancel=5, balance=5, monitor_poll=0.01),
    )

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.33)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )

    orders = executor.build_orders(signal, quantity=1)
    # Verify IOC is set on all orders
    for order in orders:
        assert order["time_in_force"] == "immediate_or_cancel"

    import asyncio
    asyncio.run(executor.execute(signal, quantity=1))
    # Should complete instantly (no monitor_fills), all legs filled
    assert not executor.is_event_blacklisted("E1")
    assert not executor._executing
```

- [ ] **Step 2: Run integration test**

Run: `python3 -m pytest tests/test_integration.py::test_ioc_taker_signal_to_execution -v`
Expected: PASS

- [ ] **Step 3: Run full test suite one final time**

Run: `python3 -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test for IOC taker execution pipeline"
```
