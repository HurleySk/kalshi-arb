# Execution Fidelity — Defense in Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all paths where a hanging API call or stale data can freeze the bot or cause unrecovered losses.

**Architecture:** Eight independent layers of timeout protection, from the HTTP transport up to signal-level staleness detection. Each layer is self-contained — any single layer protects against the core failure mode (hanging API call) even if others miss it.

**Tech Stack:** Python asyncio, aiohttp, websockets

**Spec:** `docs/superpowers/specs/2026-05-15-execution-fidelity-design.md`

---

### Task 1: HTTP Transport Timeout (Layer 1)

**Files:**
- Modify: `src/api.py:28-31`
- Test: `tests/test_api_timeout.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_timeout.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

from src.api import KalshiAPI
from src.auth import KalshiAuth


def _make_api():
    auth = MagicMock(spec=KalshiAuth)
    auth.build_headers.return_value = {
        "KALSHI-ACCESS-KEY": "test",
        "KALSHI-ACCESS-TIMESTAMP": "123",
        "KALSHI-ACCESS-SIGNATURE": "sig",
    }
    return KalshiAPI(base_url="https://test.kalshi.com", auth=auth)


def test_session_has_timeout():
    """aiohttp session must have a ClientTimeout configured."""
    api = _make_api()

    async def _run():
        session = await api._ensure_session()
        assert session.timeout is not None
        assert session.timeout.total is not None
        assert session.timeout.total <= 30
        assert session.timeout.connect is not None
        assert session.timeout.sock_read is not None
        await api.close()

    asyncio.run(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_api_timeout.py::test_session_has_timeout -v`
Expected: FAIL — session.timeout.total is None (default aiohttp has no timeout)

- [ ] **Step 3: Add ClientTimeout to session**

In `src/api.py`, add the import and modify `_ensure_session`:

```python
# Add to existing imports at the top of the file
# (aiohttp is already imported)

# In _ensure_session, change:
#     self._session = aiohttp.ClientSession()
# to:
    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_api_timeout.py::test_session_has_timeout -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All 248+ tests pass

- [ ] **Step 6: Commit**

```bash
git add src/api.py tests/test_api_timeout.py
git commit -m "feat: add aiohttp ClientTimeout — 30s total, 10s connect, 15s read"
```

---

### Task 2: Executor API Call Timeouts (Layer 2)

**Files:**
- Modify: `src/executor.py:77-189`
- Test: `tests/test_executor_timeouts.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_executor_timeouts.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.executor import ExecutionManager
from src.models import TradeSignal
from src.positions import PositionTracker
from src.risk import load_risk_profile


def _make_executor(batch_create_side_effect=None, get_balance_side_effect=None):
    profile = load_risk_profile("conservative", {
        "unwind_phase1_secs": 0,
        "unwind_phase2_secs": 0,
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
    if batch_create_side_effect:
        api.batch_create_orders = AsyncMock(side_effect=batch_create_side_effect)
    else:
        api.batch_create_orders = AsyncMock(return_value={"orders": []})
    if get_balance_side_effect:
        api.get_balance = AsyncMock(side_effect=get_balance_side_effect)
    else:
        api.get_balance = AsyncMock(return_value={"balance": 10000})
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = PositionTracker()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=1, risk_profile=profile,
    ), api


def _signal(leg_actions=None):
    return TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.55), ("M2", 0.55)],
        net_profit=0.05, profit_pct=5.0, exposure_ratio=1.5,
        leg_actions=leg_actions,
    )


def test_batch_create_timeout_does_not_hang():
    """If batch_create_orders hangs, execute() must not block forever."""
    async def _hang():
        await asyncio.sleep(999)

    executor, api = _make_executor(batch_create_side_effect=_hang)

    async def _run():
        try:
            await asyncio.wait_for(executor.execute(_signal()), timeout=20)
        except asyncio.TimeoutError:
            raise AssertionError("execute() hung — batch_create_orders has no timeout")

    asyncio.run(_run())


def test_balance_check_timeout_proceeds():
    """If get_balance hangs, buy-side execute() should proceed anyway."""
    async def _hang():
        await asyncio.sleep(999)

    executor, api = _make_executor(get_balance_side_effect=_hang)

    async def _run():
        signal = _signal(leg_actions=["buy", "buy"])
        try:
            await asyncio.wait_for(executor.execute(signal), timeout=15)
        except asyncio.TimeoutError:
            raise AssertionError("execute() hung — get_balance has no timeout")

    asyncio.run(_run())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_executor_timeouts.py -v`
Expected: FAIL — tests hang (timeout after 20s means the batch_create call has no protection)

- [ ] **Step 3: Add timeouts to executor API calls**

In `src/executor.py`, wrap the API calls in `execute()`:

```python
    # In execute(), wrap batch_create_orders (the initial arb order):
    # Change:
    #     response = await self.api.batch_create_orders(orders)
    # To:
            response = await asyncio.wait_for(
                self.api.batch_create_orders(orders), timeout=15)

    # In execute(), wrap get_balance (buy-side pre-flight):
    # Change:
    #     bal = await self.api.get_balance()
    # To:
                bal = await asyncio.wait_for(self.api.get_balance(), timeout=10)

    # In execute(), wrap batch_cancel_orders (buy-side immediate cancel):
    # Change:
    #     await self.api.batch_cancel_orders(resting_ids)
    # To:
                    await asyncio.wait_for(
                        self.api.batch_cancel_orders(resting_ids), timeout=10)

    # In _monitor_fills(), wrap batch_cancel_orders (timeout cancel):
    # Change:
    #     await self.api.batch_cancel_orders(unfilled)
    # To:
            await asyncio.wait_for(
                self.api.batch_cancel_orders(unfilled), timeout=10)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_executor_timeouts.py -v`
Expected: PASS — both tests complete quickly instead of hanging

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/executor.py tests/test_executor_timeouts.py
git commit -m "feat: add timeouts to executor API calls — 15s orders, 10s cancels/balance"
```

---

### Task 3: Emergency Shutdown Timeout (Layer 3)

**Files:**
- Modify: `src/main.py:256-304`
- Test: `tests/test_main.py` (modify — add timeout test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py`:

```python
def test_emergency_shutdown_does_not_hang(mock_config):
    """Emergency shutdown must complete even if API calls hang."""
    bot = ArbBot.__new__(ArbBot)
    bot.cfg = mock_config
    bot.api = MagicMock()
    bot.executor = MagicMock()
    bot.executor.session_realized_loss = 1.0
    bot.maker = None

    async def _hang():
        await asyncio.sleep(999)

    bot.api.get_open_orders = AsyncMock(side_effect=_hang)
    bot.api.get_positions = AsyncMock(side_effect=_hang)
    bot.api.batch_cancel_orders = AsyncMock(side_effect=_hang)

    async def _run():
        try:
            await asyncio.wait_for(bot._emergency_shutdown(), timeout=70)
        except asyncio.TimeoutError:
            raise AssertionError("_emergency_shutdown hung — no overall timeout")

    asyncio.run(_run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py::test_emergency_shutdown_does_not_hang -v`
Expected: FAIL — hangs for 70s then fails with AssertionError

- [ ] **Step 3: Add timeouts to emergency shutdown**

In `src/main.py`, wrap each API call in `_emergency_shutdown()` with individual timeouts, and wrap the overall method:

```python
    async def _emergency_shutdown(self):
        logger.critical(
            "CIRCUIT BREAKER TRIPPED — session loss: $%.4f. Cancelling all orders and closing positions.",
            self.executor.session_realized_loss,
        )
        try:
            await asyncio.wait_for(self._emergency_shutdown_inner(), timeout=60)
        except asyncio.TimeoutError:
            logger.critical("Emergency shutdown timed out after 60s — manual intervention required")

    async def _emergency_shutdown_inner(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.maker:
                    await asyncio.wait_for(self.maker.cancel_all(), timeout=15)
            except (asyncio.TimeoutError, Exception):
                logger.warning("Failed to cancel maker orders (attempt %d)", attempt + 1)

            try:
                orders_resp = await asyncio.wait_for(
                    self.api.get_open_orders(), timeout=15)
                resting = [o for o in orders_resp.get("orders", [])
                           if o.get("status") in ("resting", "pending", "open")]
                if resting:
                    await asyncio.wait_for(
                        self.api.batch_cancel_orders([o["order_id"] for o in resting]),
                        timeout=15)
                    logger.info("Cancelled %d open orders", len(resting))
            except (asyncio.TimeoutError, Exception):
                logger.warning("Failed to cancel open orders (attempt %d)", attempt + 1)

            try:
                positions_resp = await asyncio.wait_for(
                    self.api.get_positions(), timeout=15)
                close_orders = []
                for mp in positions_resp.get("market_positions", []):
                    qty = int(float(mp.get("position_fp", "0")))
                    if qty != 0:
                        close_orders.append(self.api.build_close_order(mp["ticker"], qty))
                if close_orders:
                    await asyncio.wait_for(
                        self.api.batch_create_orders(close_orders), timeout=15)
                    logger.info("Sent %d close orders", len(close_orders))
                else:
                    logger.info("No positions to close")
                return
            except (asyncio.TimeoutError, Exception):
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_main.py::test_emergency_shutdown_does_not_hang -v`
Expected: PASS — completes in ~1-2s, not 70s

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: add 60s overall timeout and per-call 15s timeouts to emergency shutdown"
```

---

### Task 4: Boot Reconcile Timeout (Layer 4)

**Files:**
- Modify: `src/main.py:353-406`

- [ ] **Step 1: Add timeout wrapper to _boot_reconcile**

Wrap the existing `_boot_reconcile` internals:

```python
    async def _boot_reconcile(self):
        """Cancel orphaned resting orders, load longs, close shorts."""
        try:
            await asyncio.wait_for(self._boot_reconcile_inner(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Boot reconciliation timed out after 60s — proceeding without full reconcile")

    async def _boot_reconcile_inner(self):
        # ... existing _boot_reconcile body unchanged ...
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add 60s timeout to boot reconciliation"
```

---

### Task 5: Recent Trades Timeout (Layer 8)

**Files:**
- Modify: `src/main.py:172-184`

- [ ] **Step 1: Add timeout to _validate_recent_trades**

```python
    async def _validate_recent_trades(self, tickers: list[str]) -> bool:
        if not self.risk_profile.require_recent_trades:
            return True
        for ticker in tickers:
            try:
                resp = await asyncio.wait_for(
                    self.api.get_market_trades(ticker), timeout=10)
                if not resp.get("trades"):
                    logger.info("No recent trades for %s, skipping arb", ticker)
                    return False
            except asyncio.TimeoutError:
                logger.warning("Recent trades check timed out for %s — treating as no trades", ticker)
                return False
            except Exception:
                logger.exception("Failed to check recent trades for %s", ticker)
                return False
        return True
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add 10s timeout to recent trades validation"
```

---

### Code Review Checkpoint 1

After Tasks 1-5, dispatch `superpowers:code-reviewer` to review all timeout changes before proceeding to the WebSocket and staleness layers.

**Base SHA:** commit before Task 1
**Head SHA:** HEAD after Task 5 commit
**Description:** HTTP transport timeout, executor API call timeouts, emergency shutdown timeout, boot reconcile timeout, recent trades timeout

---

### Task 6: Orderbook Staleness Detection (Layer 6)

**Files:**
- Modify: `src/scanner.py:15-80` (OrderbookManager)
- Modify: `src/dispatch.py:67-80`
- Test: `tests/test_staleness.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_staleness.py
import time
from unittest.mock import MagicMock

from src.scanner import OrderbookManager
from src.models import Orderbook


def test_market_age_returns_seconds_since_update():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    mgr.apply_snapshot("M1", {"yes": [[55, 10]], "no": [[45, 10]]})
    age = mgr.market_age("M1")
    assert age < 1.0, f"Freshly updated market should be <1s old, got {age}"


def test_market_age_returns_inf_for_never_updated():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])
    age = mgr.market_age("M1")
    assert age == float("inf"), "Never-updated market should have infinite age"


def test_market_age_returns_inf_for_unknown():
    mgr = OrderbookManager()
    age = mgr.market_age("UNKNOWN")
    assert age == float("inf")


def test_apply_delta_updates_timestamp():
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1"])
    mgr.apply_snapshot("M1", {"yes": [[55, 10]], "no": [[45, 10]]})
    age1 = mgr.market_age("M1")

    import time
    time.sleep(0.05)
    mgr.apply_delta("M1", {"price": 56, "delta": 5, "side": "yes"})
    age2 = mgr.market_age("M1")
    assert age2 < age1, "Delta should refresh the timestamp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_staleness.py -v`
Expected: FAIL — `OrderbookManager` has no `market_age` method

- [ ] **Step 3: Add staleness tracking to OrderbookManager**

In `src/scanner.py`, modify `OrderbookManager`:

```python
class OrderbookManager:
    def __init__(self):
        self._event_markets: dict[str, list[str]] = {}
        self._market_event: dict[str, str] = {}
        self._books: dict[str, Orderbook] = {}
        self._last_update_ts: dict[str, float] = {}

    # In apply_snapshot, after updating the book:
    def apply_snapshot(self, ticker: str, snapshot: dict):
        # ... existing snapshot logic ...
        self._last_update_ts[ticker] = time.time()

    # In apply_delta, after updating the book:
    def apply_delta(self, ticker: str, delta: dict):
        # ... existing delta logic ...
        self._last_update_ts[ticker] = time.time()

    def market_age(self, ticker: str) -> float:
        ts = self._last_update_ts.get(ticker)
        if ts is None:
            return float("inf")
        return time.time() - ts
```

Add `import time` to the top of `src/scanner.py` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_staleness.py -v`
Expected: PASS

- [ ] **Step 5: Add staleness check to dispatcher**

Write a test first:

```python
# Add to tests/test_staleness.py

def test_dispatcher_skips_stale_event():
    """Dispatcher must not evaluate signals when orderbook data is stale."""
    from src.dispatch import Dispatcher
    from src.engine import ArbEngine
    from src.executor import ExecutionManager
    from src.risk import load_risk_profile
    from src.positions import PositionTracker

    profile = load_risk_profile("conservative", {})
    mgr = OrderbookManager()
    mgr.register_event("E1", ["M1", "M2"])

    # Simulate stale data: set timestamp to 10 seconds ago
    mgr.apply_snapshot("M1", {"yes": [[55, 10]], "no": [[45, 10]]})
    mgr.apply_snapshot("M2", {"yes": [[55, 10]], "no": [[45, 10]]})
    mgr._last_update_ts["M1"] = time.time() - 10.0

    engine = ArbEngine(risk_profile=profile)
    api = MagicMock()
    positions = PositionTracker()
    executor = MagicMock(spec=ExecutionManager)
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False

    dispatcher = Dispatcher(
        engine=engine, executor=executor,
        orderbook_mgr=mgr, market_metadata={},
    )

    result = dispatcher.process_orderbook_update("M1")
    assert result is None, "Should skip evaluation when orderbook is stale"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m pytest tests/test_staleness.py::test_dispatcher_skips_stale_event -v`
Expected: FAIL — dispatcher has no staleness check

- [ ] **Step 7: Add staleness check to dispatcher**

In `src/dispatch.py`, add staleness check at the top of `process_orderbook_update`:

```python
    STALE_THRESHOLD_SECS = 5.0

    def process_orderbook_update(self, market_ticker: str) -> TradeSignal | None:
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return None

        if self.executor.is_circuit_breaker_tripped():
            return None

        # Check orderbook staleness for all markets in this event
        event_markets = self.orderbook_mgr.get_event_markets(event_ticker)
        for mt in event_markets:
            age = self.orderbook_mgr.market_age(mt)
            if age > self.STALE_THRESHOLD_SECS:
                logger.warning(
                    "stale orderbook for %s: %s age=%.1fs — skipping signal evaluation",
                    event_ticker, mt, age,
                )
                return None

        if event_ticker in self._pending_execution:
            return None

        # ... rest of method unchanged ...
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python3 -m pytest tests/test_staleness.py -v`
Expected: All PASS

- [ ] **Step 9: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 10: Commit**

```bash
git add src/scanner.py src/dispatch.py tests/test_staleness.py
git commit -m "feat: orderbook staleness detection — skip signals when data >5s old"
```

---

### Task 7: WebSocket Reconnect Timeout (Layer 5)

**Files:**
- Modify: `src/scanner.py:160-169`

- [ ] **Step 1: Add timeout to reconnect**

In `src/scanner.py`, modify `_reconnect`:

```python
    async def _reconnect(self):
        logger.info("Reconnecting to WebSocket...")
        self._ws = None
        try:
            await asyncio.wait_for(self.connect(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("WebSocket reconnect timed out after 30s")
            raise
        if self._stopping:
            return
        if self._fills_subscribed:
            await self.subscribe_fills()
        if self._subscribed_tickers:
            await self.subscribe(list(self._subscribed_tickers))
```

Note: `connect()` already has its own retry loop with exponential backoff. The 30s timeout caps the total reconnect attempt including retries. If it times out, the exception propagates to `listen()` which will call `_reconnect` again.

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add src/scanner.py
git commit -m "feat: add 30s timeout to WebSocket reconnect"
```

---

### Task 8: SIGTERM Handler + Graceful Shutdown (Layer 7)

**Files:**
- Modify: `src/main.py:408-449`

- [ ] **Step 1: Add signal handler and graceful shutdown**

In `src/main.py`, modify the `run()` method:

```python
    async def run(self):
        self._setup_logging()
        self._stats["started_at"] = time.time()
        self.recorder.start_session({
            "mode": self.cfg.mode,
            "risk_mode": self.cfg.risk_mode,
            "max_contracts_per_arb": self.cfg.max_contracts_per_arb,
            "maker_enabled": self.cfg.maker_enabled,
            "circuit_breaker_on_any_loss": self.cfg.circuit_breaker_on_any_loss,
            "max_session_loss": self.cfg.max_session_loss,
            "strategy_overrides": self.cfg.strategy_overrides,
        })
        logger.info("Starting Kalshi Arb Bot in %s mode (risk: %s)",
                     self.cfg.mode.upper(), self.cfg.risk_mode)

        await self._boot_reconcile()
        await self.scanner.connect()
        await self.scanner.subscribe_fills()

        discovery_task = asyncio.create_task(self.discovery.poll_loop(self.cfg.event_poll_interval_secs))
        listen_task = asyncio.create_task(self.scanner.listen())
        status_task = asyncio.create_task(self._report_status())
        cleanup_task = asyncio.create_task(self.discovery.cleanup_loop())
        ob_task = asyncio.create_task(self._process_orderbook_updates())
        maker_task = asyncio.create_task(self._maker_worker()) if self.maker else None

        tasks = [discovery_task, listen_task, status_task, cleanup_task, ob_task]
        if maker_task:
            tasks.append(maker_task)
        if self.two_sided:
            tasks.append(asyncio.create_task(self.two_sided.timeout_loop()))
        if self.cfg.recording_enabled:
            tasks.append(asyncio.create_task(self._snapshot_loop()))
            tasks.append(asyncio.create_task(self._balance_loop()))

        shutdown_event = asyncio.Event()

        def _signal_handler():
            logger.info("Received shutdown signal")
            shutdown_event.set()

        loop = asyncio.get_event_loop()
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            gather_task = asyncio.gather(*tasks)
            shutdown_waiter = asyncio.create_task(shutdown_event.wait())
            done, pending = await asyncio.wait(
                [gather_task, shutdown_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_waiter in done:
                logger.info("Shutting down gracefully...")
                gather_task.cancel()
                try:
                    await gather_task
                except asyncio.CancelledError:
                    pass
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.executor.cancel_unwinds()
            self.scanner.stop()
            self.recorder.end_session()
            self.recorder.close()
            await self.api.close()
```

Add `import signal` to the top of `src/main.py` if not already present.

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: SIGTERM handler + graceful shutdown with unwind cancellation"
```

---

### Code Review Checkpoint 2

After Tasks 6-8, dispatch `superpowers:code-reviewer` to review the WebSocket, staleness, and signal handler changes.

**Base SHA:** commit after Task 5
**Head SHA:** HEAD after Task 8 commit
**Description:** Orderbook staleness detection, WebSocket reconnect timeout, SIGTERM handler, graceful shutdown with unwind cancellation

---

### Task 9: Update Documentation and Skills

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/skills/live-test/SKILL.md`
- Modify: `.claude/skills/strategy-review/SKILL.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to the "Partial Fill Protection" section after the existing content:

```markdown
### Execution Fidelity — Defense in Depth

Eight layers of timeout protection prevent hanging API calls from freezing the bot:

1. **HTTP transport** — `aiohttp.ClientTimeout(total=30, connect=10, sock_read=15)` on the session. No HTTP request can hang >30s.
2. **Executor API calls** — `asyncio.wait_for` on `batch_create_orders` (15s), `batch_cancel_orders` (10s), `get_balance` (10s) inside `execute()` and `_monitor_fills()`.
3. **Emergency shutdown** — 60s overall timeout, 15s per API call inside. If shutdown hangs, logs CRITICAL and proceeds.
4. **Boot reconcile** — 60s timeout. On timeout, proceeds without full reconciliation.
5. **WebSocket reconnect** — 30s timeout on `connect()` inside `_reconnect()`.
6. **Orderbook staleness** — `OrderbookManager.market_age()` tracks seconds since last update. Dispatcher skips signal evaluation when any market in the event is >5s stale.
7. **SIGTERM handler** — `signal.SIGTERM`/`SIGINT` trigger graceful shutdown: cancel tasks, await unwinds, close connections.
8. **Recent trades** — 10s timeout on `get_market_trades()`. On timeout, treats as "no recent trades" (rejects the signal).
```

- [ ] **Step 2: Update strategy-review skill**

In `.claude/skills/strategy-review/SKILL.md`, add to the "Risk Bound Verification" section:

```markdown
- Are all API calls in the execution path wrapped in `asyncio.wait_for`?
- Does the staleness check in dispatch.py prevent trading on stale data?
- Is `cancel_unwinds()` called during shutdown?
```

- [ ] **Step 3: Update live-test skill**

In `.claude/skills/live-test/SKILL.md`, add to the "Key log patterns to watch for" table:

```markdown
| `stale orderbook` | Orderbook data >5s old for a market — signal evaluation skipped |
| `UNWIND TIMEOUT` | Unwind process exceeded max time — manual check needed |
| `timed out` | An API call exceeded its timeout — check connectivity |
```

- [ ] **Step 4: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_scanner.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/skills/strategy-review/SKILL.md .claude/skills/live-test/SKILL.md
git commit -m "docs: update CLAUDE.md and skills with execution fidelity defense-in-depth"
```

---

### Final Code Review

After Task 9, dispatch `superpowers:code-reviewer` for a final review of the complete implementation.

**Base SHA:** commit before Task 1
**Head SHA:** HEAD after Task 9
**Description:** Full execution fidelity defense-in-depth: 8 layers of timeout protection, staleness detection, SIGTERM handler, documentation updates
