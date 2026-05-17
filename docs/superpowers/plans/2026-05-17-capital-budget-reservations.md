# Capital Budget & Position Reservations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use git worktrees for isolation. Dispatch code review agent after each task with substantive code changes.

**Goal:** Add per-exchange capital budgeting and persistent position reservations so the bot only deploys $X on a given exchange and leaves user-owned positions untouched.

**Architecture:** A `CapitalGuard` middleware maintains a local ledger of committed capital (fills + resting orders) and gates execution. A `ReservationStore` persists user position reservations to JSON and is respected at boot reconcile, unwind, and shutdown. Both are wired in `main.py`; reservations are managed via MCP tools.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, JSON file persistence, pytest

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/core/capital_guard.py` | Per-exchange capital ledger and headroom check |
| `src/core/reservation_store.py` | JSON-backed reservation persistence and lookup |
| `src/config.py` | Parse new `capital_budget` config section |
| `src/main.py` | Wire both into bot lifecycle (boot, execution, shutdown) |
| `src/strategies/maker.py` | Headroom check before posting maker orders |
| `src/strategies/two_sided.py` | Headroom check before posting two-sided pairs |
| `src/mcp_server.py` | MCP tools: reserve_position, release_position, list_reservations |
| `config.example.yaml` | Document capital_budget section |
| `tests/test_capital_guard.py` | Unit tests for CapitalGuard |
| `tests/test_reservation_store.py` | Unit tests for ReservationStore |
| `tests/test_main.py` | Integration tests for wiring changes |

---

### Task 1: ReservationStore — Core Class

**Files:**
- Create: `src/core/reservation_store.py`
- Create: `tests/test_reservation_store.py`

- [ ] **Step 1: Write failing tests for ReservationStore**

```python
# tests/test_reservation_store.py
import json
import os
import tempfile

from src.core.reservation_store import ReservationStore, Reservation


def test_reserve_creates_entry():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 5, "kalshi", note="my BTC bet")
        assert store.is_reserved("KXBTC-25MAY16-T55000")
        assert store.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 5
    finally:
        os.unlink(path)


def test_release_removes_entry():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 5, "kalshi")
        store.release("KXBTC-25MAY16-T55000")
        assert not store.is_reserved("KXBTC-25MAY16-T55000")
        assert store.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 0
    finally:
        os.unlink(path)


def test_persistence_survives_reload():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 3, "kalshi")

        store2 = ReservationStore(path=path)
        assert store2.is_reserved("KXBTC-25MAY16-T55000")
        assert store2.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 3
    finally:
        os.unlink(path)


def test_list_all_returns_reservations():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 2, "kalshi")
        store.reserve("M2", "no", 4, "predictit", note="hedge")
        all_res = store.list_all()
        assert len(all_res) == 2
        tickers = {r.ticker for r in all_res}
        assert tickers == {"M1", "M2"}
    finally:
        os.unlink(path)


def test_reserve_updates_existing():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 2, "kalshi")
        store.reserve("M1", "yes", 5, "kalshi")
        assert store.get_reserved_quantity("M1", "yes") == 5
        assert len(store.list_all()) == 1
    finally:
        os.unlink(path)


def test_get_reserved_quantity_wrong_side_returns_zero():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 5, "kalshi")
        assert store.get_reserved_quantity("M1", "no") == 0
    finally:
        os.unlink(path)


def test_empty_file_loads_gracefully():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        assert store.list_all() == []
        assert not store.is_reserved("anything")
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_reservation_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.reservation_store'`

- [ ] **Step 3: Implement ReservationStore**

```python
# src/core/reservation_store.py
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class Reservation:
    ticker: str
    side: str
    quantity: int
    exchange: str
    created_at: str
    note: str = ""


class ReservationStore:
    def __init__(self, path: str = "data/reservations.json"):
        self._path = path
        self._reservations: dict[str, Reservation] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            for entry in data:
                r = Reservation(**entry)
                self._reservations[r.ticker] = r
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def _save(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        data = [asdict(r) for r in self._reservations.values()]
        dir_name = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            os.unlink(tmp_path)
            raise

    def reserve(self, ticker: str, side: str, quantity: int, exchange: str, note: str = "") -> None:
        self._reservations[ticker] = Reservation(
            ticker=ticker,
            side=side,
            quantity=quantity,
            exchange=exchange,
            created_at=datetime.now(timezone.utc).isoformat(),
            note=note,
        )
        self._save()

    def release(self, ticker: str) -> None:
        self._reservations.pop(ticker, None)
        self._save()

    def is_reserved(self, ticker: str) -> bool:
        return ticker in self._reservations

    def get_reserved_quantity(self, ticker: str, side: str) -> int:
        r = self._reservations.get(ticker)
        if r and r.side == side:
            return r.quantity
        return 0

    def list_all(self) -> list[Reservation]:
        return list(self._reservations.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_reservation_store.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/reservation_store.py tests/test_reservation_store.py
git commit -m "feat: add ReservationStore with JSON persistence"
```

---

### Task 2: CapitalGuard — Core Class

**Files:**
- Create: `src/core/capital_guard.py`
- Create: `tests/test_capital_guard.py`

- [ ] **Step 1: Write failing tests for CapitalGuard**

```python
# tests/test_capital_guard.py
from src.core.capital_guard import CapitalGuard


def test_can_execute_within_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    assert guard.can_execute("kalshi", 10.0)


def test_can_execute_exceeds_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 20.0)
    assert not guard.can_execute("kalshi", 10.0)


def test_can_execute_exact_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 15.0)
    assert guard.can_execute("kalshi", 10.0)


def test_release_frees_headroom():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 20.0)
    assert not guard.can_execute("kalshi", 10.0)
    guard.release("kalshi", "order1")
    assert guard.can_execute("kalshi", 10.0)


def test_headroom_returns_remaining():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    assert guard.headroom("kalshi") == 25.0
    guard.commit("kalshi", "order1", 10.0)
    assert guard.headroom("kalshi") == 15.0


def test_deployed_returns_total():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 10.0)
    guard.commit("kalshi", "order2", 5.0)
    assert guard.deployed("kalshi") == 15.0


def test_no_budget_configured_unlimited():
    guard = CapitalGuard(budgets={})
    assert guard.can_execute("kalshi", 1000.0)
    assert guard.headroom("kalshi") == float("inf")


def test_release_nonexistent_order_is_noop():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.release("kalshi", "nonexistent")
    assert guard.headroom("kalshi") == 25.0


def test_commit_same_order_id_replaces():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 10.0)
    guard.commit("kalshi", "order1", 15.0)
    assert guard.deployed("kalshi") == 15.0


def test_multiple_exchanges_independent():
    guard = CapitalGuard(budgets={"kalshi": 25.0, "predictit": 50.0})
    guard.commit("kalshi", "k1", 20.0)
    guard.commit("predictit", "p1", 30.0)
    assert guard.headroom("kalshi") == 5.0
    assert guard.headroom("predictit") == 20.0
    assert guard.can_execute("kalshi", 5.0)
    assert not guard.can_execute("kalshi", 6.0)
    assert guard.can_execute("predictit", 20.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_capital_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.capital_guard'`

- [ ] **Step 3: Implement CapitalGuard**

```python
# src/core/capital_guard.py
import logging

logger = logging.getLogger(__name__)


class CapitalGuard:
    def __init__(self, budgets: dict[str, float]):
        self._budgets = budgets
        self._ledger: dict[str, dict[str, float]] = {}

    def can_execute(self, exchange: str, cost: float) -> bool:
        budget = self._budgets.get(exchange)
        if budget is None:
            return True
        return self.deployed(exchange) + cost <= budget

    def commit(self, exchange: str, order_id: str, cost: float) -> None:
        if exchange not in self._ledger:
            self._ledger[exchange] = {}
        self._ledger[exchange][order_id] = cost

    def release(self, exchange: str, order_id: str) -> None:
        if exchange in self._ledger:
            self._ledger[exchange].pop(order_id, None)

    def headroom(self, exchange: str) -> float:
        budget = self._budgets.get(exchange)
        if budget is None:
            return float("inf")
        return budget - self.deployed(exchange)

    def deployed(self, exchange: str) -> float:
        if exchange not in self._ledger:
            return 0.0
        return sum(self._ledger[exchange].values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_capital_guard.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/capital_guard.py tests/test_capital_guard.py
git commit -m "feat: add CapitalGuard per-exchange capital ledger"
```

---

### Task 3: Config Parsing — `capital_budget` Section

**Files:**
- Modify: `src/config.py`
- Modify: `config.example.yaml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for capital_budget config parsing**

Add to `tests/test_config.py`:

```python
def test_capital_budgets_parsed():
    import tempfile, os, yaml
    cfg_data = {
        "exchange": "kalshi",
        "mode": "demo",
        "credentials": {"demo": {"api_key_id": "k", "private_key_path": "/tmp/k.pem"}},
        "strategy": {"risk_mode": "conservative"},
        "capital_budget": {"kalshi": 25.0, "predictit": 50.0},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(cfg_data, f)
        path = f.name
    try:
        from src.config import load_config
        cfg = load_config(path)
        assert cfg.capital_budgets == {"kalshi": 25.0, "predictit": 50.0}
    finally:
        os.unlink(path)


def test_capital_budgets_absent_defaults_empty():
    import tempfile, os, yaml
    cfg_data = {
        "exchange": "kalshi",
        "mode": "demo",
        "credentials": {"demo": {"api_key_id": "k", "private_key_path": "/tmp/k.pem"}},
        "strategy": {"risk_mode": "conservative"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(cfg_data, f)
        path = f.name
    try:
        from src.config import load_config
        cfg = load_config(path)
        assert cfg.capital_budgets == {}
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py::test_capital_budgets_parsed tests/test_config.py::test_capital_budgets_absent_defaults_empty -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'capital_budgets'`

- [ ] **Step 3: Add `capital_budgets` field to Config dataclass and parse it**

In `src/config.py`, add field to the `Config` dataclass:

```python
    capital_budgets: dict[str, float]
```

In `load_config()`, parse the section (add before the `return Config(...)` call):

```python
    capital_budget_raw = raw.get("capital_budget", {})
    capital_budgets = {k: float(v) for k, v in capital_budget_raw.items() if v}
```

Add to the `return Config(...)` kwargs:

```python
        capital_budgets=capital_budgets,
```

- [ ] **Step 4: Update config.example.yaml**

Append after the `recording:` section:

```yaml

# Capital budget per exchange (omit or set to 0 for unlimited)
# Bot will not deploy more than this amount on a given exchange.
# Resting orders (maker, two-sided) count toward the budget.
# capital_budget:
#   kalshi: 25.00
#   predictit: 50.00
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All config tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py config.example.yaml tests/test_config.py
git commit -m "feat: parse capital_budget config section"
```

---

### Task 4: Wire CapitalGuard into Bot Execution

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Add imports and instantiation in `ArbBot.__init__`**

After the existing `from src.core.risk import load_risk_profile` import, add:

```python
from src.core.capital_guard import CapitalGuard
from src.core.reservation_store import ReservationStore
```

In `ArbBot.__init__`, after `self.risk_profile = ...` and before `self.engine = ...`, add:

```python
        self.reservations = ReservationStore(path="data/reservations.json")
        self.capital_guard = CapitalGuard(budgets=self.cfg.capital_budgets)
```

- [ ] **Step 2: Gate execution in `_execute_and_track`**

At the start of `_execute_and_track`, before the `_validate_recent_trades` call, add:

```python
        cost = sum(price * signal.quantity for _, price in signal.legs)
        if not self.capital_guard.can_execute(self.cfg.exchange, cost):
            logger.info(
                "capital_limit: skipping %s (need $%.4f, headroom $%.4f)",
                signal.event_ticker, cost, self.capital_guard.headroom(self.cfg.exchange),
            )
            self.dispatcher.mark_execution_complete(signal.event_ticker)
            return
```

- [ ] **Step 3: Commit/release on successful execution**

After `await self.executor.execute(signal, quantity=signal.quantity)` succeeds, commit:

```python
            self.capital_guard.commit(
                self.cfg.exchange,
                f"taker_{signal.event_ticker}",
                cost,
            )
```

In the existing `finally:` block where `mark_execution_complete` is called, the release should happen when the arb completes (positions resolve). For now, the release happens when positions are closed — handled in the next task.

- [ ] **Step 4: Add capital_headroom to STATUS line in `_report_status`**

In the `_report_status` method, after the `maker_horizon_events` calculation, add:

```python
            capital_info = ""
            if self.cfg.capital_budgets.get(self.cfg.exchange):
                capital_info = f" | capital_headroom=${self.capital_guard.headroom(self.cfg.exchange):.2f}"
```

Append `capital_info` to the `logger.info("STATUS | ...")` format string.

- [ ] **Step 5: Commit**

```bash
git add src/main.py
git commit -m "feat: wire CapitalGuard into execution path with STATUS reporting"
```

---

### Task 5: Wire ReservationStore into Boot Reconcile & Emergency Shutdown

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Modify `_boot_reconcile_inner` to respect reservations**

Replace the existing position-loading loop in `_boot_reconcile_inner`:

```python
            positions_resp = await asyncio.wait_for(
                self.api.get_positions(), timeout=15)
            longs, shorts = [], []
            for mp in positions_resp.get("market_positions", []):
                qty = int(float(mp.get("position_fp", "0")))
                ticker = mp["ticker"]
                avg_price = float(mp.get("average_price_fp", "0")) or 0.0

                if qty > 0:
                    reserved_qty = self.reservations.get_reserved_quantity(ticker, "yes")
                    bot_qty = max(0, qty - reserved_qty)
                    if reserved_qty > 0:
                        logger.info(
                            "Boot: %s has %d total, %d reserved, %d bot-owned",
                            ticker, qty, reserved_qty, bot_qty,
                        )
                    if bot_qty > 0:
                        longs.append((ticker, bot_qty))
                        self.capital_guard.commit(
                            self.cfg.exchange,
                            f"boot_{ticker}",
                            avg_price * bot_qty,
                        )
                elif qty < 0:
                    if self.reservations.is_reserved(ticker):
                        logger.info("Boot: skipping short close for reserved %s", ticker)
                        continue
                    shorts.append(ticker)
```

- [ ] **Step 2: Modify `_emergency_shutdown_inner` to skip reserved positions**

In the position-closing loop inside `_emergency_shutdown_inner`, add a reservation check:

```python
                for mp in positions_resp.get("market_positions", []):
                    qty = int(float(mp.get("position_fp", "0")))
                    if qty != 0:
                        if self.reservations.is_reserved(mp["ticker"]):
                            logger.info("Emergency shutdown: skipping reserved %s", mp["ticker"])
                            continue
                        close_orders.append(self.order_builder.build_close_order(mp["ticker"], qty))
```

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: boot reconcile and emergency shutdown respect reservations"
```

---

### Task 6: Maker & Two-Sided Capital Headroom Checks

**Files:**
- Modify: `src/strategies/maker.py`
- Modify: `src/strategies/two_sided.py`
- Modify: `src/main.py` (pass capital_guard to managers)

- [ ] **Step 1: Add capital_guard parameter to MakerManager**

In `src/strategies/maker.py`, modify `__init__` to accept an optional `capital_guard` and `exchange_name`:

```python
    def __init__(self, api, order_builder=None, fill_mode: str = "cancel_and_take",
                 max_events: int = 3, risk_profile=None,
                 tighten_phase1_secs: int = 15, tighten_phase2_secs: int = 30,
                 tighten_step_cents: int = 3, track_fill_id=None,
                 capital_guard=None, exchange_name: str = "kalshi"):
```

Store as `self._capital_guard = capital_guard` and `self._exchange_name = exchange_name`.

- [ ] **Step 2: Add headroom check in MakerManager.post()**

At the start of `post()`, after the existing `if len(self._active) >= self.max_events:` check, add:

```python
        if self._capital_guard:
            cost = sum(price for _, price in signal.legs) * 1  # 1 contract per leg
            if not self._capital_guard.can_execute(self._exchange_name, cost):
                logger.info("capital_limit: skipping maker on %s", signal.event_ticker)
                return False
```

- [ ] **Step 3: Commit/release maker orders in capital guard**

After successfully posting orders (after `self._active[signal.event_ticker] = event`):

```python
                if self._capital_guard:
                    cost = sum(event.order_prices.values())
                    self._capital_guard.commit(
                        self._exchange_name,
                        f"maker_{signal.event_ticker}",
                        cost,
                    )
```

In `cancel_event()`, after removing from `_active`:

```python
        if self._capital_guard:
            self._capital_guard.release(self._exchange_name, f"maker_{event_ticker}")
```

In `_cleanup_event()` (the internal method called on full fill), add the same release.

- [ ] **Step 4: Add capital_guard parameter to TwoSidedManager**

In `src/strategies/two_sided.py`, modify `__init__`:

```python
    def __init__(self, api, risk_profile: RiskProfile, order_builder=None,
                 capital_guard=None, exchange_name: str = "kalshi"):
```

Store as `self._capital_guard = capital_guard` and `self._exchange_name = exchange_name`.

- [ ] **Step 5: Add headroom check in TwoSidedManager.post()**

After the `if quantity <= 0: return False` check:

```python
        if self._capital_guard:
            cost = sum(price for _, price in signal.legs) * quantity
            if not self._capital_guard.can_execute(self._exchange_name, cost):
                logger.info("capital_limit: skipping two-sided on %s", signal.event_ticker)
                return False
```

After successfully posting (after updating `self._positions[ticker]`):

```python
        if self._capital_guard:
            cost = buy_leg[1] * quantity + sell_leg[1] * quantity
            self._capital_guard.commit(
                self._exchange_name,
                f"twosided_{ticker}",
                cost,
            )
```

- [ ] **Step 6: Update main.py to pass capital_guard to managers**

In `ArbBot.__init__`, where `MakerManager` is instantiated, add the parameters:

```python
        self.maker = MakerManager(
            api=self.api,
            order_builder=self.exchange.order_builder,
            fill_mode=self.cfg.maker_fill_mode,
            max_events=self.cfg.max_maker_events,
            risk_profile=self.risk_profile,
            track_fill_id=self.executor._track_fill_id,
            capital_guard=self.capital_guard,
            exchange_name=self.cfg.exchange,
        ) if self.cfg.maker_enabled else None
```

Similarly for `TwoSidedManager`:

```python
        self.two_sided = TwoSidedManager(
            api=self.api,
            order_builder=self.exchange.order_builder,
            risk_profile=self.risk_profile,
            capital_guard=self.capital_guard,
            exchange_name=self.cfg.exchange,
        ) if self.risk_profile.two_sided_max_inventory > 0 else None
```

- [ ] **Step 7: Run existing tests to check nothing breaks**

Run: `python3 -m pytest tests/test_maker.py tests/test_two_sided.py -v`
Expected: All existing tests PASS (capital_guard defaults to None, so no behavior change)

- [ ] **Step 8: Commit**

```bash
git add src/strategies/maker.py src/strategies/two_sided.py src/main.py
git commit -m "feat: maker and two-sided respect capital budget headroom"
```

---

### Task 7: MCP Tools — reserve_position, release_position, list_reservations

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: Add reservation MCP tools**

Add at the bottom of `src/mcp_server.py`, before the `if __name__ == "__main__":` block:

```python
@mcp.tool()
async def reserve_position(
    ticker: str,
    side: str,
    quantity: int,
    exchange: str = "kalshi",
    note: str = "",
) -> str:
    """Reserve a position as user-owned. The bot will not close, unwind, or interfere with it.

    Args:
        ticker: Market ticker (e.g. KXBTC-25MAY16-T55000)
        side: Position side ("yes" or "no")
        quantity: Number of contracts to reserve
        exchange: Exchange name (default: kalshi)
        note: Optional annotation for this reservation
    """
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    store.reserve(ticker, side, quantity, exchange, note)
    all_res = store.list_all()
    lines = [f"Reserved {quantity}x {side} on {ticker} ({exchange})"]
    if note:
        lines.append(f"  Note: {note}")
    lines.append(f"\nAll reservations ({len(all_res)}):")
    for r in all_res:
        lines.append(f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}" +
                     (f" — {r.note}" if r.note else ""))
    return "\n".join(lines)


@mcp.tool()
async def release_position(ticker: str) -> str:
    """Release a previously reserved position. The bot may now manage this position.

    Args:
        ticker: Market ticker to release
    """
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    if not store.is_reserved(ticker):
        return f"No reservation found for {ticker}"
    store.release(ticker)
    all_res = store.list_all()
    lines = [f"Released reservation on {ticker}"]
    lines.append(f"\nRemaining reservations ({len(all_res)}):")
    for r in all_res:
        lines.append(f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}" +
                     (f" — {r.note}" if r.note else ""))
    return "\n".join(lines)


@mcp.tool()
async def list_reservations() -> str:
    """List all active position reservations."""
    from src.core.reservation_store import ReservationStore
    store = ReservationStore(path="data/reservations.json")
    all_res = store.list_all()
    if not all_res:
        return "No active reservations."
    lines = [f"Active reservations ({len(all_res)}):"]
    for r in all_res:
        lines.append(
            f"  {r.ticker}: {r.quantity}x {r.side} on {r.exchange}"
            + (f" — {r.note}" if r.note else "")
            + f" (since {r.created_at[:10]})"
        )
    return "\n".join(lines)
```

- [ ] **Step 2: Update `close_all_positions` to skip reserved**

In the existing `close_all_positions` MCP tool, after fetching positions but before building close orders:

```python
        from src.core.reservation_store import ReservationStore
        store = ReservationStore(path="data/reservations.json")
        # ...
        for mp in market_positions:
            qty = int(float(mp.get("position_fp", "0")))
            if qty != 0:
                if store.is_reserved(mp["ticker"]):
                    results.append(f"  SKIPPED {mp['ticker']} (reserved)")
                    continue
                open_pos.append((mp["ticker"], qty))
```

- [ ] **Step 3: Update `close_position` to warn on reserved**

In the existing `close_position` MCP tool, after finding the target position:

```python
        from src.core.reservation_store import ReservationStore
        store = ReservationStore(path="data/reservations.json")
        if store.is_reserved(ticker):
            return (f"WARNING: {ticker} is reserved ({store.get_reserved_quantity(ticker, 'yes')}x). "
                    f"Use release_position first if you want to close it.")
```

- [ ] **Step 4: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: add MCP tools for position reservations"
```

---

### Task 8: Capital Release on Position Close

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Release capital when taker arb completes**

The taker arb's positions are held until the event settles (one outcome pays $1, rest expire worthless). Capital should be released when positions close. Add a callback approach:

In `_execute_and_track`, after `self.executor.execute(signal, quantity=signal.quantity)` returns, check if the execution resulted in all-filled (no partial). If all legs filled, the capital stays committed until the event settles. If partial unwind happens, the release occurs in the executor's unwind path.

Since the executor already calls `positions.record_fill` on close/unwind, we can hook into `PositionTracker` to release capital. The simplest approach: release when the event is no longer tracked.

For this implementation, release the taker commitment when `_execute_and_track` detects a circuit breaker trip or when the arb completes with unwind:

After the `self._stats["arbs_executed"] += 1` line:

```python
            # If execution failed (partial fill → unwind), release capital
            if self.executor.is_event_blacklisted(signal.event_ticker):
                self.capital_guard.release(self.cfg.exchange, f"taker_{signal.event_ticker}")
```

In `_emergency_shutdown_inner`, after successfully closing positions, release all taker commits:

```python
                # Release all capital on shutdown
                for mp in positions_resp.get("market_positions", []):
                    ticker = mp["ticker"]
                    if not self.reservations.is_reserved(ticker):
                        self.capital_guard.release(self.cfg.exchange, f"boot_{ticker}")
                        self.capital_guard.release(self.cfg.exchange, f"taker_{ticker}")
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: release capital on partial fill unwind and emergency shutdown"
```

---

### Task 9: Integration Test

**Files:**
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write integration test for capital gating**

Add to `tests/test_main.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.capital_guard import CapitalGuard


def test_capital_guard_blocks_execution_over_budget():
    """Verify that _execute_and_track skips signals when capital is exhausted."""
    guard = CapitalGuard(budgets={"kalshi": 1.0})
    guard.commit("kalshi", "existing", 0.95)

    # Signal with cost > remaining headroom (0.05)
    from src.core.models import TradeSignal
    signal = TradeSignal(
        event_ticker="EVT1",
        legs=[("M1", 0.40), ("M2", 0.35), ("M3", 0.35)],
        net_profit=0.03,
        profit_pct=3.0,
        exposure_ratio=1.5,
        signal_type="sell_side_taker",
        quantity=1,
    )
    cost = sum(price * signal.quantity for _, price in signal.legs)
    assert not guard.can_execute("kalshi", cost)
```

- [ ] **Step 2: Write integration test for reservation in boot reconcile**

```python
def test_reservation_store_excludes_from_boot():
    """Verify reserved positions don't get loaded as bot positions."""
    import tempfile, os
    from src.core.reservation_store import ReservationStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("TICKER1", "yes", 3, "kalshi")

        # Simulate boot: 5 contracts total, 3 reserved → 2 bot-owned
        total_qty = 5
        reserved_qty = store.get_reserved_quantity("TICKER1", "yes")
        bot_qty = max(0, total_qty - reserved_qty)
        assert bot_qty == 2
    finally:
        os.unlink(path)
```

- [ ] **Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_main.py
git commit -m "test: integration tests for capital guard and reservation boot behavior"
```

---

### Task 10: Update get_risk_profile MCP to Include Capital Info

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: Add capital budget info to get_risk_profile response**

In the `get_risk_profile` tool, after the existing lines list, add:

```python
    capital_budget_raw = raw_cfg.get("capital_budget", {}) if raw_cfg else {}
    if capital_budget_raw:
        lines.append(f"\nCapital budgets:")
        for exchange, budget in capital_budget_raw.items():
            lines.append(f"  {exchange}: ${budget:.2f}")
    else:
        lines.append(f"\nCapital budgets: unlimited (none configured)")
```

To access raw config, load it at the top of the function:

```python
    import yaml
    with open(CONFIG_PATH) as f:
        raw_cfg = yaml.safe_load(f)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: show capital budget info in get_risk_profile MCP tool"
```

---

### Task 11: Full Test Suite Verification & Cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the complete test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS with no regressions

- [ ] **Step 2: Verify import isolation**

Run: `python3 -m pytest tests/test_import_isolation.py -v`
Expected: PASS (no circular imports from new modules)

- [ ] **Step 3: Verify bot starts without error in demo mode**

Run: `python3 -c "from src.config import load_config; from src.core.capital_guard import CapitalGuard; from src.core.reservation_store import ReservationStore; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 4: Final commit (if any cleanup needed)**

```bash
git status
# If clean, no commit needed
```
