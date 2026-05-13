# Intra-Kalshi Strategy Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add four new intra-Kalshi strategies — buy-side structural arb, near-expiry stale order harvesting, monotone constraint arb, and two-sided market making — all calibrated per risk profile.

**Architecture:** All strategies extend `ArbEngine` with new `evaluate_*` methods and route through `Dispatcher` to the existing `ExecutionManager`. `Orderbook` gains ask-side methods (derived from `no_bids` which are already tracked). `TradeSignal` gains an optional `leg_actions` field to support mixed buy/sell legs. Risk profiles gain per-strategy threshold fields.

**Tech Stack:** Python 3.11 asyncio, pytest, existing `src/` module tree. Run tests with `python3 -m pytest tests/ -v`.

---

## Phase 1: Buy-Side Structural Arb

Signal: `sum(yes_asks) < $1 - fees` across all outcomes of a mutually exclusive event. Mirror of existing taker arb.

---

### Task 1: Orderbook ask-side methods

**Files:**
- Modify: `src/models.py:10-24`
- Test: `tests/test_models.py`

**Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
from src.models import Orderbook

def test_best_yes_ask_from_no_bids():
    book = Orderbook(yes_bids={}, no_bids={60: 10.0})
    assert book.best_yes_ask() == 0.40

def test_best_yes_ask_returns_none_when_no_no_bids():
    book = Orderbook(yes_bids={40: 10.0}, no_bids={})
    assert book.best_yes_ask() is None

def test_best_yes_ask_uses_highest_no_bid():
    # Highest NO bid = 70¢ → YES ask = 30¢
    book = Orderbook(yes_bids={}, no_bids={60: 5.0, 70: 3.0})
    assert book.best_yes_ask() == 0.30

def test_yes_ask_depth_at_sums_matching_no_bids():
    # YES ask 40¢ → need NO bids at 60¢ or higher
    book = Orderbook(yes_bids={}, no_bids={60: 5.0, 65: 3.0, 50: 10.0})
    assert book.yes_ask_depth_at(0.40) == 8.0  # 5 + 3 (not 50¢ NO bid)

def test_yes_ask_depth_at_returns_zero_when_no_match():
    book = Orderbook(yes_bids={}, no_bids={30: 10.0})
    assert book.yes_ask_depth_at(0.40) == 0.0
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_models.py::test_best_yes_ask_from_no_bids -v
```
Expected: `AttributeError: 'Orderbook' object has no attribute 'best_yes_ask'`

**Step 3: Add methods to Orderbook**

In `src/models.py`, after `yes_bid_depth_at` (line 24), add:

```python
def best_yes_ask(self) -> float | None:
    if not self.no_bids:
        return None
    return 1.0 - max(self.no_bids) / 100.0

def yes_ask_depth_at(self, price: float) -> float:
    no_price_cents = round((1.0 - price) * 100)
    return sum(qty for cents, qty in self.no_bids.items() if cents >= no_price_cents)
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_models.py -v
```
Expected: all pass.

**Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add best_yes_ask() and yes_ask_depth_at() to Orderbook"
```

---

### Task 2: buy_side_arb_profit() in fees.py

**Files:**
- Modify: `src/fees.py`
- Test: `tests/test_fees.py`

**Step 1: Write the failing test**

Add to `tests/test_fees.py`:

```python
from src.fees import buy_side_arb_profit

def test_buy_side_profit_positive_when_sum_below_one():
    # 3 legs at 30¢ each = 90¢ total, fees = 3 * 0.07 * 0.30 * 0.70 = 0.0441
    # profit = 1.0 - 0.90 - 0.0441 = 0.0559
    profit = buy_side_arb_profit([0.30, 0.30, 0.30])
    assert profit > 0

def test_buy_side_profit_negative_when_sum_above_one():
    profit = buy_side_arb_profit([0.40, 0.40, 0.40])
    assert profit < 0

def test_buy_side_profit_exact():
    asks = [0.30, 0.30, 0.30]
    from src.fees import taker_fee
    expected = 1.0 - sum(asks) - sum(taker_fee(p) for p in asks)
    assert abs(buy_side_arb_profit(asks) - expected) < 1e-9
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_fees.py::test_buy_side_profit_positive_when_sum_below_one -v
```
Expected: `ImportError: cannot import name 'buy_side_arb_profit'`

**Step 3: Add to fees.py**

Append to `src/fees.py`:

```python
def buy_side_arb_profit(ask_prices: list[float]) -> float:
    """Per-contract net profit from buying all outcomes at ask prices after taker fees."""
    gross = 1.0 - sum(ask_prices)
    fees = sum(taker_fee(p) for p in ask_prices)
    return gross - fees
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_fees.py -v
```

**Step 5: Commit**

```bash
git add src/fees.py tests/test_fees.py
git commit -m "feat: add buy_side_arb_profit() to fees"
```

---

### Task 3: api.build_buy_order() and executor leg_actions support

**Files:**
- Modify: `src/api.py:146-154`
- Modify: `src/models.py` (TradeSignal)
- Modify: `src/executor.py:50-54`
- Test: `tests/test_executor.py`

**Step 1: Write the failing tests**

Add to `tests/test_executor.py`:

```python
def test_build_orders_sell_by_default():
    # existing behavior unchanged
    ...  # check existing test still passes

def test_build_orders_buy_when_leg_action_is_buy():
    from src.models import TradeSignal
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.35), ("M2", 0.40)],
        net_profit=0.05,
        profit_pct=5.0,
        exposure_ratio=1.0,
        signal_type="buy_side_taker",
        leg_actions=["buy", "buy"],
    )
    # need an executor with a mock api
    from unittest.mock import MagicMock
    api = MagicMock()
    api.build_buy_order.return_value = {"action": "buy"}
    api.build_sell_order.return_value = {"action": "sell"}
    from src.executor import ExecutionManager
    from src.positions import PositionTracker
    from src.risk import load_risk_profile
    executor = ExecutionManager(api=api, positions=PositionTracker(),
                                fill_timeout_secs=10,
                                risk_profile=load_risk_profile("aggressive", {}))
    orders = executor.build_orders(signal, quantity=1)
    assert api.build_buy_order.call_count == 2
    assert api.build_sell_order.call_count == 0

def test_build_orders_mixed_actions():
    from src.models import TradeSignal
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.60), ("M2", 0.35)],
        net_profit=0.03,
        profit_pct=3.0,
        exposure_ratio=0.0,
        signal_type="monotone",
        leg_actions=["sell", "buy"],
    )
    from unittest.mock import MagicMock
    api = MagicMock()
    api.build_buy_order.return_value = {"action": "buy"}
    api.build_sell_order.return_value = {"action": "sell"}
    from src.executor import ExecutionManager
    from src.positions import PositionTracker
    from src.risk import load_risk_profile
    executor = ExecutionManager(api=api, positions=PositionTracker(),
                                fill_timeout_secs=10,
                                risk_profile=load_risk_profile("aggressive", {}))
    orders = executor.build_orders(signal, quantity=1)
    assert api.build_sell_order.call_count == 1
    assert api.build_buy_order.call_count == 1
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_executor.py::test_build_orders_buy_when_leg_action_is_buy -v
```
Expected: AttributeError on `leg_actions`.

**Step 3: Implement**

In `src/models.py`, add field to `TradeSignal` (after `quantity: int = 1`):

```python
leg_actions: list[str] | None = None  # None means all legs are "sell"
```

In `src/api.py`, add after `build_sell_order` (after line 154):

```python
def build_buy_order(self, ticker: str, yes_price: float, quantity: int) -> dict:
    return {
        "ticker": ticker,
        "action": "buy",
        "side": "yes",
        "type": "limit",
        "yes_price": round(yes_price * 100),
        "count": quantity,
    }
```

In `src/executor.py`, replace `build_orders` (lines 50-54):

```python
def build_orders(self, signal: TradeSignal, quantity: int) -> list[dict]:
    orders = []
    for i, (ticker, price) in enumerate(signal.legs):
        action = signal.leg_actions[i] if signal.leg_actions else "sell"
        if action == "buy":
            orders.append(self.api.build_buy_order(ticker=ticker, yes_price=price, quantity=quantity))
        else:
            orders.append(self.api.build_sell_order(ticker=ticker, yes_price=price, quantity=quantity))
    return orders
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/ -v
```
Expected: all pass (existing tests unaffected since `leg_actions=None` preserves old behavior).

**Step 5: Commit**

```bash
git add src/models.py src/api.py src/executor.py tests/test_executor.py
git commit -m "feat: add build_buy_order() and leg_actions support for mixed buy/sell signals"
```

---

### Task 4: engine.evaluate_buy_side()

**Files:**
- Modify: `src/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def test_evaluate_buy_side_profitable():
    # YES ask = 1 - NO bid. 3 legs: NO bids at 72¢ each → YES asks at 28¢ → sum=84¢ < $1
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is not None
    assert signal.signal_type == "buy_side_taker"
    assert signal.net_profit > 0
    assert all(a == "buy" for a in signal.leg_actions)

def test_evaluate_buy_side_no_signal_when_sum_above_one():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={65: 100}),  # ask = 35¢
        "M2": Orderbook(yes_bids={}, no_bids={65: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={65: 100}),  # sum = 105¢ > $1
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None

def test_evaluate_buy_side_returns_none_when_no_ask():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={40: 100}, no_bids={}),  # no NO bids → no ask
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None

def test_evaluate_buy_side_respects_min_profit_pct():
    engine = _make_engine(min_profit_pct=50.0, max_exposure_ratio=10.0)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None

def test_evaluate_buy_side_respects_depth():
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0, min_bid_depth=50)
    orderbooks = {
        "M1": Orderbook(yes_bids={}, no_bids={72: 5}),  # thin
        "M2": Orderbook(yes_bids={}, no_bids={72: 100}),
        "M3": Orderbook(yes_bids={}, no_bids={72: 100}),
    }
    signal = engine.evaluate_buy_side("E1", orderbooks)
    assert signal is None
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_engine.py::test_evaluate_buy_side_profitable -v
```
Expected: `AttributeError: 'ArbEngine' object has no attribute 'evaluate_buy_side'`

**Step 3: Add evaluate_buy_side() to engine.py**

Add after `evaluate_maker` in `src/engine.py`:

```python
def evaluate_buy_side(
    self,
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None = None,
) -> TradeSignal | None:
    from src.fees import buy_side_arb_profit
    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        best_ask = book.best_yes_ask()
        if best_ask is None:
            return None
        legs.append((ticker, best_ask))

    ask_prices = [price for _, price in legs]

    if self.min_bid_depth > 1:
        for ticker, ask_price in legs:
            if orderbooks[ticker].yes_ask_depth_at(ask_price) < self.min_bid_depth:
                return None

    if self.min_volume_24h > 0 and market_metadata:
        for ticker, _ in legs:
            if market_metadata.get(ticker, {}).get("volume_24h", 0) < self.min_volume_24h:
                return None

    profit = buy_side_arb_profit(ask_prices)
    if profit <= 0:
        return None

    profit_pct = (profit / 1.0) * 100
    if profit_pct < self.min_profit_pct:
        return None

    depths = [orderbooks[ticker].yes_ask_depth_at(price) for ticker, price in legs]
    quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="buy_side_taker",
        quantity=quantity,
        leg_actions=["buy"] * len(legs),
    )
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_engine.py -v
```

**Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: add evaluate_buy_side() to ArbEngine"
```

---

### Task 5: Wire buy-side into Dispatcher and add risk profile field

**Files:**
- Modify: `src/risk.py`
- Modify: `src/dispatch.py`
- Modify: `src/main.py`
- Test: `tests/test_dispatch.py`

**Step 1: Write the failing tests**

Add to `tests/test_dispatch.py`:

```python
def test_dispatcher_routes_buy_side_signal():
    """Dispatcher returns a buy_side_taker signal when evaluate_buy_side fires."""
    from unittest.mock import MagicMock, patch
    from src.dispatch import Dispatcher
    from src.models import TradeSignal

    buy_signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.28)], net_profit=0.05,
        profit_pct=5.0, exposure_ratio=0.0, signal_type="buy_side_taker",
        leg_actions=["buy"],
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = buy_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata={})
    signal = dispatcher.process_orderbook_update("M1")
    assert signal is not None
    assert signal.signal_type == "buy_side_taker"
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_dispatch.py::test_dispatcher_routes_buy_side_signal -v
```
Expected: AssertionError — dispatcher doesn't call `evaluate_buy_side`.

**Step 3: Implement**

In `src/risk.py`, add to `RiskProfile` dataclass (after `min_liquidity`):

```python
enable_buy_side_arb: bool = True
```

In `src/dispatch.py`, in `process_orderbook_update` after the existing `if signal and not self.executor.is_executing():` block (after line 68), add:

```python
        if not signal:
            buy_signal = self.engine.evaluate_buy_side(event_ticker, event_books, market_metadata=meta)
            if buy_signal and not self.executor.is_executing():
                if not self.executor.is_event_blacklisted(event_ticker):
                    key = event_ticker + ":buy"
                    last = self._last_signal_time.get(key, 0)
                    if time.time() - last >= self._signal_cooldown:
                        self._last_signal_time[key] = time.time()
                        self._pending_execution.add(key)
                        logger.info(
                            json.dumps({
                                "event": "buy_side_arb_detected",
                                "event_ticker": event_ticker,
                                "legs": buy_signal.legs,
                                "net_profit": round(buy_signal.net_profit, 6),
                            })
                        )
                        return buy_signal
```

In `src/dispatch.py`, update `mark_execution_complete` to handle the `:buy` suffix:

```python
def mark_execution_complete(self, event_ticker: str):
    self._pending_execution.discard(event_ticker)
    self._pending_execution.discard(event_ticker + ":buy")
```

In `src/main.py`, in `_process_orderbook_updates`, the existing `if signal:` block already routes to `_execute_and_track(signal)`. Since `ExecutionManager.execute` now uses `build_orders` which respects `leg_actions`, buy-side signals route correctly with no main.py changes needed.

However, add `enable_buy_side_arb` check in Dispatcher init. Pass it through:

In `src/dispatch.py`, add `enable_buy_side_arb: bool = True` param to `__init__` and store as `self._enable_buy_side_arb`. Wrap the new block with `if self._enable_buy_side_arb:`.

In `src/main.py`, pass `enable_buy_side_arb=self.risk_profile.enable_buy_side_arb` to `Dispatcher(...)`.

**Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

**Step 5: Commit**

```bash
git add src/risk.py src/dispatch.py src/main.py tests/test_dispatch.py
git commit -m "feat: wire buy-side arb into dispatcher and risk profile"
```

---

## Phase 2: Near-Expiry Stale Order Harvesting

Signal: run existing taker arb with relaxed filters within `near_expiry_window_minutes` of event close.

---

### Task 6: RiskProfile near-expiry fields

**Files:**
- Modify: `src/risk.py`
- Test: `tests/test_risk.py`

**Step 1: Write the failing tests**

Add to `tests/test_risk.py`:

```python
def test_conservative_preset_has_near_expiry_window():
    from src.risk import load_risk_profile
    profile = load_risk_profile("conservative", {})
    assert profile.near_expiry_window_minutes == 30
    assert profile.near_expiry_min_profit_pct == 1.0
    assert profile.near_expiry_min_bid_depth == 1
    assert profile.near_expiry_min_volume_24h == 0.0

def test_moderate_preset_has_wider_window():
    from src.risk import load_risk_profile
    profile = load_risk_profile("moderate", {})
    assert profile.near_expiry_window_minutes == 60

def test_aggressive_preset_has_widest_window():
    from src.risk import load_risk_profile
    profile = load_risk_profile("aggressive", {})
    assert profile.near_expiry_window_minutes == 120
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_risk.py::test_conservative_preset_has_near_expiry_window -v
```

**Step 3: Add fields to risk.py**

In `src/risk.py`, add to `RiskProfile` dataclass (after `enable_buy_side_arb`):

```python
near_expiry_window_minutes: int = 0
near_expiry_min_profit_pct: float = 1.0
near_expiry_min_bid_depth: int = 1
near_expiry_min_volume_24h: float = 0.0
```

Update PRESETS to add per-profile values:

```python
"conservative": {
    ...existing fields...,
    "near_expiry_window_minutes": 30,
    "near_expiry_min_profit_pct": 1.0,
    "near_expiry_min_bid_depth": 1,
    "near_expiry_min_volume_24h": 0.0,
},
"moderate": {
    ...existing fields...,
    "near_expiry_window_minutes": 60,
    "near_expiry_min_profit_pct": 0.5,
    "near_expiry_min_bid_depth": 1,
    "near_expiry_min_volume_24h": 0.0,
},
"aggressive": {
    ...existing fields...,
    "near_expiry_window_minutes": 120,
    "near_expiry_min_profit_pct": 0.3,
    "near_expiry_min_bid_depth": 1,
    "near_expiry_min_volume_24h": 0.0,
},
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_risk.py -v
```

**Step 5: Commit**

```bash
git add src/risk.py tests/test_risk.py
git commit -m "feat: add near-expiry fields to RiskProfile presets"
```

---

### Task 7: engine.evaluate_near_expiry()

**Files:**
- Modify: `src/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def _make_engine_near_expiry(**kwargs):
    profile = RiskProfile(
        min_profit_pct=2.0,
        max_exposure_ratio=3.0,
        min_volume_24h=50.0,
        min_bid_depth=5,
        require_recent_trades=False,
        near_term_hours=24,
        hurdle_rate_annual_pct=10.0,
        unwind_phase1_secs=15,
        unwind_phase2_secs=30,
        unwind_price_step_cents=3,
        near_expiry_window_minutes=30,
        near_expiry_min_profit_pct=kwargs.get("near_expiry_min_profit_pct", 1.0),
        near_expiry_min_bid_depth=kwargs.get("near_expiry_min_bid_depth", 1),
        near_expiry_min_volume_24h=kwargs.get("near_expiry_min_volume_24h", 0.0),
    )
    return ArbEngine(risk_profile=profile)


def test_near_expiry_fires_when_normal_evaluate_would_fail_filters():
    """Normal evaluate rejects due to min_volume_24h=50, near_expiry accepts at 0."""
    engine = _make_engine_near_expiry()
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {
        "M1": {"volume_24h": 0, "close_time": _future_iso(0)},
        "M2": {"volume_24h": 0, "close_time": _future_iso(0)},
        "M3": {"volume_24h": 0, "close_time": _future_iso(0)},
    }
    # Normal evaluate should reject (volume < 50)
    assert engine.evaluate("E1", orderbooks, market_metadata=meta) is None
    # Near-expiry should accept (near_expiry_min_volume_24h=0)
    signal = engine.evaluate_near_expiry("E1", orderbooks, market_metadata=meta)
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"

def test_near_expiry_uses_near_expiry_min_profit_pct():
    """Signal rejected if below near_expiry_min_profit_pct."""
    engine = _make_engine_near_expiry(near_expiry_min_profit_pct=50.0)
    orderbooks = {"M1": _ob([(0.40, 100)]), "M2": _ob([(0.35, 100)]), "M3": _ob([(0.35, 100)])}
    meta = {t: {"volume_24h": 0, "close_time": _future_iso(0)} for t in ["M1", "M2", "M3"]}
    assert engine.evaluate_near_expiry("E1", orderbooks, market_metadata=meta) is None
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_engine.py::test_near_expiry_fires_when_normal_evaluate_would_fail_filters -v
```

**Step 3: Add evaluate_near_expiry() to engine.py**

In `src/engine.py` `__init__`, store near-expiry params (after `self.min_liquidity`):

```python
self.near_expiry_min_profit_pct = risk_profile.near_expiry_min_profit_pct
self.near_expiry_min_bid_depth = risk_profile.near_expiry_min_bid_depth
self.near_expiry_min_volume_24h = risk_profile.near_expiry_min_volume_24h
```

Add method after `evaluate_buy_side`:

```python
def evaluate_near_expiry(
    self,
    event_ticker: str,
    orderbooks: dict[str, Orderbook],
    market_metadata: dict[str, dict] | None = None,
) -> TradeSignal | None:
    legs: list[tuple[str, float]] = []
    for ticker, book in orderbooks.items():
        best_bid = book.best_yes_bid()
        if best_bid is None:
            return None
        legs.append((ticker, best_bid))

    bid_prices = [price for _, price in legs]

    if self.near_expiry_min_bid_depth > 1:
        for ticker, best_bid in legs:
            if orderbooks[ticker].yes_bid_depth_at(best_bid) < self.near_expiry_min_bid_depth:
                return None

    if self.near_expiry_min_volume_24h > 0 and market_metadata:
        for ticker, _ in legs:
            if market_metadata.get(ticker, {}).get("volume_24h", 0) < self.near_expiry_min_volume_24h:
                return None

    profit = arb_profit(bid_prices)
    if profit <= 0:
        return None

    profit_pct = (profit / 1.0) * 100
    if profit_pct < self.near_expiry_min_profit_pct:
        return None

    exp_ratio = exposure_ratio(bid_prices)
    if exp_ratio > self.max_exposure_ratio:
        return None

    depths = [orderbooks[ticker].yes_bid_depth_at(price) for ticker, price in legs]
    quantity = max(1, min(int(min(depths)), self.max_contracts_per_arb))

    return TradeSignal(
        event_ticker=event_ticker,
        legs=legs,
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=exp_ratio,
        signal_type="near_expiry_taker",
        quantity=quantity,
    )
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_engine.py -v
```

**Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: add evaluate_near_expiry() to ArbEngine"
```

---

### Task 8: Dispatcher near-expiry routing

**Files:**
- Modify: `src/dispatch.py`
- Modify: `src/main.py`

Near-expiry routing follows the same pattern as buy-side. The dispatcher checks `_is_near_expiry()` and calls `evaluate_near_expiry()` only for events within the window. If a normal taker signal already fires, skip near-expiry evaluation.

**Step 1: Write the failing test**

Add to `tests/test_dispatch.py`:

```python
def test_dispatcher_routes_near_expiry_signal():
    from unittest.mock import MagicMock
    from src.dispatch import Dispatcher
    from src.models import TradeSignal
    from datetime import datetime, timezone, timedelta

    ne_signal = TradeSignal(
        event_ticker="E1", legs=[("M1", 0.55), ("M2", 0.55)], net_profit=0.02,
        profit_pct=2.0, exposure_ratio=1.0, signal_type="near_expiry_taker",
    )
    engine = MagicMock()
    engine.evaluate.return_value = None
    engine.evaluate_buy_side.return_value = None
    engine.evaluate_near_expiry.return_value = ne_signal
    executor = MagicMock()
    executor.is_circuit_breaker_tripped.return_value = False
    executor.is_executing.return_value = False
    executor.is_event_blacklisted.return_value = False
    ob_mgr = MagicMock()
    ob_mgr.get_event_for_market.return_value = "E1"
    ob_mgr.get_event_orderbooks.return_value = {"M1": MagicMock()}
    ob_mgr._event_markets = {"E1": ["M1"]}

    close_soon = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market_metadata = {"M1": {"close_time": close_soon}}

    dispatcher = Dispatcher(engine=engine, executor=executor, maker=None,
                            orderbook_mgr=ob_mgr, market_metadata=market_metadata,
                            near_expiry_window_minutes=30)
    signal = dispatcher.process_orderbook_update("M1")
    assert signal is not None
    assert signal.signal_type == "near_expiry_taker"
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_dispatch.py::test_dispatcher_routes_near_expiry_signal -v
```

**Step 3: Implement**

In `src/dispatch.py`, add `near_expiry_window_minutes: int = 0` to `__init__` params, store as `self._near_expiry_window_minutes`.

Add helper method:

```python
def _is_near_expiry(self, event_ticker: str) -> bool:
    if self._near_expiry_window_minutes <= 0:
        return False
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=self._near_expiry_window_minutes)
    for mt in self.orderbook_mgr._event_markets.get(event_ticker, []):
        close_str = self.market_metadata.get(mt, {}).get("close_time", "")
        if close_str:
            try:
                close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                if now < close_dt <= cutoff:
                    return True
            except (ValueError, TypeError):
                pass
    return False
```

In `process_orderbook_update`, after the buy-side block, add:

```python
        if not signal and self._is_near_expiry(event_ticker):
            ne_signal = self.engine.evaluate_near_expiry(event_ticker, event_books, market_metadata=meta)
            if ne_signal and not self.executor.is_executing():
                if not self.executor.is_event_blacklisted(event_ticker):
                    key = event_ticker + ":ne"
                    last = self._last_signal_time.get(key, 0)
                    if time.time() - last >= self._signal_cooldown:
                        self._last_signal_time[key] = time.time()
                        self._pending_execution.add(key)
                        logger.info(json.dumps({
                            "event": "near_expiry_arb_detected",
                            "event_ticker": event_ticker,
                            "legs": ne_signal.legs,
                            "net_profit": round(ne_signal.net_profit, 6),
                        }))
                        return ne_signal
```

Update `mark_execution_complete` to also discard the `:ne` key:

```python
def mark_execution_complete(self, event_ticker: str):
    self._pending_execution.discard(event_ticker)
    self._pending_execution.discard(event_ticker + ":buy")
    self._pending_execution.discard(event_ticker + ":ne")
```

In `src/main.py`, pass `near_expiry_window_minutes=self.risk_profile.near_expiry_window_minutes` to `Dispatcher(...)`.

**Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

**Step 5: Commit**

```bash
git add src/dispatch.py src/main.py tests/test_dispatch.py
git commit -m "feat: wire near-expiry harvesting into dispatcher"
```

---

## Phase 3: Monotone Constraint Arb

Signal: in "stacked" threshold markets (e.g. "above 5000 / above 5100"), sell the overpriced upper contract and buy the underpriced lower one when price ordering is violated.

Two-leg execution: sell upper (at bid), buy lower (at ask). Both use `leg_actions`.

---

### Task 9: fees.monotone_pair_profit()

**Files:**
- Modify: `src/fees.py`
- Test: `tests/test_fees.py`

**Step 1: Write the failing test**

Add to `tests/test_fees.py`:

```python
from src.fees import monotone_pair_profit

def test_monotone_pair_profit_positive_when_upper_bid_exceeds_lower_ask():
    # Sell upper at 0.65, buy lower at 0.55
    # gross = 0.65 - 0.55 = 0.10
    # fees = taker_fee(0.65) + taker_fee(0.55) = 0.07*0.65*0.35 + 0.07*0.55*0.45
    profit = monotone_pair_profit(upper_bid=0.65, lower_ask=0.55)
    assert profit > 0

def test_monotone_pair_profit_negative_when_spread_too_small():
    profit = monotone_pair_profit(upper_bid=0.55, lower_ask=0.54)
    assert profit < 0

def test_monotone_pair_profit_exact():
    from src.fees import taker_fee
    ub, la = 0.65, 0.50
    expected = (ub - la) - taker_fee(ub) - taker_fee(la)
    assert abs(monotone_pair_profit(ub, la) - expected) < 1e-9
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_fees.py::test_monotone_pair_profit_positive_when_upper_bid_exceeds_lower_ask -v
```

**Step 3: Add to fees.py**

```python
def monotone_pair_profit(upper_bid: float, lower_ask: float) -> float:
    """Net profit from selling the upper-threshold contract and buying the lower-threshold contract.
    Risk-free because P(above lower) >= P(above upper) always."""
    gross = upper_bid - lower_ask
    fees = taker_fee(upper_bid) + taker_fee(lower_ask)
    return gross - fees
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_fees.py -v
```

**Step 5: Commit**

```bash
git add src/fees.py tests/test_fees.py
git commit -m "feat: add monotone_pair_profit() to fees"
```

---

### Task 10: MonotoneFamilyRegistry

Detects and groups threshold markets from Kalshi event titles.

**Files:**
- Modify: `src/discovery.py`
- Test: `tests/test_discovery.py`

**Step 1: Write the failing tests**

Add to `tests/test_discovery.py`:

```python
from src.discovery import MonotoneFamilyRegistry

def test_registers_threshold_pair_with_same_template():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "Will S&P 500 close above 5,000 on May 15?")
    reg.try_register("E2", "M2", "Will S&P 500 close above 5,100 on May 15?")
    families = reg.get_families()
    assert len(families) == 1
    family = list(families.values())[0]
    assert len(family) == 2

def test_does_not_group_unrelated_events():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "Will it rain in Seattle?")
    reg.try_register("E2", "M2", "Will the Fed raise rates?")
    assert len(reg.get_families()) == 0

def test_family_sorted_by_threshold_ascending():
    reg = MonotoneFamilyRegistry()
    reg.try_register("E1", "M1", "S&P above 5,200 by June?")
    reg.try_register("E2", "M2", "S&P above 5,000 by June?")
    reg.try_register("E3", "M3", "S&P above 5,100 by June?")
    families = reg.get_families()
    assert len(families) == 1
    members = list(families.values())[0]
    thresholds = [m["threshold"] for m in members]
    assert thresholds == sorted(thresholds)

def test_try_register_returns_family_key_when_matched():
    reg = MonotoneFamilyRegistry()
    key1 = reg.try_register("E1", "M1", "S&P above 5,000 by June?")
    key2 = reg.try_register("E2", "M2", "S&P above 5,100 by June?")
    assert key1 is not None
    assert key1 == key2
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_discovery.py::test_registers_threshold_pair_with_same_template -v
```

**Step 3: Implement MonotoneFamilyRegistry**

Add to `src/discovery.py` (before `EventDiscovery`):

```python
import re

_THRESHOLD_PATTERN = re.compile(
    r'\b(above|exceed|over|reach|below|under)\s+([\d,]+(?:\.\d+)?)\b',
    re.IGNORECASE,
)


class MonotoneFamilyRegistry:
    def __init__(self):
        # template_key → list of {event_ticker, market_ticker, threshold, direction}
        self._families: dict[str, list[dict]] = {}

    def try_register(self, event_ticker: str, market_ticker: str, title: str) -> str | None:
        m = _THRESHOLD_PATTERN.search(title)
        if not m:
            return None
        direction = m.group(1).lower()
        threshold = float(m.group(2).replace(",", ""))
        template = title[:m.start(2)] + "*" + title[m.end(2):]
        key = template.lower()
        if key not in self._families:
            self._families[key] = []
        self._families[key].append({
            "event_ticker": event_ticker,
            "market_ticker": market_ticker,
            "threshold": threshold,
            "direction": direction,
        })
        self._families[key].sort(key=lambda x: x["threshold"])
        return key

    def get_families(self) -> dict[str, list[dict]]:
        return {k: v for k, v in self._families.items() if len(v) >= 2}

    def unregister_event(self, event_ticker: str):
        for key in list(self._families.keys()):
            self._families[key] = [m for m in self._families[key] if m["event_ticker"] != event_ticker]
            if len(self._families[key]) == 0:
                del self._families[key]
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_discovery.py -v
```

**Step 5: Commit**

```bash
git add src/discovery.py tests/test_discovery.py
git commit -m "feat: add MonotoneFamilyRegistry for threshold market grouping"
```

---

### Task 11: engine.evaluate_monotone_pair() + Dispatcher wiring

**Files:**
- Modify: `src/engine.py`
- Modify: `src/risk.py`
- Modify: `src/dispatch.py`
- Modify: `src/main.py` (wire registry into discovery + dispatcher)
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def test_evaluate_monotone_pair_fires_on_violation():
    """Upper bid (0.65) > lower ask (1 - lower NO bid = 1 - 0.55 = 0.45): violation."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.65, 100)])  # YES bid 65¢
    lower_book = Orderbook(yes_bids={}, no_bids={55: 100})  # YES ask = 45¢
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is not None
    assert signal.signal_type == "monotone"
    assert signal.leg_actions == ["sell", "buy"]

def test_evaluate_monotone_pair_no_signal_when_no_violation():
    """Upper bid (0.40) < lower ask (0.55): no violation, no arb."""
    engine = _make_engine(min_profit_pct=1.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.40, 100)])
    lower_book = Orderbook(yes_bids={}, no_bids={45: 100})  # YES ask = 55¢
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is None

def test_evaluate_monotone_pair_respects_min_profit_pct():
    engine = _make_engine(min_profit_pct=50.0, max_exposure_ratio=10.0)
    upper_book = _ob([(0.65, 100)])
    lower_book = Orderbook(yes_bids={}, no_bids={55: 100})
    signal = engine.evaluate_monotone_pair("E_upper", upper_book, "E_lower", lower_book)
    assert signal is None
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_engine.py::test_evaluate_monotone_pair_fires_on_violation -v
```

**Step 3: Add evaluate_monotone_pair() to engine.py**

In `src/engine.py`, add after `evaluate_near_expiry`:

```python
def evaluate_monotone_pair(
    self,
    upper_ticker: str,
    upper_book: Orderbook,
    lower_ticker: str,
    lower_book: Orderbook,
) -> TradeSignal | None:
    from src.fees import monotone_pair_profit
    upper_bid = upper_book.best_yes_bid()
    lower_ask = lower_book.best_yes_ask()
    if upper_bid is None or lower_ask is None:
        return None

    profit = monotone_pair_profit(upper_bid, lower_ask)
    if profit <= 0:
        return None

    profit_pct = profit * 100
    if profit_pct < self.min_profit_pct:
        return None

    return TradeSignal(
        event_ticker=f"{upper_ticker}|{lower_ticker}",
        legs=[(upper_ticker, upper_bid), (lower_ticker, lower_ask)],
        net_profit=profit,
        profit_pct=profit_pct,
        exposure_ratio=0.0,
        signal_type="monotone",
        quantity=1,
        leg_actions=["sell", "buy"],
    )
```

Add `min_monotone_pair_profit_pct` to RiskProfile in `src/risk.py` (default = `min_profit_pct`) and to engine `__init__`. For simplicity, use `min_profit_pct` from the existing risk profile for the monotone check (no new field needed — the existing threshold is appropriate).

**Dispatcher wiring for monotone:**

In `src/dispatch.py`, add a `monotone_registry` parameter to `__init__` (optional, defaults to None). In `process_orderbook_update`, after near-expiry block, if registry is set, iterate over all families and check each adjacent pair:

```python
        if not signal and self._monotone_registry:
            for family in self._monotone_registry.get_families().values():
                for i in range(len(family) - 1):
                    lower = family[i]
                    upper = family[i + 1]
                    lower_book = self.orderbook_mgr.get_orderbook(lower["market_ticker"])
                    upper_book = self.orderbook_mgr.get_orderbook(upper["market_ticker"])
                    if lower_book is None or upper_book is None:
                        continue
                    mono_signal = self.engine.evaluate_monotone_pair(
                        upper["market_ticker"], upper_book,
                        lower["market_ticker"], lower_book,
                    )
                    if mono_signal:
                        key = mono_signal.event_ticker + ":mono"
                        last = self._last_signal_time.get(key, 0)
                        if time.time() - last >= self._signal_cooldown:
                            self._last_signal_time[key] = time.time()
                            self._pending_execution.add(key)
                            logger.info(json.dumps({
                                "event": "monotone_arb_detected",
                                "pair": mono_signal.event_ticker,
                                "net_profit": round(mono_signal.net_profit, 6),
                            }))
                            return mono_signal
```

Note: monotone signals evaluate ALL families on each update (not just the event that triggered the update). This is intentional — any orderbook change in a family could create a violation.

**Wire MonotoneFamilyRegistry into discovery:**

In `src/discovery.py`, `EventDiscovery.__init__` creates a `self.monotone_registry = MonotoneFamilyRegistry()`.

In `register_events`, for each market in the event, call:
```python
self.monotone_registry.try_register(event.event_ticker, m.ticker, event.title)
```

In `cleanup_expired`, call:
```python
self.monotone_registry.unregister_event(event_ticker)
```

In `src/main.py`, pass `monotone_registry=self.discovery.monotone_registry` to `Dispatcher(...)`.

**Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

**Step 5: Commit**

```bash
git add src/engine.py src/risk.py src/dispatch.py src/discovery.py src/main.py tests/test_engine.py
git commit -m "feat: monotone constraint arb — evaluate, registry, dispatcher wiring"
```

---

## Phase 4: Two-Sided Market Making

Post both bid and ask on individual liquid markets. Earn the spread when both sides fill. Risk-gated by `min_spread_cents` and `max_two_sided_inventory`.

---

### Task 12: RiskProfile two-sided fields

**Files:**
- Modify: `src/risk.py`
- Test: `tests/test_risk.py`

**Step 1: Write the failing tests**

Add to `tests/test_risk.py`:

```python
def test_conservative_two_sided_fields():
    from src.risk import load_risk_profile
    profile = load_risk_profile("conservative", {})
    assert profile.two_sided_min_spread_cents == 6
    assert profile.two_sided_max_inventory == 10
    assert profile.two_sided_timeout_secs == 120
    assert profile.two_sided_min_volume_24h == 50.0

def test_aggressive_two_sided_fields():
    from src.risk import load_risk_profile
    profile = load_risk_profile("aggressive", {})
    assert profile.two_sided_min_spread_cents == 2
    assert profile.two_sided_max_inventory == 50
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_risk.py::test_conservative_two_sided_fields -v
```

**Step 3: Add fields to risk.py**

In `src/risk.py`, add to `RiskProfile`:

```python
two_sided_min_spread_cents: int = 6
two_sided_max_inventory: int = 0   # 0 = disabled
two_sided_timeout_secs: int = 120
two_sided_min_volume_24h: float = 50.0
```

Update PRESETS:

```python
"conservative": { ...existing...,
    "two_sided_min_spread_cents": 6,
    "two_sided_max_inventory": 10,
    "two_sided_timeout_secs": 120,
    "two_sided_min_volume_24h": 50.0,
},
"moderate": { ...existing...,
    "two_sided_min_spread_cents": 4,
    "two_sided_max_inventory": 25,
    "two_sided_timeout_secs": 180,
    "two_sided_min_volume_24h": 10.0,
},
"aggressive": { ...existing...,
    "two_sided_min_spread_cents": 2,
    "two_sided_max_inventory": 50,
    "two_sided_timeout_secs": 300,
    "two_sided_min_volume_24h": 0.0,
},
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_risk.py -v
```

**Step 5: Commit**

```bash
git add src/risk.py tests/test_risk.py
git commit -m "feat: add two-sided market making fields to RiskProfile"
```

---

### Task 13: engine.evaluate_two_sided()

**Files:**
- Modify: `src/engine.py`
- Test: `tests/test_engine.py`

Evaluates a single market (not an event). Returns a signal with two legs: one buy (bid+1¢) and one sell (ask-1¢) on the same ticker. Uses a special `TwoSidedSignal` representation via `legs` — leg 0 is the buy side, leg 1 is the sell side, both with the same ticker.

**Step 1: Write the failing tests**

Add to `tests/test_engine.py`:

```python
def _make_two_sided_engine(min_spread_cents=6, max_inventory=10, min_volume=0.0):
    profile = RiskProfile(
        min_profit_pct=1.0, max_exposure_ratio=3.0, min_volume_24h=0.0,
        min_bid_depth=1, require_recent_trades=False, near_term_hours=24,
        hurdle_rate_annual_pct=10.0, unwind_phase1_secs=15, unwind_phase2_secs=30,
        unwind_price_step_cents=3, two_sided_min_spread_cents=min_spread_cents,
        two_sided_max_inventory=max_inventory, two_sided_timeout_secs=120,
        two_sided_min_volume_24h=min_volume,
    )
    return ArbEngine(risk_profile=profile)


def test_evaluate_two_sided_fires_on_wide_spread():
    # YES bid 45¢, YES ask (= 1 - NO bid) = 1 - 0.45 = 55¢ → spread = 10¢ > 6¢
    engine = _make_two_sided_engine(min_spread_cents=6)
    book = Orderbook(yes_bids={45: 50}, no_bids={45: 50})  # ask = 55¢
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is not None
    assert signal.signal_type == "two_sided"
    assert signal.leg_actions == ["buy", "sell"]
    buy_leg = signal.legs[0]
    sell_leg = signal.legs[1]
    assert buy_leg[0] == "M1"
    assert sell_leg[0] == "M1"
    assert buy_leg[1] == 0.46   # bid + 1¢
    assert sell_leg[1] == 0.54  # ask - 1¢

def test_evaluate_two_sided_no_signal_on_narrow_spread():
    engine = _make_two_sided_engine(min_spread_cents=6)
    book = Orderbook(yes_bids={48: 50}, no_bids={48: 50})  # spread = 4¢
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is None

def test_evaluate_two_sided_disabled_when_max_inventory_zero():
    engine = _make_two_sided_engine(max_inventory=0)
    book = Orderbook(yes_bids={40: 50}, no_bids={40: 50})
    signal = engine.evaluate_two_sided("M1", book, volume_24h=100.0)
    assert signal is None
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_engine.py::test_evaluate_two_sided_fires_on_wide_spread -v
```

**Step 3: Add evaluate_two_sided() to engine.py**

In `src/engine.py` `__init__`, add:

```python
self.two_sided_min_spread_cents = risk_profile.two_sided_min_spread_cents
self.two_sided_max_inventory = risk_profile.two_sided_max_inventory
self.two_sided_min_volume_24h = risk_profile.two_sided_min_volume_24h
```

Add method:

```python
def evaluate_two_sided(
    self,
    ticker: str,
    book: Orderbook,
    volume_24h: float = 0.0,
) -> TradeSignal | None:
    if self.two_sided_max_inventory <= 0:
        return None
    if volume_24h < self.two_sided_min_volume_24h:
        return None

    best_bid = book.best_yes_bid()
    best_ask = book.best_yes_ask()
    if best_bid is None or best_ask is None:
        return None

    spread_cents = round((best_ask - best_bid) * 100)
    if spread_cents < self.two_sided_min_spread_cents + 2:
        # Need at least min_spread + 2¢ to post 1¢ inside on each side
        return None

    post_bid = round(best_bid + 0.01, 2)
    post_ask = round(best_ask - 0.01, 2)

    if post_bid >= post_ask:
        return None

    return TradeSignal(
        event_ticker=ticker,
        legs=[(ticker, post_bid), (ticker, post_ask)],
        net_profit=round(post_ask - post_bid, 4),
        profit_pct=round((post_ask - post_bid) * 100, 2),
        exposure_ratio=0.0,
        signal_type="two_sided",
        quantity=1,
        leg_actions=["buy", "sell"],
    )
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_engine.py -v
```

**Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: add evaluate_two_sided() to ArbEngine"
```

---

### Task 14: TwoSidedManager

Manages active two-sided positions: tracks paired order IDs, handles timeouts, and unwinds via existing tiered logic.

**Files:**
- Create: `src/two_sided.py`
- Test: `tests/test_two_sided.py`

**Step 1: Write the failing tests**

Create `tests/test_two_sided.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.two_sided import TwoSidedManager
from src.risk import load_risk_profile


def _make_manager(timeout_secs=5, max_inventory=10):
    profile = load_risk_profile("aggressive", {
        "two_sided_timeout_secs": timeout_secs,
        "two_sided_max_inventory": max_inventory,
    })
    api = MagicMock()
    api.build_buy_order.return_value = {"action": "buy"}
    api.build_sell_order.return_value = {"action": "sell"}
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "BUY1", "status": "resting"}},
            {"order": {"order_id": "SELL1", "status": "resting"}},
        ]
    })
    api.cancel_order = AsyncMock(return_value={})
    return TwoSidedManager(api=api, risk_profile=profile), api


@pytest.mark.asyncio
async def test_post_places_both_orders():
    manager, api = _make_manager()
    from src.models import TradeSignal
    signal = TradeSignal(
        event_ticker="M1", legs=[("M1", 0.46), ("M1", 0.54)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=0.0,
        signal_type="two_sided", leg_actions=["buy", "sell"],
    )
    posted = await manager.post(signal)
    assert posted
    assert api.batch_create_orders.called


@pytest.mark.asyncio
async def test_inventory_cap_prevents_over_posting():
    manager, api = _make_manager(max_inventory=2)
    from src.models import TradeSignal
    signal = TradeSignal(
        event_ticker="M1", legs=[("M1", 0.46), ("M1", 0.54)],
        net_profit=0.08, profit_pct=8.0, exposure_ratio=0.0,
        signal_type="two_sided", leg_actions=["buy", "sell"], quantity=5,
    )
    posted = await manager.post(signal)
    # Should cap to max_inventory=2, still post
    assert posted


@pytest.mark.asyncio
async def test_cancel_unfilled_on_timeout():
    manager, api = _make_manager(timeout_secs=1)
    manager._positions["M1"] = {"buy_id": "BUY1", "sell_id": "SELL1", "filled_side": None, "quantity": 1}
    await manager._check_timeouts()
    # Nothing to time out (filled_side=None means neither side filled yet — both pending)
    # Timeout should cancel both
    assert api.cancel_order.call_count == 2
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_two_sided.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.two_sided'`

**Step 3: Implement TwoSidedManager**

Create `src/two_sided.py`:

```python
import asyncio
import logging
import time
from src.models import TradeSignal
from src.risk import RiskProfile

logger = logging.getLogger(__name__)


class TwoSidedManager:
    def __init__(self, api, risk_profile: RiskProfile):
        self.api = api
        self._timeout_secs = risk_profile.two_sided_timeout_secs
        self._max_inventory = risk_profile.two_sided_max_inventory
        # ticker → {buy_id, sell_id, filled_side, quantity, posted_at, fill_price}
        self._positions: dict[str, dict] = {}

    @property
    def total_inventory(self) -> int:
        return sum(p["quantity"] for p in self._positions.values() if p["filled_side"])

    async def post(self, signal: TradeSignal) -> bool:
        ticker = signal.event_ticker
        if ticker in self._positions:
            return False  # already active on this market

        quantity = min(signal.quantity, max(1, self._max_inventory - self.total_inventory))
        if quantity <= 0:
            return False

        buy_leg, sell_leg = signal.legs
        orders = [
            self.api.build_buy_order(ticker=buy_leg[0], yes_price=buy_leg[1], quantity=quantity),
            self.api.build_sell_order(ticker=sell_leg[0], yes_price=sell_leg[1], quantity=quantity),
        ]
        resp = await self.api.batch_create_orders({"orders": orders})
        order_list = resp.get("orders", [])
        if len(order_list) < 2:
            return False

        buy_inner = self.api.unwrap_order(order_list[0]) if hasattr(self.api, 'unwrap_order') else order_list[0].get("order", order_list[0])
        sell_inner = self.api.unwrap_order(order_list[1]) if hasattr(self.api, 'unwrap_order') else order_list[1].get("order", order_list[1])

        self._positions[ticker] = {
            "buy_id": buy_inner.get("order_id"),
            "sell_id": sell_inner.get("order_id"),
            "filled_side": None,
            "quantity": quantity,
            "posted_at": time.time(),
            "fill_price": None,
            "post_bid": buy_leg[1],
            "post_ask": sell_leg[1],
        }
        return True

    def owns_order(self, order_id: str) -> bool:
        for pos in self._positions.values():
            if pos["buy_id"] == order_id or pos["sell_id"] == order_id:
                return True
        return False

    async def handle_fill(self, order_id: str, fill_price: float, quantity: int):
        for ticker, pos in list(self._positions.items()):
            if pos["buy_id"] == order_id:
                pos["filled_side"] = "buy"
                pos["fill_price"] = fill_price
                await self.api.cancel_order(pos["sell_id"])
                # Unwind: sell what we just bought
                await self._unwind_buy(ticker, fill_price, quantity)
                self._positions.pop(ticker, None)
                return
            if pos["sell_id"] == order_id:
                pos["filled_side"] = "sell"
                pos["fill_price"] = fill_price
                await self.api.cancel_order(pos["buy_id"])
                # We're short — need to buy to cover
                await self._unwind_sell(ticker, fill_price, quantity)
                self._positions.pop(ticker, None)
                return

    async def _unwind_buy(self, ticker: str, bought_at: float, quantity: int):
        # Try to sell slightly above buy price; fall back to market
        order = self.api.build_sell_order(ticker=ticker, yes_price=min(0.99, bought_at + 0.01), quantity=quantity)
        await self.api.batch_create_orders({"orders": [order]})

    async def _unwind_sell(self, ticker: str, sold_at: float, quantity: int):
        order = self.api.build_buy_order(ticker=ticker, yes_price=max(0.01, sold_at - 0.01), quantity=quantity)
        await self.api.batch_create_orders({"orders": [order]})

    async def _check_timeouts(self):
        now = time.time()
        for ticker, pos in list(self._positions.items()):
            if pos["filled_side"] is None and now - pos["posted_at"] > self._timeout_secs:
                logger.info("Two-sided timeout on %s — cancelling both sides", ticker)
                await self.api.cancel_order(pos["buy_id"])
                await self.api.cancel_order(pos["sell_id"])
                self._positions.pop(ticker, None)

    async def timeout_loop(self):
        while True:
            await asyncio.sleep(10)
            try:
                await self._check_timeouts()
            except Exception:
                logger.exception("Two-sided timeout loop error")
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_two_sided.py -v
```

**Step 5: Commit**

```bash
git add src/two_sided.py tests/test_two_sided.py
git commit -m "feat: TwoSidedManager for paired bid/ask order lifecycle"
```

---

### Task 15: Wire two-sided into main.py

**Files:**
- Modify: `src/main.py`
- Modify: `src/dispatch.py`

Two-sided differs from other strategies: it fires per-market (not per-event) and runs on its own cadence from the `_maker_worker`-equivalent loop, not the dispatcher's hot path. The cleanest approach: evaluate two-sided in `_maker_worker` alongside existing maker logic.

**Step 1: No failing test for wiring — verify by running full test suite**

```bash
python3 -m pytest tests/ -v
```
Confirm all pass before starting.

**Step 2: Implement**

In `src/main.py`:

1. Import `TwoSidedManager` at the top:
```python
from src.two_sided import TwoSidedManager
```

2. In `ArbBot.__init__`, add after maker setup:
```python
self.two_sided = TwoSidedManager(
    api=self.api,
    risk_profile=self.risk_profile,
) if self.risk_profile.two_sided_max_inventory > 0 else None
```

3. In `_maker_worker`, after existing maker logic for each dirty event, add two-sided evaluation:
```python
                    # Two-sided evaluation per market
                    if self.two_sided:
                        for mt, book in event_books.items():
                            vol = self.discovery.market_metadata.get(mt, {}).get("volume_24h", 0.0)
                            ts_signal = self.engine.evaluate_two_sided(mt, book, volume_24h=vol)
                            if ts_signal and not self.two_sided.owns_order(mt):
                                await self.two_sided.post(ts_signal)
```

4. In `_on_fill`, add two-sided fill routing before the maker check:
```python
    def _on_fill(self, fill_data: dict):
        order_id = fill_data.get("order_id", "")
        if self.two_sided and self.two_sided.owns_order(order_id):
            ticker = fill_data.get("market_ticker", "")
            price = float(fill_data.get("yes_price_dollars", 0))
            quantity = int(float(fill_data.get("count_fp", 0)))
            asyncio.create_task(self.two_sided.handle_fill(order_id, price, quantity))
            return
        if self.dispatcher.route_fill(fill_data) == "maker":
            ...
```

5. In `run()`, add two-sided timeout loop to tasks:
```python
        if self.two_sided:
            tasks.append(asyncio.create_task(self.two_sided.timeout_loop()))
```

**Step 3: Run all tests**

```bash
python3 -m pytest tests/ -v
```

**Step 4: Commit**

```bash
git add src/main.py tests/
git commit -m "feat: wire TwoSidedManager into bot lifecycle"
```

---

## Phase 5: Config and Docs

### Task 16: Update config.example.yaml and CLAUDE.md

**Files:**
- Modify: `config.example.yaml`
- Modify: `CLAUDE.md`

**Step 1: Update config.example.yaml**

Add new strategy fields under `strategy:`:

```yaml
strategy:
  # --- existing fields ---
  ...

  # Buy-side structural arb (all risk modes)
  # enable_buy_side_arb: true

  # Near-expiry stale order harvesting
  # near_expiry_window_minutes: 30     # 0 = disabled; conservative=30, moderate=60, aggressive=120
  # near_expiry_min_profit_pct: 1.0
  # near_expiry_min_bid_depth: 1
  # near_expiry_min_volume_24h: 0.0

  # Two-sided market making
  # two_sided_max_inventory: 10        # 0 = disabled; conservative=10, moderate=25, aggressive=50
  # two_sided_min_spread_cents: 6      # conservative=6, moderate=4, aggressive=2
  # two_sided_timeout_secs: 120
  # two_sided_min_volume_24h: 50.0
```

**Step 2: Update CLAUDE.md**

In the `### Key modules` section, add:
- `src/two_sided.py` — `TwoSidedManager`: paired bid/ask order lifecycle and timeout/unwind

In the `### Data flow` section, add to the dispatcher routing block:
```
    → ArbEngine.evaluate_buy_side (buy all outcomes at ask < $1-fees) → ExecutionManager.execute
    → ArbEngine.evaluate_near_expiry (relaxed taker within near_expiry_window_minutes) → ExecutionManager.execute
    → ArbEngine.evaluate_monotone_pair (stacked threshold violation) → ExecutionManager.execute
    → ArbEngine.evaluate_two_sided (spread capture per market) → TwoSidedManager.post
```

Update the `### Risk Modes` section to document new fields.

**Step 3: Diff config.yaml against config.example.yaml**

```bash
diff config.yaml config.example.yaml
```

Remove any stale overrides in `config.yaml` that are now covered by risk profile defaults.

**Step 4: Commit**

```bash
git add config.example.yaml CLAUDE.md
git commit -m "docs: update config.example.yaml and CLAUDE.md for new strategies"
```

---

## Verification

After all tasks, run the full test suite:

```bash
python3 -m pytest tests/ -v --tb=short
```

Expected: all tests pass. Confirm no regressions in existing `test_engine.py`, `test_dispatch.py`, `test_executor.py`, `test_risk.py`.

Then start the bot in demo mode and verify STATUS line shows no errors:

```bash
python3 -m src.main
```

Check logs for:
- No import errors
- `maker_horizon=N` still appears in STATUS
- New signal types logged if triggered (`buy_side_arb_detected`, `near_expiry_arb_detected`, `monotone_arb_detected`)
