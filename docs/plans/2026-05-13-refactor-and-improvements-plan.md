# Kalshi Arb Bot — Refactor & Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix reliability bugs that can cost money, clean up architecture for maintainability, and add strategy improvements for better profitability tracking.

**Architecture:** Three phases — reliability first (fix race conditions, memory leaks, retry logic), then extract `main.py` into smaller modules (`dispatch.py`, `discovery.py`), finally add dynamic sizing and accurate PnL. Each task is self-contained with TDD.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, websockets, pytest

---

## Phase 1: Reliability

### Task 1: Broaden API retry to handle transient errors

Currently `src/api.py:48-76` (`_request`) only retries on HTTP 429. Transient 5xx errors and connection resets abort the request — which can fail a batch order mid-execution.

**Files:**
- Modify: `src/api.py:48-76`
- Test: `tests/test_api.py`

**Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp
from src.api import KalshiAPI


def _make_api():
    auth = MagicMock()
    auth.build_headers.return_value = {}
    api = KalshiAPI(base_url="https://fake.kalshi.com/trade-api/v2", auth=auth)
    return api


def test_retry_on_502():
    """502 errors should be retried, not raised immediately."""
    api = _make_api()

    call_count = 0
    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count < 3:
            resp.status = 502
            resp.text = AsyncMock(return_value="Bad Gateway")
            resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=502, message="Bad Gateway"))
        else:
            resp.status = 200
            resp.json = AsyncMock(return_value={"ok": True})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        result = await api._request("GET", "/test")
        assert result == {"ok": True}
        assert call_count == 3

    asyncio.run(run())


def test_no_retry_on_400():
    """400 errors (bad request) should raise immediately, not retry."""
    api = _make_api()

    async def mock_request(method, url, **kwargs):
        resp = MagicMock()
        resp.status = 400
        resp.text = AsyncMock(return_value="Bad Request")
        resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=400, message="Bad Request"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        try:
            await api._request("GET", "/test")
            assert False, "Should have raised"
        except aiohttp.ClientResponseError as e:
            assert e.status == 400

    asyncio.run(run())


def test_retry_on_connection_error():
    """Connection errors should be retried."""
    api = _make_api()

    call_count = 0
    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise aiohttp.ClientConnectionError("Connection reset")
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"ok": True})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def run():
        session = MagicMock()
        session.request = mock_request
        session.closed = False
        api._session = session
        result = await api._request("GET", "/test")
        assert result == {"ok": True}
        assert call_count == 2

    asyncio.run(run())
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_api.py::test_retry_on_502 tests/test_api.py::test_no_retry_on_400 tests/test_api.py::test_retry_on_connection_error -v`

Expected: `test_retry_on_502` and `test_retry_on_connection_error` FAIL (they raise instead of retrying). `test_no_retry_on_400` may pass already.

**Step 3: Implement retry logic**

In `src/api.py`, replace the `_request` method (lines 48-76) with:

```python
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

async def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    session = await self._ensure_session()
    url = f"{self.base_url}{path}"
    sign_path = f"{self._sign_path_prefix}{path}"
    headers = self._headers(method, sign_path)

    for attempt in range(3):
        await self._throttle()
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body

        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status in RETRYABLE_STATUSES:
                    wait = 2 ** attempt + 1
                    logger.warning("Retryable error %d on %s %s, backing off %ds (attempt %d/3)",
                                   resp.status, method, path, wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                if resp.status >= 400:
                    error_body = await resp.text()
                    logger.error("API error %d %s %s: %s", resp.status, method, path, error_body)
                    resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientConnectionError:
            if attempt == 2:
                raise
            wait = 2 ** attempt + 1
            logger.warning("Connection error on %s %s, backing off %ds (attempt %d/3)",
                           method, path, wait, attempt + 1)
            await asyncio.sleep(wait)

    raise aiohttp.ClientResponseError(
        request_info=None, history=(), status=429, message="Rate limited after retries"
    )
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_api.py -v`

Expected: ALL PASS

**Step 5: Run full test suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS (no regressions)

**Step 6: Commit**

```bash
git add src/api.py tests/test_api.py
git commit -m "fix: retry API requests on 5xx and connection errors"
```

---

### Task 2: Guard duplicate signal execution with per-event pending set

Currently in `src/main.py:115-151`, between `evaluate()` returning a signal and `execute()` setting `self._executing = True`, concurrent orderbook updates for the same event can trigger duplicate execution tasks.

**Files:**
- Modify: `src/main.py:62-63` (add `_pending_execution` set)
- Modify: `src/main.py:115-151` (`_on_orderbook_update`)
- Modify: `src/main.py:223-235` (`_execute_and_track`)
- Test: `tests/test_main.py` (new file)

**Step 1: Write failing test**

Create `tests/test_main.py`:

```python
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from src.models import Orderbook, TradeSignal


def test_pending_execution_prevents_duplicate():
    """If an event is in _pending_execution, _on_orderbook_update should not fire another execution."""
    from src.main import ArbBot

    with patch("src.main.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.api_key_id = "fake"
        cfg.private_key_path = "fake.pem"
        cfg.rest_base_url = "https://fake"
        cfg.ws_url = "wss://fake"
        cfg.risk_mode = "aggressive"
        cfg.strategy_overrides = {}
        cfg.fill_timeout_secs = 30
        cfg.event_poll_interval_secs = 60
        cfg.max_session_loss = 1.0
        cfg.circuit_breaker_on_any_loss = True
        cfg.maker_enabled = False
        cfg.maker_fill_mode = "cancel_and_take"
        cfg.max_maker_events = 3
        cfg.maker_max_horizon_hours = 2.0
        cfg.log_level = "INFO"
        cfg.log_file = "/dev/null"
        mock_cfg.return_value = cfg

        with patch("src.main.KalshiAuth"):
            with patch("src.main.KalshiAPI"):
                with patch("src.main.MarketScanner"):
                    bot = ArbBot("fake.yaml")

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )
    bot.engine.evaluate = MagicMock(return_value=signal)
    bot.executor.is_executing = MagicMock(return_value=False)
    bot.executor.is_event_blacklisted = MagicMock(return_value=False)
    bot.executor.is_circuit_breaker_tripped = MagicMock(return_value=False)

    bot.orderbook_mgr.register_event("E1", ["M1", "M2", "M3"])
    book = Orderbook(yes_bids={40: 100}, no_bids={})
    bot.orderbook_mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.4000", "100"]], "no_dollars_fp": []})
    bot.orderbook_mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})
    bot.orderbook_mgr.apply_snapshot("M3", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})

    # Simulate: event already pending execution
    bot._pending_execution.add("E1")

    bot._on_orderbook_update("M1")

    # evaluate should not even be called because event is pending
    bot.engine.evaluate.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py::test_pending_execution_prevents_duplicate -v`

Expected: FAIL — `AttributeError: 'ArbBot' object has no attribute '_pending_execution'`

**Step 3: Implement per-event pending guard**

In `src/main.py`, add `_pending_execution` set at line 62 (after `_event_tickers`):

```python
self._pending_execution: set[str] = set()
```

In `_on_orderbook_update` (line 115), add check after circuit breaker check:

```python
def _on_orderbook_update(self, market_ticker: str):
    event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
    if not event_ticker:
        return

    if self.executor.is_circuit_breaker_tripped():
        return

    if event_ticker in self._pending_execution:
        return

    event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
    meta = {t: self._market_metadata.get(t, {}) for t in event_books}

    signal = self.engine.evaluate(event_ticker, event_books, market_metadata=meta)

    if signal and not self.executor.is_executing():
        if self.executor.is_event_blacklisted(event_ticker):
            return
        if self.maker and self.maker.is_event_active(event_ticker):
            asyncio.create_task(self.maker.cancel_event(event_ticker))
        last = self._last_signal_time.get(event_ticker, 0)
        if time.time() - last < self._signal_cooldown:
            return
        self._last_signal_time[event_ticker] = time.time()
        self._stats["arbs_detected"] += 1
        self._stats["total_theoretical_profit"] += signal.net_profit
        logger.info(
            json.dumps({
                "event": "arb_detected",
                "event_ticker": event_ticker,
                "legs": signal.legs,
                "net_profit": round(signal.net_profit, 6),
                "profit_pct": round(signal.profit_pct, 2),
                "exposure_ratio": round(signal.exposure_ratio, 2),
            })
        )
        self._pending_execution.add(event_ticker)
        asyncio.create_task(self._execute_and_track(signal))
        return

    if self.maker and not signal:
        self._maker_dirty_events.add(event_ticker)
        if self._maker_queue and not self._maker_queue.full():
            try:
                self._maker_queue.put_nowait(True)
            except asyncio.QueueFull:
                pass
```

In `_execute_and_track` (line 223), remove event from pending in finally:

```python
async def _execute_and_track(self, signal):
    try:
        tickers = [t for t, _ in signal.legs]
        if not await self._validate_recent_trades(tickers):
            logger.info("Recent trades check failed for %s, skipping", signal.event_ticker)
            return
        await self.executor.execute(signal)
        self._stats["arbs_executed"] += 1
        if self.executor.is_circuit_breaker_tripped():
            await self._emergency_shutdown()
    except Exception:
        logger.exception("Failed to execute arb for %s", signal.event_ticker)
        self._stats["arbs_failed"] += 1
    finally:
        self._pending_execution.discard(signal.event_ticker)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py -v`

Expected: PASS

**Step 5: Run full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "fix: guard duplicate signal execution with per-event pending set"
```

---

### Task 3: Periodic expired event cleanup

Events that close/expire are never removed from `OrderbookManager` or `_market_metadata`, growing unbounded over time.

**Files:**
- Modify: `src/main.py` (add `_cleanup_expired_events` coroutine, launch in `run()`)
- Test: `tests/test_main.py`

**Step 1: Write failing test**

Add to `tests/test_main.py`:

```python
from datetime import datetime, timezone, timedelta


def _make_bot():
    """Create an ArbBot with all dependencies mocked."""
    with patch("src.main.load_config") as mock_cfg:
        cfg = MagicMock()
        cfg.api_key_id = "fake"
        cfg.private_key_path = "fake.pem"
        cfg.rest_base_url = "https://fake"
        cfg.ws_url = "wss://fake"
        cfg.risk_mode = "aggressive"
        cfg.strategy_overrides = {}
        cfg.fill_timeout_secs = 30
        cfg.event_poll_interval_secs = 60
        cfg.max_session_loss = 1.0
        cfg.circuit_breaker_on_any_loss = True
        cfg.maker_enabled = False
        cfg.maker_fill_mode = "cancel_and_take"
        cfg.max_maker_events = 3
        cfg.maker_max_horizon_hours = 2.0
        cfg.log_level = "INFO"
        cfg.log_file = "/dev/null"
        mock_cfg.return_value = cfg

        with patch("src.main.KalshiAuth"):
            with patch("src.main.KalshiAPI"):
                with patch("src.main.MarketScanner"):
                    return ArbBot("fake.yaml")


def test_cleanup_expired_events():
    """Expired events should be removed from orderbook manager and metadata."""
    bot = _make_bot()

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    bot._event_tickers = {"E_EXPIRED", "E_ACTIVE"}
    bot.orderbook_mgr.register_event("E_EXPIRED", ["M_EXP1", "M_EXP2"])
    bot.orderbook_mgr.register_event("E_ACTIVE", ["M_ACT1", "M_ACT2"])
    bot._market_metadata = {
        "M_EXP1": {"close_time": past},
        "M_EXP2": {"close_time": past},
        "M_ACT1": {"close_time": future},
        "M_ACT2": {"close_time": future},
    }

    bot._cleanup_expired_events_now()

    assert "E_EXPIRED" not in bot._event_tickers
    assert "E_ACTIVE" in bot._event_tickers
    assert "M_EXP1" not in bot._market_metadata
    assert "M_ACT1" in bot._market_metadata
    assert bot.orderbook_mgr.get_event_for_market("M_EXP1") is None
    assert bot.orderbook_mgr.get_event_for_market("M_ACT1") == "E_ACTIVE"
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py::test_cleanup_expired_events -v`

Expected: FAIL — `AttributeError: 'ArbBot' object has no attribute '_cleanup_expired_events_now'`

**Step 3: Implement cleanup**

Add to `ArbBot` in `src/main.py`:

```python
def _cleanup_expired_events_now(self):
    now = datetime.now(timezone.utc)
    expired_events: set[str] = set()

    for event_ticker in list(self._event_tickers):
        market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
        if not market_tickers:
            continue
        all_expired = True
        for mt in market_tickers:
            meta = self._market_metadata.get(mt, {})
            close_str = meta.get("close_time", "")
            if not close_str:
                all_expired = False
                break
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                if close_dt > now:
                    all_expired = False
                    break
            except (ValueError, TypeError):
                all_expired = False
                break
        if all_expired:
            expired_events.add(event_ticker)

    for event_ticker in expired_events:
        market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
        for mt in market_tickers:
            self._market_metadata.pop(mt, None)
        self.orderbook_mgr.unregister_event(event_ticker)
        self._event_tickers.discard(event_ticker)
        logger.info("Cleaned up expired event: %s (%d markets)", event_ticker, len(market_tickers))

async def _cleanup_expired_events(self):
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        self._cleanup_expired_events_now()
```

Add `from datetime import datetime, timezone` to top of `src/main.py`.

In `run()`, add the cleanup task alongside the others:

```python
cleanup_task = asyncio.create_task(self._cleanup_expired_events())
tasks = [discovery_task, listen_task, status_task, cleanup_task]
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_main.py::test_cleanup_expired_events -v`

Expected: PASS

**Step 5: Full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "fix: periodically clean up expired events from orderbook manager"
```

---

### Task 4: Make orderbook callback async via queue

Currently `_on_orderbook_update` runs synchronously inside `MarketScanner.listen()`, blocking WS message processing.

**Files:**
- Modify: `src/scanner.py:74-198` (change callback type, add queue option)
- Modify: `src/main.py` (add `_process_orderbook_updates` coroutine)
- Test: `tests/test_scanner.py` (add async callback test)

**Step 1: Write failing test**

Add to `tests/test_scanner.py`:

```python
def test_listen_with_async_callback():
    """listen() should support async on_orderbook_update callbacks."""
    auth = MagicMock()
    auth.build_headers.return_value = {}

    received_tickers = []

    async def async_callback(ticker: str):
        received_tickers.append(ticker)

    scanner = MarketScanner(
        ws_url="wss://fake",
        auth=auth,
        orderbook_mgr=OrderbookManager(),
        on_orderbook_update=async_callback,
    )

    scanner.orderbook_mgr.register_event("E1", ["M1"])

    fake_ws = MagicMock()
    messages = [
        '{"type": "orderbook_snapshot", "msg": {"market_ticker": "M1", "yes_dollars_fp": [["0.40", "100"]], "no_dollars_fp": []}}',
    ]
    msg_iter = iter(messages)

    async def fake_recv():
        try:
            return next(msg_iter)
        except StopIteration:
            scanner._running = False
            raise websockets.ConnectionClosed(None, None)

    fake_ws.recv = fake_recv
    scanner._ws = fake_ws
    scanner._running = True

    asyncio.run(scanner.listen())
    assert "M1" in received_tickers
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scanner.py::test_listen_with_async_callback -v`

Expected: FAIL — the callback is called synchronously, and since it's a coroutine, it won't actually execute (it'll be a coroutine object, not awaited).

**Step 3: Implement async callback support**

In `src/scanner.py`, modify `listen()` to detect and await async callbacks:

```python
import inspect

# In listen(), replace the callback invocation pattern:
async def listen(self):
    if not self._ws:
        return
    while self._running:
        try:
            raw = await self._ws.recv()
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "orderbook_snapshot":
                ticker = data["msg"]["market_ticker"]
                self.orderbook_mgr.apply_snapshot(ticker, data["msg"])
                await self._fire_orderbook_update(ticker)

            elif msg_type == "orderbook_delta":
                ticker = data["msg"]["market_ticker"]
                self.orderbook_mgr.apply_delta(ticker, data["msg"])
                await self._fire_orderbook_update(ticker)

            elif msg_type == "fill":
                if self.on_fill:
                    self.on_fill(data["msg"])

        except websockets.ConnectionClosed:
            logger.warning("WebSocket disconnected")
            await self._reconnect()

        except Exception:
            logger.exception("Error processing WebSocket message")

async def _fire_orderbook_update(self, ticker: str):
    if self.on_orderbook_update:
        result = self.on_orderbook_update(ticker)
        if inspect.isawaitable(result):
            await result
```

**Step 4: Modify main.py callback to use queue pattern**

In `src/main.py`, add queue-based processing:

```python
# Add to __init__ (after _maker_queue):
self._ob_update_queue: asyncio.Queue[str] = asyncio.Queue()

# Change _on_orderbook_update to just enqueue:
def _on_orderbook_update(self, market_ticker: str):
    try:
        self._ob_update_queue.put_nowait(market_ticker)
    except asyncio.QueueFull:
        pass

# Add async processor:
async def _process_orderbook_updates(self):
    while True:
        market_ticker = await self._ob_update_queue.get()
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            continue

        if self.executor.is_circuit_breaker_tripped():
            continue

        if event_ticker in self._pending_execution:
            continue

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        meta = {t: self._market_metadata.get(t, {}) for t in event_books}

        signal = self.engine.evaluate(event_ticker, event_books, market_metadata=meta)

        if signal and not self.executor.is_executing():
            if self.executor.is_event_blacklisted(event_ticker):
                continue
            if self.maker and self.maker.is_event_active(event_ticker):
                asyncio.create_task(self.maker.cancel_event(event_ticker))
            last = self._last_signal_time.get(event_ticker, 0)
            if time.time() - last < self._signal_cooldown:
                continue
            self._last_signal_time[event_ticker] = time.time()
            self._stats["arbs_detected"] += 1
            self._stats["total_theoretical_profit"] += signal.net_profit
            logger.info(
                json.dumps({
                    "event": "arb_detected",
                    "event_ticker": event_ticker,
                    "legs": signal.legs,
                    "net_profit": round(signal.net_profit, 6),
                    "profit_pct": round(signal.profit_pct, 2),
                    "exposure_ratio": round(signal.exposure_ratio, 2),
                })
            )
            self._pending_execution.add(event_ticker)
            asyncio.create_task(self._execute_and_track(signal))
            continue

        if self.maker and not signal:
            self._maker_dirty_events.add(event_ticker)
            if self._maker_queue and not self._maker_queue.full():
                try:
                    self._maker_queue.put_nowait(True)
                except asyncio.QueueFull:
                    pass
```

In `run()`, add the processor task:

```python
ob_task = asyncio.create_task(self._process_orderbook_updates())
tasks = [discovery_task, listen_task, status_task, cleanup_task, ob_task]
```

**Step 5: Run tests**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/scanner.py src/main.py tests/test_scanner.py
git commit -m "fix: decouple orderbook processing from WS listen loop via async queue"
```

---

## Phase 2: Architecture Cleanup

### Task 5: Extract dispatch layer (`src/dispatch.py`)

Move the orderbook update processing and fill routing logic from `ArbBot` into a standalone `Dispatcher` class.

**Files:**
- Create: `src/dispatch.py`
- Modify: `src/main.py`
- Create: `tests/test_dispatch.py`

**Step 1: Write failing test**

Create `tests/test_dispatch.py`:

```python
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

from src.dispatch import Dispatcher
from src.models import Orderbook, TradeSignal
from src.scanner import OrderbookManager


def _make_dispatcher():
    engine = MagicMock()
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False

    ob_mgr = OrderbookManager()
    ob_mgr.register_event("E1", ["M1", "M2", "M3"])

    dispatcher = Dispatcher(
        engine=engine,
        executor=executor,
        maker=None,
        orderbook_mgr=ob_mgr,
        market_metadata={},
    )
    return dispatcher, engine, executor


def test_dispatch_routes_profitable_signal():
    """When engine.evaluate returns a signal, dispatcher should fire execution."""
    dispatcher, engine, executor = _make_dispatcher()

    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03, profit_pct=3.0, exposure_ratio=1.5,
    )
    engine.evaluate.return_value = signal

    # Apply snapshots so orderbooks exist
    dispatcher.orderbook_mgr.apply_snapshot("M1", {"yes_dollars_fp": [["0.4000", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M2", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})
    dispatcher.orderbook_mgr.apply_snapshot("M3", {"yes_dollars_fp": [["0.3500", "100"]], "no_dollars_fp": []})

    result = dispatcher.process_orderbook_update("M1")
    assert result is not None
    assert result.event_ticker == "E1"


def test_dispatch_skips_pending_event():
    """Events already pending execution should be skipped."""
    dispatcher, engine, executor = _make_dispatcher()
    dispatcher._pending_execution.add("E1")

    result = dispatcher.process_orderbook_update("M1")
    assert result is None
    engine.evaluate.assert_not_called()


def test_dispatch_routes_fill_to_executor():
    """Fills not owned by maker should go to executor."""
    dispatcher, _, executor = _make_dispatcher()
    fill = {"order_id": "o1", "market_ticker": "M1", "yes_price_dollars": "0.40", "count_fp": "1"}
    dispatcher.route_fill(fill)
    executor.handle_fill.assert_called_once_with(fill)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dispatch.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'src.dispatch'`

**Step 3: Implement Dispatcher**

Create `src/dispatch.py`:

```python
import json
import logging
import time

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import TradeSignal
from src.scanner import OrderbookManager

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        engine: ArbEngine,
        executor: ExecutionManager,
        maker,
        orderbook_mgr: OrderbookManager,
        market_metadata: dict[str, dict],
        signal_cooldown: float = 60.0,
    ):
        self.engine = engine
        self.executor = executor
        self.maker = maker
        self.orderbook_mgr = orderbook_mgr
        self.market_metadata = market_metadata
        self._signal_cooldown = signal_cooldown
        self._last_signal_time: dict[str, float] = {}
        self._pending_execution: set[str] = set()
        self._maker_dirty_events: set[str] = set()
        self.stats = {
            "arbs_detected": 0,
            "total_theoretical_profit": 0.0,
        }

    def process_orderbook_update(self, market_ticker: str) -> TradeSignal | None:
        event_ticker = self.orderbook_mgr.get_event_for_market(market_ticker)
        if not event_ticker:
            return None

        if self.executor.is_circuit_breaker_tripped():
            return None

        if event_ticker in self._pending_execution:
            return None

        event_books = self.orderbook_mgr.get_event_orderbooks(event_ticker)
        meta = {t: self.market_metadata.get(t, {}) for t in event_books}

        signal = self.engine.evaluate(event_ticker, event_books, market_metadata=meta)

        if signal and not self.executor.is_executing():
            if self.executor.is_event_blacklisted(event_ticker):
                return None
            if self.maker and self.maker.is_event_active(event_ticker):
                return signal
            last = self._last_signal_time.get(event_ticker, 0)
            if time.time() - last < self._signal_cooldown:
                return None
            self._last_signal_time[event_ticker] = time.time()
            self.stats["arbs_detected"] += 1
            self.stats["total_theoretical_profit"] += signal.net_profit
            self._pending_execution.add(event_ticker)
            logger.info(
                json.dumps({
                    "event": "arb_detected",
                    "event_ticker": event_ticker,
                    "legs": signal.legs,
                    "net_profit": round(signal.net_profit, 6),
                    "profit_pct": round(signal.profit_pct, 2),
                    "exposure_ratio": round(signal.exposure_ratio, 2),
                })
            )
            return signal

        if self.maker and not signal:
            self._maker_dirty_events.add(event_ticker)

        return None

    def mark_execution_complete(self, event_ticker: str):
        self._pending_execution.discard(event_ticker)

    def consume_dirty_events(self) -> list[str]:
        dirty = list(self._maker_dirty_events)
        self._maker_dirty_events.clear()
        return dirty

    def route_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        if self.maker and self.maker.owns_order(order_id):
            return "maker"
        self.executor.handle_fill(fill_data)
        return "executor"
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_dispatch.py -v`

Expected: ALL PASS

**Step 5: Wire Dispatcher into main.py**

Update `src/main.py` to use `Dispatcher` instead of inline logic. Replace `_on_orderbook_update`, `_on_fill`, and `_process_orderbook_updates` with dispatcher calls. The `ArbBot.__init__` should create a `Dispatcher` and the processing loop should use `dispatcher.process_orderbook_update()`.

**Step 6: Full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/dispatch.py tests/test_dispatch.py src/main.py
git commit -m "refactor: extract dispatch layer from main.py"
```

---

### Task 6: Extract event discovery (`src/discovery.py`)

Move `_full_scan`, `_discover_events`, `_register_events`, `_market_metadata`, and the cleanup logic into an `EventDiscovery` class.

**Files:**
- Create: `src/discovery.py`
- Modify: `src/main.py`
- Create: `tests/test_discovery.py`

**Step 1: Write failing test**

Create `tests/test_discovery.py`:

```python
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from src.discovery import EventDiscovery
from src.scanner import OrderbookManager
from src.models import Event, Market


def _make_discovery():
    api = MagicMock()
    ob_mgr = OrderbookManager()
    scanner = MagicMock()
    scanner.subscribe = AsyncMock()
    discovery = EventDiscovery(api=api, orderbook_mgr=ob_mgr, scanner=scanner)
    return discovery, api


def test_register_events_stores_metadata():
    discovery, _ = _make_discovery()
    market = Market(
        ticker="M1", event_ticker="E1", title="Test",
        status="active", close_time="2026-06-01T00:00:00Z",
        volume_24h=100.0,
    )
    event = Event(event_ticker="E1", title="Test Event", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    new_tickers = discovery.register_events([event])
    assert new_tickers == ["M1"]
    assert "M1" in discovery.market_metadata
    assert discovery.market_metadata["M1"]["volume_24h"] == 100.0


def test_register_events_skips_duplicates():
    discovery, _ = _make_discovery()
    market = Market(ticker="M1", event_ticker="E1", title="Test",
                    status="active", volume_24h=100.0)
    event = Event(event_ticker="E1", title="Test", series_ticker="S1",
                  mutually_exclusive=True, markets=[market])

    discovery.register_events([event])
    new_tickers = discovery.register_events([event])
    assert new_tickers == []


def test_cleanup_removes_expired():
    discovery, _ = _make_discovery()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    m_exp = Market(ticker="M_EXP", event_ticker="E_EXP", title="Exp",
                   status="active", close_time=past)
    m_act = Market(ticker="M_ACT", event_ticker="E_ACT", title="Act",
                   status="active", close_time=future)

    discovery.register_events([
        Event(event_ticker="E_EXP", title="", series_ticker="", mutually_exclusive=True, markets=[m_exp]),
        Event(event_ticker="E_ACT", title="", series_ticker="", mutually_exclusive=True, markets=[m_act]),
    ])

    removed = discovery.cleanup_expired()
    assert "E_EXP" in removed
    assert "E_ACT" not in removed
    assert "M_EXP" not in discovery.market_metadata
    assert "M_ACT" in discovery.market_metadata
```

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/test_discovery.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'src.discovery'`

**Step 3: Implement EventDiscovery**

Create `src/discovery.py`:

```python
import asyncio
import logging
from datetime import datetime, timezone

from src.api import KalshiAPI
from src.models import Event
from src.scanner import OrderbookManager

logger = logging.getLogger(__name__)


class EventDiscovery:
    def __init__(self, api: KalshiAPI, orderbook_mgr: OrderbookManager, scanner):
        self.api = api
        self.orderbook_mgr = orderbook_mgr
        self.scanner = scanner
        self.event_tickers: set[str] = set()
        self.market_metadata: dict[str, dict] = {}

    def register_events(self, events: list[Event]) -> list[str]:
        new_tickers = []
        for event in events:
            if event.event_ticker not in self.event_tickers:
                self.event_tickers.add(event.event_ticker)
                market_tickers = event.market_tickers()
                self.orderbook_mgr.register_event(event.event_ticker, market_tickers)
                new_tickers.extend(market_tickers)
            for m in event.markets:
                self.market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.expected_expiration_time,
                    "volume_24h": m.volume_24h,
                }
        return new_tickers

    def cleanup_expired(self) -> set[str]:
        now = datetime.now(timezone.utc)
        expired: set[str] = set()

        for event_ticker in list(self.event_tickers):
            market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
            if not market_tickers:
                continue
            all_expired = True
            for mt in market_tickers:
                meta = self.market_metadata.get(mt, {})
                close_str = meta.get("close_time", "")
                if not close_str:
                    all_expired = False
                    break
                try:
                    close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                    if close_dt > now:
                        all_expired = False
                        break
                except (ValueError, TypeError):
                    all_expired = False
                    break
            if all_expired:
                expired.add(event_ticker)

        for event_ticker in expired:
            market_tickers = self.orderbook_mgr._event_markets.get(event_ticker, [])
            for mt in market_tickers:
                self.market_metadata.pop(mt, None)
            self.orderbook_mgr.unregister_event(event_ticker)
            self.event_tickers.discard(event_ticker)
            logger.info("Cleaned up expired event: %s (%d markets)", event_ticker, len(market_tickers))

        return expired

    async def full_scan(self):
        logger.info("Starting full event scan...")
        cursor = ""
        pages = 0
        retries = 0
        max_retries = 3
        all_events = []
        while True:
            try:
                events, next_cursor = await self.api.fetch_events_page(cursor)
                all_events.extend(events)
                pages += 1
                retries = 0
                if pages % 10 == 0:
                    logger.info("Scanning page %d... (%d events collected)", pages, len(all_events))
                if not next_cursor:
                    break
                cursor = next_cursor
                await asyncio.sleep(0.5)
            except Exception:
                retries += 1
                if retries >= max_retries:
                    logger.error("Full scan aborted after %d retries at page %d", max_retries, pages)
                    break
                logger.exception("Error during full scan (retry %d/%d)", retries, max_retries)
                await asyncio.sleep(5)

        def _earliest_close(event):
            times = [m.close_time for m in event.markets if m.close_time]
            return min(times) if times else "9999"

        all_events.sort(key=_earliest_close)
        all_new = []
        for event in all_events:
            all_new.extend(self.register_events([event]))
        if all_new:
            await self.scanner.subscribe(all_new)

        logger.info("Full scan complete: %d pages, %d events, %d new markets",
                     pages, len(self.event_tickers), len(all_new))

    async def poll_loop(self, interval_secs: int):
        await self.full_scan()
        while True:
            await asyncio.sleep(interval_secs)
            try:
                events, _ = await self.api.fetch_events_page("")
                new_tickers = self.register_events(events)
                if new_tickers:
                    await self.scanner.subscribe(new_tickers)
                    logger.info("Re-poll: %d new markets found", len(new_tickers))
            except Exception:
                logger.exception("Error during event re-poll")

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(300)
            self.cleanup_expired()
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_discovery.py -v`

Expected: ALL PASS

**Step 5: Wire into main.py**

Replace `_full_scan`, `_discover_events`, `_register_events`, `_market_metadata`, `_event_tickers`, and `_cleanup_expired_events` in `ArbBot` with `EventDiscovery` usage. `ArbBot.__init__` creates an `EventDiscovery` instance; `run()` launches `discovery.poll_loop()` and `discovery.cleanup_loop()` as tasks. The dispatcher's `market_metadata` points to `discovery.market_metadata`.

**Step 6: Full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/discovery.py tests/test_discovery.py src/main.py tests/test_main.py
git commit -m "refactor: extract event discovery into src/discovery.py"
```

---

### Task 7: Consolidate config wiring — pass RiskProfile directly

Currently `ExecutionManager.__init__` and `MakerManager.__init__` take individual risk fields. Pass the full `RiskProfile` instead.

**Files:**
- Modify: `src/executor.py:23-39`
- Modify: `src/maker.py:31-48`
- Modify: `src/main.py` (constructor calls)
- Modify: `tests/test_executor.py`, `tests/test_maker.py`

**Step 1: Write failing test**

Add to `tests/test_executor.py`:

```python
def test_executor_accepts_risk_profile_directly():
    from src.risk import load_risk_profile
    profile = load_risk_profile("conservative", {})
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    positions = MagicMock()
    executor = ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=30, risk_profile=profile,
    )
    assert executor._unwind_phase1_secs == profile.unwind_phase1_secs
    assert executor._unwind_phase2_secs == profile.unwind_phase2_secs
    assert executor._unwind_price_step_cents == profile.unwind_price_step_cents
```

Add to `tests/test_maker.py`:

```python
def test_maker_accepts_risk_profile():
    from src.risk import load_risk_profile
    profile = load_risk_profile("conservative", {})
    api = MagicMock()
    api.unwrap_order = lambda raw: raw.get("order", raw)
    maker = MakerManager(api=api, risk_profile=profile)
    assert maker._tighten_phase1_secs == profile.unwind_phase1_secs
    assert maker._tighten_phase2_secs == profile.unwind_phase2_secs
    assert maker._tighten_step_cents == profile.unwind_price_step_cents
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_executor.py::test_executor_accepts_risk_profile_directly tests/test_maker.py::test_maker_accepts_risk_profile -v`

Expected: FAIL — `MakerManager.__init__` doesn't accept `risk_profile` kwarg directly.

**Step 3: Implement**

In `src/maker.py`, change `__init__`:

```python
def __init__(self, api: KalshiAPI, fill_mode: str = "cancel_and_take",
             max_events: int = 3, risk_profile=None,
             tighten_phase1_secs: int = 15, tighten_phase2_secs: int = 30,
             tighten_step_cents: int = 3):
    self.api = api
    if fill_mode not in self.VALID_FILL_MODES:
        logger.warning("Unknown fill_mode %r, falling back to cancel_and_take", fill_mode)
        fill_mode = "cancel_and_take"
    self.fill_mode = fill_mode
    self.max_events = max_events
    if risk_profile:
        self._tighten_phase1_secs = risk_profile.unwind_phase1_secs
        self._tighten_phase2_secs = risk_profile.unwind_phase2_secs
        self._tighten_step_cents = risk_profile.unwind_price_step_cents
    else:
        self._tighten_phase1_secs = tighten_phase1_secs
        self._tighten_phase2_secs = tighten_phase2_secs
        self._tighten_step_cents = tighten_step_cents
    # ... rest unchanged
```

In `src/main.py`, simplify the MakerManager construction:

```python
self.maker = MakerManager(
    api=self.api,
    fill_mode=self.cfg.maker_fill_mode,
    max_events=self.cfg.max_maker_events,
    risk_profile=self.risk_profile,
) if self.cfg.maker_enabled else None
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/maker.py src/main.py tests/test_maker.py tests/test_executor.py
git commit -m "refactor: pass RiskProfile directly to MakerManager"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update**

Remove the "No WebSocket reconnection logic" known limitation. Add `dispatch.py` and `discovery.py` to architecture docs. Update the data flow diagram.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new modules and remove stale limitations"
```

---

## Phase 3: Strategy Improvements

### Task 9: Fix PositionTracker to handle closes/unwinds

Currently `src/positions.py:19-33` (`record_fill`) only adds to quantity — buys never decrement.

**Files:**
- Modify: `src/positions.py:19-33`
- Modify: `tests/test_positions.py`

**Step 1: Write failing test**

Add to `tests/test_positions.py`:

```python
def test_buy_decrements_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=5, action="buy")
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 5


def test_buy_fully_closes_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    pos = tracker.get_position("M1")
    assert pos is None


def test_open_positions_excludes_closed():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.40, quantity=5, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    positions = tracker.open_positions()
    assert len(positions) == 1
    assert positions[0].ticker == "M2"


def test_buy_tracks_realized_pnl():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    assert tracker.realized_pnl == -0.5  # sold@0.55, bought@0.60, 10 contracts => -$0.50
```

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/test_positions.py::test_buy_decrements_position tests/test_positions.py::test_buy_fully_closes_position tests/test_positions.py::test_buy_tracks_realized_pnl -v`

Expected: FAIL — buys add to quantity instead of decrementing; `realized_pnl` doesn't exist.

**Step 3: Implement**

Replace `src/positions.py`:

```python
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    ticker: str
    side: str
    quantity: float
    avg_price: float


class PositionTracker:
    def __init__(self):
        self._positions: dict[str, TrackedPosition] = {}
        self.realized_pnl: float = 0.0

    def record_fill(self, ticker: str, side: str, price: float, quantity: float, action: str):
        if quantity <= 0:
            return

        if action == "buy" and ticker in self._positions:
            pos = self._positions[ticker]
            pnl = (pos.avg_price - price) * min(quantity, pos.quantity)
            self.realized_pnl += pnl
            pos.quantity -= quantity
            if pos.quantity <= 0:
                del self._positions[ticker]
            logger.info("Close: buy %dx %s @ %.4f (realized: $%.4f)", quantity, ticker, price, pnl)
            return

        if ticker in self._positions:
            pos = self._positions[ticker]
            total_cost = pos.avg_price * pos.quantity + price * quantity
            pos.quantity += quantity
            pos.avg_price = total_cost / pos.quantity
        else:
            self._positions[ticker] = TrackedPosition(
                ticker=ticker,
                side=side,
                quantity=quantity,
                avg_price=price,
            )
        logger.info("Fill: %s %dx %s @ %.4f", action, quantity, ticker, price)

    def get_position(self, ticker: str) -> TrackedPosition | None:
        return self._positions.get(ticker)

    def open_positions(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.quantity > 0]

    def calculate_event_pnl(self, tickers: list[str]) -> dict:
        total_premium = 0.0
        max_quantity = 0.0
        for t in tickers:
            pos = self._positions.get(t)
            if pos:
                total_premium += pos.avg_price * pos.quantity
                max_quantity = max(max_quantity, pos.quantity)
        max_payout = 1.0 * max_quantity
        return {
            "total_premium": total_premium,
            "max_payout": max_payout,
            "gross_profit": total_premium - max_payout,
        }
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_positions.py -v`

Expected: ALL PASS

**Step 5: Full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/positions.py tests/test_positions.py
git commit -m "fix: PositionTracker now decrements on buy and tracks realized PnL"
```

---

### Task 10: Accurate PnL in status report

Replace the misleading `realized_pnl` (actually premium collected) in `_report_status` with real metrics.

**Files:**
- Modify: `src/main.py` (`_report_status` method, around line 266-292)

**Step 1: Implement**

In `_report_status`, replace the PnL calculation:

```python
async def _report_status(self):
    while True:
        await asyncio.sleep(30)
        uptime = time.time() - self._stats["started_at"]
        positions = self.positions.open_positions()
        unrealized_premium = sum(p.avg_price * p.quantity for p in positions)
        realized_pnl = self.positions.realized_pnl
        cb_status = "TRIPPED" if self.executor.is_circuit_breaker_tripped() else "ok"
        maker_count = self.maker.active_event_count() if self.maker else 0
        logger.info(
            "STATUS | uptime=%.0fs | events=%d | arbs_detected=%d | "
            "arbs_executed=%d | arbs_failed=%d | theoretical_profit=$%.4f | "
            "open_positions=%d | unrealized_premium=$%.4f | "
            "realized_pnl=$%.4f | session_loss=$%.4f | circuit_breaker=%s | maker_events=%d",
            uptime,
            len(self._event_tickers),
            self._stats["arbs_detected"],
            self._stats["arbs_executed"],
            self._stats["arbs_failed"],
            self._stats["total_theoretical_profit"],
            len(positions),
            unrealized_premium,
            realized_pnl,
            self.executor.session_realized_loss,
            cb_status,
            maker_count,
        )
```

**Step 2: Run full suite**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "fix: report accurate realized PnL and unrealized premium in status"
```

---

### Task 11: Dynamic quantity sizing

Currently all orders trade 1 contract. Size based on minimum bid depth across all legs, capped by a configurable maximum.

**Files:**
- Modify: `src/config.py` (add `max_contracts_per_arb` field)
- Modify: `src/models.py` (add `quantity` to `TradeSignal`)
- Modify: `src/engine.py` (compute quantity from depth)
- Modify: `config.example.yaml`
- Modify: `tests/test_engine.py`

**Step 1: Write failing test**

Add to `tests/test_engine.py`:

```python
def test_signal_includes_quantity_from_depth():
    """Signal quantity should be min(depth) across legs, capped by max_contracts."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    engine.max_contracts_per_arb = 5
    orderbooks = {
        "M1": _ob([(0.40, 10)]),  # 10 contracts at best bid
        "M2": _ob([(0.35, 3)]),   # 3 contracts at best bid
        "M3": _ob([(0.35, 8)]),   # 8 contracts at best bid
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 3  # min(10, 3, 8) = 3, capped at 5 → 3


def test_signal_quantity_capped_by_max():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    engine.max_contracts_per_arb = 2
    orderbooks = {
        "M1": _ob([(0.40, 100)]),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 2


def test_signal_quantity_defaults_to_one():
    """Without max_contracts set, quantity defaults to 1."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": _ob([(0.40, 100)]),
        "M2": _ob([(0.35, 100)]),
        "M3": _ob([(0.35, 100)]),
    }
    signal = engine.evaluate("E1", orderbooks)
    assert signal is not None
    assert signal.quantity == 1
```

**Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_engine.py::test_signal_includes_quantity_from_depth tests/test_engine.py::test_signal_quantity_capped_by_max tests/test_engine.py::test_signal_quantity_defaults_to_one -v`

Expected: FAIL — `TradeSignal` has no `quantity` field, engine doesn't compute it.

**Step 3: Implement**

In `src/models.py`, add `quantity` to `TradeSignal`:

```python
@dataclass
class TradeSignal:
    event_ticker: str
    legs: list[tuple[str, float]]
    net_profit: float
    profit_pct: float
    exposure_ratio: float
    signal_type: str = "taker"
    quantity: int = 1
```

In `src/engine.py`, add `max_contracts_per_arb` to `__init__` and compute quantity in `evaluate`:

```python
def __init__(self, risk_profile: RiskProfile, maker_max_horizon_hours: float = 2.0,
             max_contracts_per_arb: int = 1):
    # ... existing code ...
    self.max_contracts_per_arb = max_contracts_per_arb
```

In `evaluate()`, after computing legs and before returning the signal, compute quantity:

```python
# After legs are validated and profit is confirmed:
depths = [orderbooks[ticker].yes_bid_depth_at(price) for ticker, price in legs]
quantity = min(int(min(depths)), self.max_contracts_per_arb)
quantity = max(quantity, 1)

return TradeSignal(
    event_ticker=event_ticker,
    legs=legs,
    net_profit=profit,
    profit_pct=profit_pct,
    exposure_ratio=exp_ratio,
    quantity=quantity,
)
```

In `src/config.py`, add `max_contracts_per_arb` to `Config`:

```python
max_contracts_per_arb: int
```

And in `load_config`:

```python
max_contracts_per_arb=int(strategy.get("max_contracts_per_arb", 1)),
```

In `src/main.py`, pass to engine:

```python
self.engine = ArbEngine(
    risk_profile=self.risk_profile,
    maker_max_horizon_hours=self.cfg.maker_max_horizon_hours,
    max_contracts_per_arb=self.cfg.max_contracts_per_arb,
)
```

In `_execute_and_track`, use signal.quantity:

```python
await self.executor.execute(signal, quantity=signal.quantity)
```

In `config.example.yaml`, add:

```yaml
  max_contracts_per_arb: 1        # Max contracts per arb execution (default: 1)
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/models.py src/engine.py src/config.py src/main.py config.example.yaml tests/test_engine.py
git commit -m "feat: dynamic quantity sizing based on bid depth"
```

---

### Task 12: Add open_interest and liquidity filtering

These fields are already parsed from the API but unused. Add optional thresholds to `RiskProfile`.

**Files:**
- Modify: `src/risk.py` (add fields + preset defaults)
- Modify: `src/config.py` (add to override_keys)
- Modify: `src/engine.py` (`_validate_legs`)
- Modify: `tests/test_engine.py`

**Step 1: Write failing test**

Add to `tests/test_engine.py`:

```python
def test_min_open_interest_rejects_low_oi():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_open_interest=100.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"open_interest": 50}, "M2": {"open_interest": 200}, "M3": {"open_interest": 200}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_min_liquidity_rejects_illiquid():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_liquidity=1000.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"liquidity": 500}, "M2": {"liquidity": 2000}, "M3": {"liquidity": 2000}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_zero_thresholds_accept_all():
    """Default 0 thresholds should not filter anything."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {"M1": {"open_interest": 0, "liquidity": 0},
            "M2": {"open_interest": 0, "liquidity": 0},
            "M3": {"open_interest": 0, "liquidity": 0}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None
```

**Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/test_engine.py::test_min_open_interest_rejects_low_oi tests/test_engine.py::test_min_liquidity_rejects_illiquid -v`

Expected: FAIL — `_make_engine` doesn't accept `min_open_interest` or `min_liquidity`; `RiskProfile` doesn't have those fields.

**Step 3: Implement**

In `src/risk.py`, add fields to `RiskProfile`:

```python
@dataclass
class RiskProfile:
    min_volume_24h: float
    min_bid_depth: int
    min_profit_pct: float
    require_recent_trades: bool
    max_exposure_ratio: float
    near_term_hours: float
    hurdle_rate_annual_pct: float
    unwind_phase1_secs: int
    unwind_phase2_secs: int
    unwind_price_step_cents: int
    min_open_interest: float = 0.0
    min_liquidity: float = 0.0
```

Add defaults to all presets:

```python
# In each preset dict, add:
"min_open_interest": 0.0,
"min_liquidity": 0.0,
```

In `src/config.py`, add to `override_keys`:

```python
override_keys = {
    "min_volume_24h", "min_bid_depth", "min_profit_pct",
    "require_recent_trades", "max_exposure_ratio",
    "near_term_hours", "hurdle_rate_annual_pct",
    "unwind_phase1_secs", "unwind_phase2_secs", "unwind_price_step_cents",
    "min_open_interest", "min_liquidity",
}
```

In `src/engine.py`, add to `__init__`:

```python
self.min_open_interest = risk_profile.min_open_interest
self.min_liquidity = risk_profile.min_liquidity
```

In `_validate_legs`, after the volume check, add:

```python
if self.min_open_interest > 0 and market_metadata:
    for ticker, _ in legs:
        meta = market_metadata.get(ticker, {})
        if meta.get("open_interest", 0) < self.min_open_interest:
            return None

if self.min_liquidity > 0 and market_metadata:
    for ticker, _ in legs:
        meta = market_metadata.get(ticker, {})
        if meta.get("liquidity", 0) < self.min_liquidity:
            return None
```

Update `_make_engine` helper in `tests/test_engine.py` to accept the new fields:

```python
def _make_engine(min_profit_pct=2.0, max_exposure_ratio=3.0, **kwargs):
    profile = RiskProfile(
        min_profit_pct=min_profit_pct,
        max_exposure_ratio=max_exposure_ratio,
        min_volume_24h=kwargs.get("min_volume_24h", 0),
        min_bid_depth=kwargs.get("min_bid_depth", 1),
        require_recent_trades=kwargs.get("require_recent_trades", False),
        near_term_hours=kwargs.get("near_term_hours", 24),
        hurdle_rate_annual_pct=kwargs.get("hurdle_rate_annual_pct", 10.0),
        unwind_phase1_secs=15,
        unwind_phase2_secs=30,
        unwind_price_step_cents=3,
        min_open_interest=kwargs.get("min_open_interest", 0.0),
        min_liquidity=kwargs.get("min_liquidity", 0.0),
    )
    return ArbEngine(
        risk_profile=profile,
        maker_max_horizon_hours=kwargs.get("maker_max_horizon_hours", 1.0),
    )
```

In `config.example.yaml`, add:

```yaml
  # min_open_interest: 0
  # min_liquidity: 0
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`

Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/risk.py src/config.py src/engine.py config.example.yaml tests/test_engine.py
git commit -m "feat: add open_interest and liquidity filtering thresholds"
```

---

## Summary

| Task | Phase | Description | Key files |
|------|-------|-------------|-----------|
| 1 | Reliability | Broaden API retry to 5xx + connection errors | `api.py`, `test_api.py` |
| 2 | Reliability | Per-event pending execution guard | `main.py`, `test_main.py` |
| 3 | Reliability | Periodic expired event cleanup | `main.py`, `test_main.py` |
| 4 | Reliability | Async orderbook callback via queue | `scanner.py`, `main.py` |
| 5 | Architecture | Extract `dispatch.py` | `dispatch.py`, `main.py` |
| 6 | Architecture | Extract `discovery.py` | `discovery.py`, `main.py` |
| 7 | Architecture | Pass RiskProfile directly | `maker.py`, `executor.py` |
| 8 | Architecture | Update CLAUDE.md | `CLAUDE.md` |
| 9 | Strategy | Fix PositionTracker for closes | `positions.py` |
| 10 | Strategy | Accurate PnL reporting | `main.py` |
| 11 | Strategy | Dynamic quantity sizing | `engine.py`, `models.py`, `config.py` |
| 12 | Strategy | Open interest + liquidity filters | `risk.py`, `engine.py`, `config.py` |
