# Buy-Side Partial Fill Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent catastrophic losses from buy-side arb partial fills by (1) detecting resting legs immediately, (2) unwinding at graduated prices instead of panic-dumping, and (3) making emergency shutdown resilient to rate limiting.

**Architecture:** Three independent fixes in `src/executor.py` and `src/main.py`. Fix 1 adds early resting-leg detection in `execute()`. Fix 2 replaces the 3-phase unwind with a 5-phase graduated descent in `_unwind_partial_fill()`. Fix 3 wraps `_emergency_shutdown()` in a retry loop with split cancel/close operations.

**Tech Stack:** Python asyncio, pytest, unittest.mock

---

### Task 1: Graduated Unwind Pricing (5-Phase)

**Files:**
- Modify: `src/executor.py:197-214` (the phase definitions in `_unwind_partial_fill`)
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write failing test — sell-side unwind uses 5 graduated phases**

Add to `tests/test_executor.py`:

```python
def test_unwind_sell_side_graduated_phases():
    """Sell-side arb partial fill: one leg filled as a sell, unwind by buying back.
    Should try 5 graduated prices before reaching the $0.99 ceiling."""
    executor, api, positions = _make_executor_with_profile(fill_timeout=1)
    # Phase 1-3 resting, phase 4 fills
    unwind_responses = [
        {"orders": [{"order": {"order_id": f"u{i}", "ticker": "M2",
                                "status": "resting", "yes_price_dollars": "0.50",
                                "fill_count_fp": "0.00", "action": "buy", "side": "yes"}}]}
        for i in range(4)
    ]
    unwind_responses.append(
        {"orders": [{"order": {"order_id": "u4", "ticker": "M2",
                                "status": "executed", "yes_price_dollars": "0.99",
                                "fill_count_fp": "1.00", "action": "buy", "side": "yes"}}]}
    )
    api.batch_create_orders = AsyncMock(side_effect=[
        # Original arb batch
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.46", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.99", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
    ] + unwind_responses)

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # 1 original + 5 unwind phases = 6 total batch_create_orders calls
    assert api.batch_create_orders.call_count == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_executor.py::test_unwind_sell_side_graduated_phases -v`
Expected: FAIL — currently only 3 unwind phases, so call_count == 4 not 6.

- [ ] **Step 3: Write failing test — buy-side unwind uses graduated prices, not $0.01 dump**

Add to `tests/test_executor.py`:

```python
def test_unwind_buy_side_graduated_prices():
    """Buy-side arb partial fill: one leg filled as a buy at $0.66, unwind by selling.
    Phase 3 should NOT be $0.01 — it should be fill_price - 4*step."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
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

    # Track sell prices submitted during unwind
    sell_prices_submitted = []
    original_build_sell = api.build_sell_order.side_effect
    def tracking_build_sell(ticker, yes_price, quantity):
        sell_prices_submitted.append(yes_price)
        return original_build_sell(ticker, yes_price, quantity)
    api.build_sell_order.side_effect = tracking_build_sell

    # All unwind phases rest (so we see all 5 prices attempted)
    def make_resting_response(orders):
        return {"orders": [{"order": {"order_id": f"u{len(sell_prices_submitted)}",
                                       "ticker": orders[0]["ticker"],
                                       "status": "resting",
                                       "yes_price_dollars": str(orders[0]["yes_price"] / 100),
                                       "fill_count_fp": "0.00",
                                       "action": "sell", "side": "yes"}}]}

    api.batch_create_orders = AsyncMock(side_effect=[
        # Original arb batch: KIA resting, SAM filled
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "KIA", "status": "resting",
                       "yes_price_dollars": "0.24", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "SAM", "status": "executed",
                       "yes_price_dollars": "0.66", "fill_count_fp": "1.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
        # 5 unwind phase responses (all resting to see all prices)
        make_resting_response([{"ticker": "SAM", "yes_price": 63}]),
        make_resting_response([{"ticker": "SAM", "yes_price": 60}]),
        make_resting_response([{"ticker": "SAM", "yes_price": 54}]),
        make_resting_response([{"ticker": "SAM", "yes_price": 33}]),
        make_resting_response([{"ticker": "SAM", "yes_price": 1}]),
    ])

    executor = ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=1, risk_profile=profile,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("KIA", 0.24), ("SAM", 0.66)],
        net_profit=0.07, profit_pct=7.15, exposure_ratio=0.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # step=0.03, fill=0.66
    # Phase 1: 0.66 - 0.03 = 0.63
    # Phase 2: 0.66 - 0.06 = 0.60
    # Phase 3: 0.66 - 0.12 = 0.54
    # Phase 4: max(0.66 * 0.5, 0.01) = 0.33
    # Phase 5: 0.01
    assert sell_prices_submitted == [0.63, 0.60, 0.54, 0.33, 0.01]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python3 -m pytest tests/test_executor.py::test_unwind_buy_side_graduated_prices -v`
Expected: FAIL — currently only 3 phases and phase 3 is $0.01.

- [ ] **Step 5: Implement graduated 5-phase unwind**

In `src/executor.py`, replace lines 197-214 in `_unwind_partial_fill` (the phase definitions inside the for loop) with:

```python
        for ticker, fill_price, qty, unwind_action in filled_legs:
            if qty <= 0:
                continue
            logger.warning("Unwinding %d contracts of %s (filled @ %.2f)", qty, ticker, fill_price)
            phase2_wait = self._unwind_phase2_secs - self._unwind_phase1_secs
            if unwind_action == "buy":  # closing a short (original leg was a sell)
                phases = [
                    (lambda fp, s=step: min(fp + s, 0.99), 0),
                    (lambda fp, s=step: min(fp + 2 * s, 0.99), self._unwind_phase1_secs),
                    (lambda fp, s=step: min(fp + 4 * s, 0.99), phase2_wait),
                    (lambda fp: min(fp + (1.0 - fp) * 0.5, 0.99), self._unwind_phase2_secs),
                    (lambda fp: 0.99, self._unwind_phase2_secs),
                ]
                fallback = 0.99
            else:  # closing a long (original leg was a buy)
                phases = [
                    (lambda fp, s=step: max(fp - s, 0.01), 0),
                    (lambda fp, s=step: max(fp - 2 * s, 0.01), self._unwind_phase1_secs),
                    (lambda fp, s=step: max(fp - 4 * s, 0.01), phase2_wait),
                    (lambda fp: max(fp * 0.5, 0.01), self._unwind_phase2_secs),
                    (lambda fp: 0.01, self._unwind_phase2_secs),
                ]
                fallback = 0.01
```

- [ ] **Step 6: Run both new tests to verify they pass**

Run: `python3 -m pytest tests/test_executor.py::test_unwind_sell_side_graduated_phases tests/test_executor.py::test_unwind_buy_side_graduated_prices -v`
Expected: PASS

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: All existing tests still pass. `test_partial_fill_triggers_unwind` may need its assertion updated from `>= 2` to `>= 2` (should still pass since 6 >= 2).

- [ ] **Step 8: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "fix: graduated 5-phase unwind pricing to avoid panic dumps at \$0.01"
```

---

### Task 2: Immediate Cancellation for Resting Buy-Side Legs

**Files:**
- Modify: `src/executor.py:82-117` (inside `execute()`, after batch response processing)
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write failing test — buy-side resting leg triggers immediate cancel + unwind**

Add to `tests/test_executor.py`:

```python
def test_buy_side_resting_leg_immediate_cancel():
    """When a buy-side arb has a resting leg in the batch response,
    cancel immediately and unwind filled legs — don't wait for fill_timeout."""
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
        "unwind_price_step_cents": 3,
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
        # Original batch: KIA resting, SAM filled
        {"orders": [
            {"order": {"order_id": "o1", "ticker": "KIA", "status": "resting",
                       "yes_price_dollars": "0.24", "fill_count_fp": "0.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "SAM", "status": "executed",
                       "yes_price_dollars": "0.66", "fill_count_fp": "1.00",
                       "action": "buy", "side": "yes", "initial_count_fp": "1.00"}},
        ]},
        # Unwind responses (5 phases, first one fills)
        {"orders": [{"order": {"order_id": "u1", "ticker": "SAM",
                                "status": "executed", "yes_price_dollars": "0.63",
                                "fill_count_fp": "1.00", "action": "sell", "side": "yes"}}]},
    ])

    executor = ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=60,  # Long timeout — should NOT be reached
        risk_profile=profile,
    )
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("KIA", 0.24), ("SAM", 0.66)],
        net_profit=0.07, profit_pct=7.15, exposure_ratio=0.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )

    import time
    start = time.time()
    asyncio.run(executor.execute(signal, quantity=1))
    elapsed = time.time() - start

    # Should complete in well under fill_timeout_secs (60s)
    assert elapsed < 5.0, f"Took {elapsed:.1f}s — should have cancelled immediately, not waited for timeout"
    # Resting leg should have been cancelled
    api.batch_cancel_orders.assert_called_once_with(["o1"])
    # Event should be blacklisted
    assert executor.is_event_blacklisted("E1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_executor.py::test_buy_side_resting_leg_immediate_cancel -v`
Expected: FAIL — currently waits for `fill_timeout_secs` (60s) before cancelling.

- [ ] **Step 3: Implement immediate resting-leg detection in execute()**

In `src/executor.py`, in the `execute()` method, after the for loop that processes batch response fills (after line 116, before `await self._monitor_fills(execution)`), add:

```python
            # Buy-side arbs: if any leg is resting, cancel immediately and unwind
            is_buy_side = signal.leg_actions and all(a == "buy" for a in signal.leg_actions)
            if is_buy_side and len(execution.filled) < len(execution.order_ids):
                resting_ids = [oid for oid in execution.order_ids if oid not in execution.filled]
                if resting_ids:
                    logger.warning(
                        "Buy-side resting legs detected for %s: %d/%d filled, cancelling %d immediately",
                        signal.event_ticker, len(execution.filled), len(execution.order_ids), len(resting_ids),
                    )
                    await self.api.batch_cancel_orders(resting_ids)
                    if execution.filled:
                        logger.error(
                            "PARTIAL FILL on %s: %d legs filled, %d cancelled — UNHEDGED EXPOSURE",
                            signal.event_ticker, len(execution.filled), len(resting_ids),
                        )
                        self._failed_events.add(signal.event_ticker)
                        await self._unwind_partial_fill(execution)
                    return

            await self._monitor_fills(execution)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python3 -m pytest tests/test_executor.py::test_buy_side_resting_leg_immediate_cancel -v`
Expected: PASS

- [ ] **Step 5: Write test — sell-side arb still uses normal fill monitoring**

Add to `tests/test_executor.py`:

```python
def test_sell_side_resting_still_waits_for_fills():
    """Sell-side arbs with resting legs should still use _monitor_fills,
    not the immediate cancel path (resting is expected for sell-side)."""
    executor, api, positions = _make_executor_with_profile(fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
        # No leg_actions = defaults to sell
    )
    asyncio.run(executor.execute(signal, quantity=1))

    # Should still go through normal flow (timeout-based cancel)
    # The existing test_partial_fill_triggers_unwind already validates this path
    assert executor.is_event_blacklisted("E1")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_executor.py::test_sell_side_resting_still_waits_for_fills -v`
Expected: PASS

- [ ] **Step 7: Run full executor test suite**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "fix: immediately cancel resting buy-side legs instead of waiting for timeout"
```

---

### Task 3: Emergency Shutdown Retry Loop

**Files:**
- Modify: `src/main.py:249-276` (`_emergency_shutdown`)
- Test: `tests/test_main.py`

- [ ] **Step 1: Write failing test — shutdown retries on 429**

Add to `tests/test_main.py`:

```python
import aiohttp

def test_emergency_shutdown_retries_on_rate_limit():
    """Emergency shutdown should retry the full sequence on 429, not give up after one attempt."""
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None

    # First attempt: get_open_orders succeeds, batch_create_orders 429s
    # Second attempt: everything succeeds
    call_count = {"n": 0}
    async def mock_batch_create(orders):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(),
                status=429, message="Too Many Requests",
            )
        return {"orders": []}

    bot.api.get_open_orders = AsyncMock(return_value={"orders": []})
    bot.api.get_positions = AsyncMock(return_value={"market_positions": [
        {"ticker": "T1", "position_fp": "1.00"},
    ]})
    bot.api.batch_create_orders = AsyncMock(side_effect=mock_batch_create)
    bot.api.batch_cancel_orders = AsyncMock(return_value={})
    bot.api.build_close_order = MagicMock(return_value={"ticker": "T1", "action": "buy"})

    asyncio.run(bot._emergency_shutdown())

    # Should have retried — batch_create called twice
    assert call_count["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py::test_emergency_shutdown_retries_on_rate_limit -v`
Expected: FAIL — current code catches the exception and stops, doesn't retry.

- [ ] **Step 3: Write failing test — shutdown splits cancel and close**

Add to `tests/test_main.py`:

```python
def test_emergency_shutdown_cancel_failure_doesnt_block_close():
    """If cancelling orders fails, should still attempt to close positions."""
    bot = _make_bot()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None

    bot.api.get_open_orders = AsyncMock(return_value={"orders": [
        {"order_id": "r1", "status": "resting"},
    ]})
    bot.api.batch_cancel_orders = AsyncMock(side_effect=Exception("cancel failed"))
    bot.api.get_positions = AsyncMock(return_value={"market_positions": [
        {"ticker": "T1", "position_fp": "1.00"},
    ]})
    bot.api.batch_create_orders = AsyncMock(return_value={"orders": []})
    bot.api.build_close_order = MagicMock(return_value={"ticker": "T1", "action": "buy"})

    asyncio.run(bot._emergency_shutdown())

    # Close should still have been attempted despite cancel failure
    bot.api.batch_create_orders.assert_called_once()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py::test_emergency_shutdown_cancel_failure_doesnt_block_close -v`
Expected: FAIL — current code wraps everything in one try block, so cancel failure prevents close.

- [ ] **Step 5: Implement retry loop with split operations**

Replace `_emergency_shutdown` in `src/main.py` (lines 249-276) with:

```python
    async def _emergency_shutdown(self):
        logger.critical(
            "CIRCUIT BREAKER TRIPPED — session loss: $%.4f. Cancelling all orders and closing positions.",
            self.executor.session_realized_loss,
        )
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.maker:
                    await self.maker.cancel_all()
            except Exception:
                logger.warning("Failed to cancel maker orders (attempt %d)", attempt + 1)

            try:
                orders_resp = await self.api.get_open_orders()
                resting = [o for o in orders_resp.get("orders", [])
                           if o.get("status") in ("resting", "pending", "open")]
                if resting:
                    await self.api.batch_cancel_orders([o["order_id"] for o in resting])
                    logger.info("Cancelled %d open orders", len(resting))
            except Exception:
                logger.warning("Failed to cancel open orders (attempt %d)", attempt + 1)

            try:
                positions_resp = await self.api.get_positions()
                close_orders = []
                for mp in positions_resp.get("market_positions", []):
                    qty = int(float(mp.get("position_fp", "0")))
                    if qty != 0:
                        close_orders.append(self.api.build_close_order(mp["ticker"], qty))
                if close_orders:
                    await self.api.batch_create_orders(close_orders)
                    logger.info("Sent %d close orders", len(close_orders))
                else:
                    logger.info("No positions to close")
                return  # success
            except Exception:
                if attempt == max_retries - 1:
                    logger.critical(
                        "Emergency shutdown failed after %d attempts — manual intervention required",
                        max_retries,
                    )
                else:
                    wait = 2 ** attempt + 1
                    logger.warning(
                        "Emergency shutdown attempt %d failed, retrying in %ds",
                        attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
```

- [ ] **Step 6: Run both new tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py::test_emergency_shutdown_retries_on_rate_limit tests/test_main.py::test_emergency_shutdown_cancel_failure_doesnt_block_close -v`
Expected: PASS

- [ ] **Step 7: Run full main test suite**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "fix: emergency shutdown retry loop with split cancel/close operations"
```

---

### Task 4: Full Regression Check

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass, no regressions.

- [ ] **Step 2: Verify no stale config overrides**

Run: `diff <(grep -E '^\s+\w' config.yaml | grep -v '#') <(grep -E '^\s+\w' config.example.yaml | grep -v '#') || true`
Verify: no stale strategy fields in config.yaml that would override new defaults.
