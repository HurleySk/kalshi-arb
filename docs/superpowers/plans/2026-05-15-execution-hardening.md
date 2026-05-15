# Execution Hardening & Strategy Pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate partial-fill losses by disabling structurally flawed strategies and hardening taker execution with depth checks, tighter staleness, and sequential leg execution.

**Architecture:** Three hardening layers added to the existing execution pipeline: (1) tighter staleness threshold in Dispatcher, (2) ask-side depth validation in ArbEngine, (3) sequential leg execution in ExecutionManager. Two strategies disabled by changing risk profile defaults.

**Tech Stack:** Python asyncio, existing KalshiAPI, pytest

---

### Task 1: Disable buy-side arb and two-sided in risk profile presets

**Files:**
- Modify: `src/risk.py` — change preset defaults
- Modify: `tests/test_risk.py` — update preset assertion tests

- [ ] **Step 1: Write failing tests for new preset defaults**

In `tests/test_risk.py`, add tests asserting the new defaults:

```python
def test_all_presets_disable_buy_side_arb():
    for mode in ("conservative", "moderate", "aggressive"):
        profile = load_risk_profile(mode, {})
        assert profile.enable_buy_side_arb is False, f"{mode} should disable buy_side_arb"


def test_all_presets_disable_two_sided():
    for mode in ("conservative", "moderate", "aggressive"):
        profile = load_risk_profile(mode, {})
        assert profile.two_sided_max_inventory == 0, f"{mode} should disable two_sided"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_risk.py::test_all_presets_disable_buy_side_arb tests/test_risk.py::test_all_presets_disable_two_sided -v`
Expected: FAIL — current presets have `enable_buy_side_arb: True` and non-zero `two_sided_max_inventory`.

- [ ] **Step 3: Update preset values in risk.py**

In `src/risk.py`, change all three presets:

Conservative:
```python
"enable_buy_side_arb": False,
"two_sided_max_inventory": 0,
```

Moderate:
```python
"enable_buy_side_arb": False,
"two_sided_max_inventory": 0,
```

Aggressive:
```python
"enable_buy_side_arb": False,
"two_sided_max_inventory": 0,
```

- [ ] **Step 4: Update existing tests that assert old preset values**

In `tests/test_risk.py`, find and update:
- `test_conservative_two_sided_fields` — change `assert profile.two_sided_max_inventory == 10` to `== 0`
- `test_moderate_two_sided_fields` — change `assert profile.two_sided_max_inventory == 25` to `== 0`
- `test_aggressive_two_sided_fields` — change `assert profile.two_sided_max_inventory == 50` to `== 0`

- [ ] **Step 5: Run all risk tests**

Run: `python3 -m pytest tests/test_risk.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/risk.py tests/test_risk.py
git commit -m "fix: disable buy-side arb and two-sided in all risk presets"
```

---

### Task 2: Tighten staleness threshold to 2 seconds

**Files:**
- Modify: `src/dispatch.py:15` — change constant
- Modify: `tests/test_staleness.py:50` — update stale offset to exceed new threshold

- [ ] **Step 1: Write a failing test for the 2s threshold**

In `tests/test_staleness.py`, add:

```python
def test_dispatcher_skips_at_3s_stale():
    """3s-old data should be rejected with the tightened 2s threshold."""
    from src.dispatch import Dispatcher
    from src.executor import ExecutionManager

    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.55", "10"]], "no_dollars_fp": [["0.45", "10"]]})
    mgr._last_update_ts["M1"] = time.time() - 3.0  # 3s old

    engine = MagicMock()
    executor = MagicMock(spec=ExecutionManager)
    executor.is_circuit_breaker_tripped.return_value = False

    dispatcher = Dispatcher(
        engine=engine, executor=executor,
        maker=None, orderbook_mgr=mgr, market_metadata={},
    )
    result = dispatcher.process_orderbook_update("M1")
    assert result is None
    engine.evaluate.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_staleness.py::test_dispatcher_skips_at_3s_stale -v`
Expected: FAIL — 3s is under the current 5s threshold, so the engine WILL be called.

- [ ] **Step 3: Change the staleness constant**

In `src/dispatch.py`, change line 15:

```python
STALE_THRESHOLD_SECS = 2.0
```

- [ ] **Step 4: Update existing staleness test offset**

In `tests/test_staleness.py`, the existing `test_dispatcher_skips_stale_event` sets `time.time() - 10.0`. This still works since 10 > 2. No change needed.

- [ ] **Step 5: Run all staleness tests**

Run: `python3 -m pytest tests/test_staleness.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/dispatch.py tests/test_staleness.py
git commit -m "fix: tighten staleness threshold from 5s to 2s"
```

---

### Task 3: Add ask-side depth check to ArbEngine

**Files:**
- Modify: `src/risk.py` — add `min_ask_depth` field and preset values
- Modify: `src/engine.py:103-158` — add ask-depth validation in `_validate_legs`
- Modify: `tests/test_engine.py` — add ask-depth rejection tests

- [ ] **Step 1: Add `min_ask_depth` to RiskProfile and presets**

In `src/risk.py`, add to `RiskProfile` dataclass after `min_bid_depth`:

```python
min_ask_depth: int = 1
```

In presets, add to each:
- Conservative: `"min_ask_depth": 5,`
- Moderate: `"min_ask_depth": 2,`
- Aggressive: `"min_ask_depth": 1,`

- [ ] **Step 2: Write failing tests for ask-depth rejection**

In `tests/test_engine.py`, add:

```python
def test_ask_depth_rejects_no_asks():
    """Market with bids but no asks (one-sided) should be rejected."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=1)
    orderbooks = {
        "M1": _ob([(0.40, 100)]),              # bids only, no no_bids → no ask
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_ask_depth_rejects_thin_asks():
    """Ask depth below min_ask_depth should reject the signal."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=10)
    orderbooks = {
        # no_bids at 60¢ → YES ask at 40¢ with depth 2 (below min 10)
        "M1": Orderbook(yes_bids={40: 100}, no_bids={60: 2}),
        "M2": Orderbook(yes_bids={35: 100}, no_bids={65: 100}),
        "M3": Orderbook(yes_bids={35: 100}, no_bids={65: 100}),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is None


def test_ask_depth_accepts_sufficient_asks():
    """Signal should pass when ask depth meets the minimum."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_ask_depth=5)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={60: 10}),
        "M2": Orderbook(yes_bids={35: 100}, no_bids={65: 10}),
        "M3": Orderbook(yes_bids={35: 100}, no_bids={65: 10}),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py::test_ask_depth_rejects_no_asks tests/test_engine.py::test_ask_depth_rejects_thin_asks tests/test_engine.py::test_ask_depth_accepts_sufficient_asks -v`
Expected: First two FAIL (signal returned when it shouldn't), third may pass or fail depending on existing behavior.

- [ ] **Step 4: Update `_make_engine` helper to accept `min_ask_depth`**

In `tests/test_engine.py`, update the `_make_engine` function's `RiskProfile` construction to include:

```python
min_ask_depth=kwargs.get("min_ask_depth", 1),
```

- [ ] **Step 5: Wire `min_ask_depth` in ArbEngine and add check to `_validate_legs`**

In `src/engine.py`, add to `__init__` after `self.min_bid_depth`:

```python
self.min_ask_depth = risk_profile.min_ask_depth
```

Add `min_ask_depth` parameter to `_validate_legs` signature (after `min_volume_24h`):

```python
def _validate_legs(
    self,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None = None,
    event_ticker: str | None = None,
    min_bid_depth: int | None = None,
    min_volume_24h: float | None = None,
    min_ask_depth: int | None = None,
    strategy: str = "taker",
) -> list[tuple[str, float, float]] | None:
```

After the bid-depth check block (after the `if effective_min_depth > 1:` block), add:

```python
effective_min_ask_depth = min_ask_depth if min_ask_depth is not None else self.min_ask_depth
if effective_min_ask_depth >= 1:
    for ticker, best_bid, depth in legs:
        book = orderbooks[ticker]
        best_ask = book.best_yes_ask()
        if best_ask is None:
            if near_miss and event_ticker:
                logger.debug(
                    "near-miss %s: bid_sum=%.4f blocked — %s no ask (one-sided market)",
                    event_ticker, bid_sum, ticker,
                )
                self._record_near_miss(event_ticker, strategy, bid_sum, legs, "no_ask")
            return None
        ask_depth = book.yes_ask_depth_at(best_ask)
        if ask_depth < effective_min_ask_depth:
            if near_miss and event_ticker:
                logger.debug(
                    "near-miss %s: bid_sum=%.4f blocked — %s ask_depth %.0f < min %d",
                    event_ticker, bid_sum, ticker, ask_depth, effective_min_ask_depth,
                )
                self._record_near_miss(event_ticker, strategy, bid_sum, legs, "ask_depth_filter")
            return None
```

- [ ] **Step 6: Run all engine tests**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS. Some existing engine tests that pass orderbooks without `no_bids` may now fail because the ask-depth check rejects them. If so, add `no_bids` to those test orderbooks (e.g., `no_bids={60: 100}`) to provide valid ask depth.

- [ ] **Step 8: Commit**

```bash
git add src/risk.py src/engine.py tests/test_engine.py
git commit -m "feat: add ask-side depth check to taker signal validation"
```

---

### Task 4: Add sequential leg execution to ExecutionManager

**Files:**
- Modify: `src/risk.py` — add `sequential_execution` field
- Modify: `src/executor.py:99-231` — add sequential execution path in `execute()`
- Modify: `src/config.py` — add `sequential_execution` config field
- Modify: `src/main.py:56-64` — wire config to executor
- Modify: `tests/test_executor.py` — add sequential execution tests

- [ ] **Step 1: Add `sequential_execution` to RiskProfile**

In `src/risk.py`, add to `RiskProfile` dataclass:

```python
sequential_execution: bool = True
```

In all three presets, add:

```python
"sequential_execution": True,
```

- [ ] **Step 2: Add config field**

In `src/config.py`, add to the `Config` dataclass after `max_contracts_per_arb`:

```python
sequential_execution: bool
```

In `load_config()`, add to the return statement after `max_contracts_per_arb`:

```python
sequential_execution=strategy.get("sequential_execution", True),
```

- [ ] **Step 3: Write failing tests for sequential execution**

In `tests/test_executor.py`, add:

```python
def test_sequential_execution_sends_legs_one_at_a_time():
    """Sequential mode should send one leg per batch call, highest price first."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "sequential_execution": True,
    })
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()

    # All 3 legs fill immediately
    api.batch_create_orders = AsyncMock(side_effect=[
        {"orders": [{"order": {"order_id": "o1", "ticker": "M1", "status": "executed",
                               "yes_price_dollars": "0.50", "fill_count_fp": "1.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]},
        {"orders": [{"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                               "yes_price_dollars": "0.35", "fill_count_fp": "1.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]},
        {"orders": [{"order": {"order_id": "o3", "ticker": "M3", "status": "executed",
                               "yes_price_dollars": "0.30", "fill_count_fp": "1.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]},
    ])

    executor = ExecutionManager(
        api=api, positions=positions, fill_timeout_secs=0,
        risk_profile=profile, timeouts=_FAST_TIMEOUTS,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M2", 0.35), ("M1", 0.50), ("M3", 0.30)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Should have 3 separate batch calls (one per leg)
    assert api.batch_create_orders.call_count == 3
    # First call should be highest price leg (M1 at 0.50)
    first_order = api.batch_create_orders.call_args_list[0][0][0]
    assert len(first_order) == 1
    assert first_order[0]["ticker"] == "M1"
    assert first_order[0]["yes_price"] == 50
    # Second should be M2 at 0.35
    second_order = api.batch_create_orders.call_args_list[1][0][0]
    assert second_order[0]["ticker"] == "M2"
    # Third should be M3 at 0.30
    third_order = api.batch_create_orders.call_args_list[2][0][0]
    assert third_order[0]["ticker"] == "M3"


def test_sequential_execution_aborts_on_resting():
    """If a leg goes resting in sequential mode, cancel it and unwind filled legs."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "sequential_execution": True,
    })
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.build_buy_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "buy", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()

    api.batch_create_orders = AsyncMock(side_effect=[
        # Leg 1 (highest price) fills
        {"orders": [{"order": {"order_id": "o1", "ticker": "M1", "status": "executed",
                               "yes_price_dollars": "0.50", "fill_count_fp": "1.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]},
        # Leg 2 goes resting — abort here
        {"orders": [{"order": {"order_id": "o2", "ticker": "M2", "status": "resting",
                               "yes_price_dollars": "0.35", "fill_count_fp": "0.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]},
        # Unwind of leg 1
        {"orders": [{"order": {"order_id": "u1", "ticker": "M1", "status": "executed",
                               "yes_price_dollars": "0.53", "fill_count_fp": "1.00",
                               "action": "buy", "side": "yes", "initial_count_fp": "1.00"}}]},
    ])

    executor = ExecutionManager(
        api=api, positions=positions, fill_timeout_secs=0,
        risk_profile=profile, timeouts=_FAST_TIMEOUTS,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M2", 0.35), ("M1", 0.50), ("M3", 0.30)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Should have sent 2 leg orders + 1 unwind = 3 batch calls (NOT 3 legs + unwind)
    assert api.batch_create_orders.call_count == 3
    # Resting leg should have been cancelled
    api.batch_cancel_orders.assert_called_with(["o2"])
    # Event should be blacklisted
    assert executor.is_event_blacklisted("E1")


def test_sequential_execution_zero_exposure_on_first_leg_resting():
    """If the first (most expensive) leg goes resting, cancel and return — zero exposure."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "sequential_execution": True,
    })
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    positions = MagicMock()

    api.batch_create_orders = AsyncMock(return_value={
        "orders": [{"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                               "yes_price_dollars": "0.50", "fill_count_fp": "0.00",
                               "action": "sell", "side": "yes", "initial_count_fp": "1.00"}}]
    })

    executor = ExecutionManager(
        api=api, positions=positions, fill_timeout_secs=0,
        risk_profile=profile, timeouts=_FAST_TIMEOUTS,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M2", 0.35), ("M1", 0.50), ("M3", 0.30)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=1.5,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Only 1 batch call (the first leg that rested)
    assert api.batch_create_orders.call_count == 1
    api.batch_cancel_orders.assert_called_with(["o1"])
    # NOT blacklisted — no exposure, no harm done
    assert not executor.is_event_blacklisted("E1")
    positions.record_fill.assert_not_called()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_executor.py::test_sequential_execution_sends_legs_one_at_a_time tests/test_executor.py::test_sequential_execution_aborts_on_resting tests/test_executor.py::test_sequential_execution_zero_exposure_on_first_leg_resting -v`
Expected: FAIL — `sequential_execution` field doesn't exist yet, and executor sends all legs in one batch.

- [ ] **Step 5: Add `sequential_execution` flag to ExecutionManager**

In `src/executor.py`, add to `__init__` after `self._unwind_price_step_cents`:

```python
self._sequential_execution = risk_profile.sequential_execution if risk_profile else False
```

- [ ] **Step 6: Implement sequential execution in `execute()`**

In `src/executor.py`, in the `execute()` method, after `orders = self.build_orders(signal, quantity)` and the `logger.info("Executing arb...")` line, add the sequential path before the existing batch path:

```python
if self._sequential_execution and len(orders) > 1:
    await self._execute_sequential(signal, orders, quantity)
    return
```

Then add the `_execute_sequential` method to `ExecutionManager`:

```python
async def _execute_sequential(self, signal: TradeSignal, orders: list[dict], quantity: int):
    """Execute legs one at a time, highest price first. Abort on first resting order."""
    leg_indices = list(range(len(orders)))
    leg_indices.sort(key=lambda i: orders[i].get("yes_price", 0), reverse=True)

    filled_legs: list[FilledLeg] = []
    filled_oids: list[str] = []

    for idx in leg_indices:
        order = orders[idx]
        try:
            resp = await asyncio.wait_for(
                self.api.batch_create_orders([order]), timeout=self._timeouts.batch_create)
        except (asyncio.TimeoutError, Exception):
            logger.exception("Sequential leg %d timed out for %s — aborting", idx, signal.event_ticker)
            break

        inner = self.api.unwrap_order(resp.get("orders", [{}])[0])
        oid = inner.get("order_id", "")
        status = inner.get("status", "")

        if status == "executed":
            price = float(inner.get("yes_price_dollars", 0))
            qty = int(float(inner.get("fill_count_fp", 0)))
            if oid:
                self._track_fill_id(oid)
            filled_oids.append(oid)
            original_action = (
                signal.leg_actions[idx] if signal.leg_actions and idx < len(signal.leg_actions) else "sell"
            )
            filled_legs.append(FilledLeg(
                ticker=inner.get("ticker", ""),
                fill_price=price,
                quantity=qty,
                unwind_action="sell" if original_action == "buy" else "buy",
            ))
            self.positions.record_fill(
                ticker=inner.get("ticker", ""),
                side=inner.get("side", "yes"),
                price=price,
                quantity=qty,
                action=inner.get("action", "sell"),
            )
            logger.info("Sequential leg %d filled: %s @ %.2f (%d/%d)",
                        idx, inner.get("ticker", ""), price,
                        len(filled_legs), len(orders))
        else:
            # Resting or failed — cancel and abort
            if oid:
                try:
                    await asyncio.wait_for(
                        self.api.batch_cancel_orders([oid]), timeout=self._timeouts.batch_cancel)
                except (asyncio.TimeoutError, Exception):
                    logger.warning("Failed to cancel resting order %s", oid)
            logger.warning(
                "Sequential leg %d resting for %s (%s @ %s) — aborting after %d/%d legs filled",
                idx, signal.event_ticker, inner.get("ticker", ""),
                inner.get("yes_price_dollars", "?"),
                len(filled_legs), len(orders),
            )
            break

    if len(filled_legs) == len(orders):
        logger.info("Sequential execution complete for %s: all %d legs filled", signal.event_ticker, len(orders))
        if self.recorder:
            self.recorder.record_execution(
                event_ticker=signal.event_ticker,
                strategy=signal.signal_type,
                legs=[{"ticker": t, "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                       "price": p, "quantity": quantity} for i, (t, p) in enumerate(signal.legs)],
                result="full_fill",
                fill_details={oid: fl.fill_price for oid, fl in zip(filled_oids, filled_legs)},
                unwind_cost=0.0,
            )
        return

    if filled_legs:
        logger.error(
            "PARTIAL FILL (sequential) on %s: %d/%d legs filled — unwinding",
            signal.event_ticker, len(filled_legs), len(orders),
        )
        self._failed_events.add(signal.event_ticker)
        execution = ArbExecution(signal=signal, order_ids=filled_oids, filled_legs=filled_legs)
        if self.recorder:
            self.recorder.record_execution(
                event_ticker=signal.event_ticker,
                strategy=signal.signal_type,
                legs=[{"ticker": t, "action": (signal.leg_actions[i] if signal.leg_actions else "sell"),
                       "price": p, "quantity": quantity} for i, (t, p) in enumerate(signal.legs)],
                result="partial_fill",
                fill_details={oid: fl.fill_price for oid, fl in zip(filled_oids, filled_legs)},
                unwind_cost=0.0,
            )
        self._launch_unwind(execution)
    # else: no legs filled, nothing to unwind, no blacklist
```

- [ ] **Step 7: Wire config to ExecutionManager in main.py**

In `src/main.py`, the `ExecutionManager` constructor already receives `risk_profile`, which now includes `sequential_execution`. No additional wiring needed — the executor reads it from the profile in `__init__`.

- [ ] **Step 8: Run all executor tests**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: ALL PASS

- [ ] **Step 9: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add src/risk.py src/executor.py src/config.py tests/test_executor.py
git commit -m "feat: add sequential leg execution — highest price first, abort on resting"
```

---

### Task 5: Update config.example.yaml and CLAUDE.md

**Files:**
- Modify: `config.example.yaml`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update config.example.yaml**

Add to the `strategy:` section:

```yaml
  sequential_execution: true           # Execute legs one at a time, highest price first (default: true)
  # staleness_threshold_secs: 2.0      # Orderbook data older than this is rejected (hardcoded in dispatch.py)
```

Update comments for disabled strategies:

```yaml
  # Buy-side structural arb — DISABLED (structural ask-lifting risk, two live losses from partial fills)
  # enable_buy_side_arb: false
```

Update two-sided comment:

```yaml
  # Two-sided market making — DISABLED (untested in live, re-enable once execution layer is proven)
  # two_sided_max_inventory: 0
```

- [ ] **Step 2: Update CLAUDE.md**

In the strategy list, update buy-side and two-sided descriptions to note they're disabled by default.

In the "Execution Fidelity — Defense in Depth" section, add layer 9:

```
9. **Sequential leg execution** — Legs executed one at a time, highest price first. If any leg goes resting, cancel it immediately and unwind only the already-filled legs. Eliminates the worst partial-fill scenario where multiple expensive legs fill before a cheap leg fails.
```

Update the staleness description to reference the 2s threshold instead of 5s.

- [ ] **Step 3: Commit**

```bash
git add config.example.yaml CLAUDE.md
git commit -m "docs: update config and CLAUDE.md for execution hardening changes"
```

---

### Task 6: Run full verification

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (285+ tests)

- [ ] **Step 2: Verify buy-side disabled**

Run: `python3 -c "from src.risk import load_risk_profile; p = load_risk_profile('conservative', {}); print('buy_side:', p.enable_buy_side_arb, 'two_sided:', p.two_sided_max_inventory)"`
Expected: `buy_side: False two_sided: 0`

- [ ] **Step 3: Verify staleness threshold**

Run: `python3 -c "from src.dispatch import Dispatcher; print('staleness:', Dispatcher.STALE_THRESHOLD_SECS)"`
Expected: `staleness: 2.0`

- [ ] **Step 4: Verify sequential execution default**

Run: `python3 -c "from src.risk import load_risk_profile; p = load_risk_profile('conservative', {}); print('sequential:', p.sequential_execution)"`
Expected: `sequential: True`

- [ ] **Step 5: Run dry-run to verify no regressions**

Run: `python3 -m src.dry_run --db data/arb_history.db --ws-race-rate 1.0 --seed 42 --risk-mode aggressive`
Expected: Completes without invariant violations. Signal count may be 0 (buy-side disabled) or low — that's expected.
