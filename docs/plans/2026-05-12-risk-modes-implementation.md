# Risk Modes and Loss Elimination Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate all sources of trading losses by adding configurable risk modes, pre-execution liquidity validation, and tiered auto-unwind for partial fills.

**Architecture:** A `RiskProfile` dataclass holds all thresholds for three risk presets (conservative/moderate/aggressive). The engine gains a volume-based liquidity gate. An async recent-trade check runs between signal detection and execution. The executor gains a three-phase unwind state machine that fires on partial fills.

**Tech Stack:** Python 3.11, asyncio, pytest, existing Kalshi REST/WS API client

---

### Task 1: RiskProfile Dataclass

**Files:**
- Create: `src/risk.py`
- Create: `tests/test_risk.py`

**Step 1: Write failing tests**

```python
# tests/test_risk.py
from src.risk import RiskProfile, load_risk_profile


def test_conservative_preset():
    profile = load_risk_profile("conservative", {})
    assert profile.min_volume_24h == 50
    assert profile.min_bid_depth == 5
    assert profile.min_profit_pct == 2.0
    assert profile.require_recent_trades is True
    assert profile.max_exposure_ratio == 2.0
    assert profile.unwind_phase1_secs == 15
    assert profile.unwind_phase2_secs == 30
    assert profile.unwind_price_step_cents == 3


def test_moderate_preset():
    profile = load_risk_profile("moderate", {})
    assert profile.min_volume_24h == 10
    assert profile.min_bid_depth == 2
    assert profile.min_profit_pct == 1.0
    assert profile.require_recent_trades is True
    assert profile.max_exposure_ratio == 3.0
    assert profile.unwind_phase1_secs == 30
    assert profile.unwind_phase2_secs == 60
    assert profile.unwind_price_step_cents == 5


def test_aggressive_preset():
    profile = load_risk_profile("aggressive", {})
    assert profile.min_volume_24h == 0
    assert profile.min_bid_depth == 1
    assert profile.min_profit_pct == 0.5
    assert profile.require_recent_trades is False
    assert profile.max_exposure_ratio == 5.0
    assert profile.unwind_phase1_secs == 45
    assert profile.unwind_phase2_secs == 90
    assert profile.unwind_price_step_cents == 8


def test_overrides_take_precedence():
    profile = load_risk_profile("conservative", {"min_volume_24h": 200, "min_profit_pct": 5.0})
    assert profile.min_volume_24h == 200
    assert profile.min_profit_pct == 5.0
    # Non-overridden fields keep preset values
    assert profile.min_bid_depth == 5
    assert profile.require_recent_trades is True


def test_invalid_mode_raises():
    import pytest
    with pytest.raises(ValueError, match="Invalid risk_mode"):
        load_risk_profile("yolo", {})
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.risk'`

**Step 3: Write implementation**

```python
# src/risk.py
from dataclasses import dataclass


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


PRESETS: dict[str, dict] = {
    "conservative": {
        "min_volume_24h": 50,
        "min_bid_depth": 5,
        "min_profit_pct": 2.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 2.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 15,
        "unwind_phase2_secs": 30,
        "unwind_price_step_cents": 3,
    },
    "moderate": {
        "min_volume_24h": 10,
        "min_bid_depth": 2,
        "min_profit_pct": 1.0,
        "require_recent_trades": True,
        "max_exposure_ratio": 3.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 30,
        "unwind_phase2_secs": 60,
        "unwind_price_step_cents": 5,
    },
    "aggressive": {
        "min_volume_24h": 0,
        "min_bid_depth": 1,
        "min_profit_pct": 0.5,
        "require_recent_trades": False,
        "max_exposure_ratio": 5.0,
        "near_term_hours": 24,
        "hurdle_rate_annual_pct": 10.0,
        "unwind_phase1_secs": 45,
        "unwind_phase2_secs": 90,
        "unwind_price_step_cents": 8,
    },
}


def load_risk_profile(mode: str, overrides: dict) -> RiskProfile:
    if mode not in PRESETS:
        raise ValueError(f"Invalid risk_mode: {mode!r}. Must be one of {list(PRESETS.keys())}")
    values = {**PRESETS[mode]}
    for key, val in overrides.items():
        if key in values:
            values[key] = type(values[key])(val)
    return RiskProfile(**values)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_risk.py -v`
Expected: All 5 PASS

**Step 5: Commit**

```bash
git add src/risk.py tests/test_risk.py
git commit -m "feat: add RiskProfile dataclass with conservative/moderate/aggressive presets"
```

---

### Task 2: Config Parsing for Risk Mode

**Files:**
- Modify: `src/config.py` (add `risk_mode` field, remove strategy fields that move to RiskProfile)
- Modify: `config.example.yaml` (add `risk_mode` field)
- Modify: `tests/test_config.py` (add risk_mode tests)

**Step 1: Write failing test**

Add to `tests/test_config.py`:

```python
def test_load_config_with_risk_mode(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: "test-key"
    private_key_path: "~/.kalshi/demo_private_key.pem"
strategy:
  risk_mode: conservative
  fill_timeout_secs: 30
  event_poll_interval_secs: 60
logging:
  level: INFO
  file: logs/test.log
""")
    from src.config import load_config
    cfg = load_config(str(config_file))
    assert cfg.risk_mode == "conservative"


def test_load_config_defaults_risk_mode_to_conservative(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: "test-key"
    private_key_path: "~/.kalshi/demo_private_key.pem"
strategy:
  min_profit_pct: 1.0
  max_exposure_ratio: 3.0
  fill_timeout_secs: 30
  event_poll_interval_secs: 60
logging:
  level: INFO
  file: logs/test.log
""")
    from src.config import load_config
    cfg = load_config(str(config_file))
    assert cfg.risk_mode == "conservative"
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py::test_load_config_with_risk_mode tests/test_config.py::test_load_config_defaults_risk_mode_to_conservative -v`
Expected: FAIL — `AttributeError: Config has no attribute 'risk_mode'`

**Step 3: Modify Config dataclass and loader**

In `src/config.py`, add `risk_mode: str` and `strategy_overrides: dict` to the `Config` dataclass. Modify `load_config()` to extract `risk_mode` from strategy (defaulting to `"conservative"`) and collect any override fields.

The Config dataclass becomes:

```python
@dataclass
class Config:
    mode: str
    api_key_id: str
    private_key_path: Path
    rest_base_url: str
    ws_url: str
    risk_mode: str
    strategy_overrides: dict
    fill_timeout_secs: int
    event_poll_interval_secs: int
    log_level: str
    log_file: str
```

The loader changes: remove individual strategy fields (`min_profit_pct`, `max_exposure_ratio`, etc.) from Config. Instead store `risk_mode` and `strategy_overrides` (a dict of any explicit overrides like `min_volume_24h: 200`). Keep `fill_timeout_secs` and `event_poll_interval_secs` on Config since they're operational, not risk parameters.

In `load_config()`, replace the strategy field extraction at the end:

```python
    strategy = raw["strategy"]
    risk_mode = strategy.get("risk_mode", "conservative")
    override_keys = {
        "min_volume_24h", "min_bid_depth", "min_profit_pct",
        "require_recent_trades", "max_exposure_ratio",
        "near_term_hours", "hurdle_rate_annual_pct",
        "unwind_phase1_secs", "unwind_phase2_secs", "unwind_price_step_cents",
    }
    strategy_overrides = {k: v for k, v in strategy.items() if k in override_keys}

    return Config(
        mode=mode,
        api_key_id=creds["api_key_id"],
        private_key_path=Path(creds["private_key_path"]).expanduser(),
        rest_base_url=rest_url,
        ws_url=ws_url,
        risk_mode=risk_mode,
        strategy_overrides=strategy_overrides,
        fill_timeout_secs=strategy.get("fill_timeout_secs", 30),
        event_poll_interval_secs=strategy.get("event_poll_interval_secs", 60),
        log_level=logging_cfg.get("level", "INFO"),
        log_file=logging_cfg.get("file", "logs/arb_bot.log"),
    )
```

**Step 4: Fix existing config tests**

The existing tests in `tests/test_config.py` reference `cfg.min_profit_pct`, `cfg.max_exposure_ratio`, etc. These fields moved to RiskProfile. Update existing tests:
- `test_load_config_demo_mode`: Remove assertions on `min_profit_pct`, `max_exposure_ratio`. Add assertion on `risk_mode`.
- `test_load_config_custom_strategy_params`: Change to test that explicit overrides appear in `cfg.strategy_overrides`.
- Keep assertions on `fill_timeout_secs`, `event_poll_interval_secs` (still on Config).

**Step 5: Run all config tests**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All PASS

**Step 6: Update config.example.yaml**

```yaml
mode: demo

credentials:
  demo:
    api_key_id: "your-demo-key-id"
    private_key_path: "~/.kalshi/demo_private_key.pem"
  live:
    api_key_id: "your-live-key-id"
    private_key_path: "~/.kalshi/live_private_key.pem"

strategy:
  risk_mode: conservative    # conservative | moderate | aggressive
  fill_timeout_secs: 30
  event_poll_interval_secs: 60
  # Optional overrides (uncomment to customize):
  # min_volume_24h: 50
  # min_bid_depth: 5
  # min_profit_pct: 2.0
  # require_recent_trades: true
  # max_exposure_ratio: 2.0
  # unwind_phase1_secs: 15
  # unwind_phase2_secs: 30
  # unwind_price_step_cents: 3

logging:
  level: INFO
  file: logs/arb_bot.log
```

**Step 7: Commit**

```bash
git add src/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add risk_mode config with strategy overrides"
```

---

### Task 3: Engine Uses RiskProfile + Volume Check

**Files:**
- Modify: `src/engine.py` (accept RiskProfile, add volume check)
- Modify: `src/main.py:29-35` (construct engine from RiskProfile, pass volume in metadata)
- Modify: `src/main.py:155-159` (add volume_24h to _market_metadata)
- Modify: `tests/test_engine.py` (update _make_engine helper, add volume tests)

**Step 1: Write failing tests**

Add to `tests/test_engine.py`:

```python
from src.risk import RiskProfile, load_risk_profile


def _make_engine_from_profile(mode="aggressive", **overrides):
    profile = load_risk_profile(mode, overrides)
    return ArbEngine(risk_profile=profile)


def test_volume_check_rejects_zero_volume_leg():
    """Regression: MEDLAN event had phantom bids with zero volume."""
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {
        "KXATPSETWINNER-MED": Orderbook(yes_bids=[OrderbookLevel(price=0.46, quantity=10)], no_bids=[]),
        "KXATPSETWINNER-LAN": Orderbook(yes_bids=[OrderbookLevel(price=0.99, quantity=10)], no_bids=[]),
    }
    meta = {
        "KXATPSETWINNER-MED": {"volume_24h": 0},
        "KXATPSETWINNER-LAN": {"volume_24h": 500},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None


def test_volume_check_accepts_high_volume():
    engine = _make_engine_from_profile(mode="conservative")
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=100)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=100)], no_bids=[]),
    }
    meta = {
        "M1": {"volume_24h": 200},
        "M2": {"volume_24h": 150},
        "M3": {"volume_24h": 100},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None


def test_aggressive_mode_allows_zero_volume():
    engine = _make_engine_from_profile(mode="aggressive")
    orderbooks = {
        "M1": Orderbook(yes_bids=[OrderbookLevel(price=0.40, quantity=10)], no_bids=[]),
        "M2": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=10)], no_bids=[]),
        "M3": Orderbook(yes_bids=[OrderbookLevel(price=0.35, quantity=10)], no_bids=[]),
    }
    meta = {"M1": {"volume_24h": 0}, "M2": {"volume_24h": 0}, "M3": {"volume_24h": 0}}
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is not None
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_engine.py::test_volume_check_rejects_zero_volume_leg -v`
Expected: FAIL — `TypeError: ArbEngine.__init__() got an unexpected keyword argument 'risk_profile'`

**Step 3: Modify ArbEngine to accept RiskProfile**

Replace the `ArbEngine.__init__` signature to accept a `RiskProfile`. Keep backward compat with the old kwargs for existing tests:

```python
# src/engine.py
from src.risk import RiskProfile

class ArbEngine:
    def __init__(
        self,
        risk_profile: RiskProfile | None = None,
        # Legacy kwargs for backward compat with existing tests
        min_profit_pct: float = 1.0,
        max_exposure_ratio: float = 3.0,
        near_term_hours: float = 24,
        hurdle_rate_annual_pct: float = 10.0,
        min_bid_depth: int = 1,
    ):
        if risk_profile:
            self.min_profit_pct = risk_profile.min_profit_pct
            self.max_exposure_ratio = risk_profile.max_exposure_ratio
            self.near_term_hours = risk_profile.near_term_hours
            self.hurdle_rate_annual_pct = risk_profile.hurdle_rate_annual_pct
            self.min_bid_depth = risk_profile.min_bid_depth
            self.min_volume_24h = risk_profile.min_volume_24h
        else:
            self.min_profit_pct = min_profit_pct
            self.max_exposure_ratio = max_exposure_ratio
            self.near_term_hours = near_term_hours
            self.hurdle_rate_annual_pct = hurdle_rate_annual_pct
            self.min_bid_depth = min_bid_depth
            self.min_volume_24h = 0
```

Add volume check in `evaluate()`, after the depth check loop and before profit calculation. Insert at line 56, after `legs.append(...)`:

```python
        # Volume check — reject legs with insufficient trading activity
        if self.min_volume_24h > 0 and market_metadata:
            for ticker, _ in legs:
                meta = market_metadata.get(ticker, {})
                volume = meta.get("volume_24h", 0)
                if volume < self.min_volume_24h:
                    return None
```

**Step 4: Update main.py to pass volume in metadata**

In `src/main.py:155-159`, add `volume_24h` when building `_market_metadata`:

```python
            for m in event.markets:
                self._market_metadata[m.ticker] = {
                    "close_time": m.close_time,
                    "expected_expiration_time": m.expected_expiration_time,
                    "volume_24h": m.volume_24h,
                }
```

In `src/main.py:29-35`, construct engine from RiskProfile:

```python
        from src.risk import load_risk_profile
        self.risk_profile = load_risk_profile(self.cfg.risk_mode, self.cfg.strategy_overrides)
        self.engine = ArbEngine(risk_profile=self.risk_profile)
```

**Step 5: Run all engine tests**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: All PASS (old tests use legacy kwargs, new tests use risk_profile)

**Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/engine.py src/main.py tests/test_engine.py
git commit -m "feat: add volume-based liquidity check in engine, wire RiskProfile"
```

---

### Task 4: Recent Trades API Endpoint

**Files:**
- Modify: `src/api.py` (add `get_market_trades` method)
- Create: `tests/test_api_trades.py`

**Step 1: Write failing test**

```python
# tests/test_api_trades.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.api import KalshiAPI


def test_get_market_trades_returns_trades():
    api = KalshiAPI.__new__(KalshiAPI)
    api._get = AsyncMock(return_value={
        "trades": [
            {"ticker": "M1", "count": 5, "yes_price": 40, "created_time": "2026-05-12T17:00:00Z"},
        ],
        "cursor": "",
    })
    result = asyncio.get_event_loop().run_until_complete(api.get_market_trades("M1"))
    assert len(result.get("trades", [])) == 1
    api._get.assert_called_once_with("/markets/trades", params={"ticker": "M1", "limit": "10"})
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_api_trades.py -v`
Expected: FAIL — `AttributeError: 'KalshiAPI' object has no attribute 'get_market_trades'`

**Step 3: Add method to api.py**

Add after `get_balance()` at line 157 in `src/api.py`:

```python
    async def get_market_trades(self, ticker: str, limit: int = 10) -> dict:
        return await self._get("/markets/trades", params={"ticker": ticker, "limit": str(limit)})
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_api_trades.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/api.py tests/test_api_trades.py
git commit -m "feat: add get_market_trades API endpoint"
```

---

### Task 5: Async Liquidity Validation (Recent Trades Check)

**Files:**
- Modify: `src/main.py` (add `_validate_recent_trades` method, call before execution)

**Step 1: Write failing test**

```python
# tests/test_recent_trades.py
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.main import ArbBot


def _make_mock_bot(require_recent_trades=True):
    """Create a minimal ArbBot with mocked dependencies for testing."""
    bot = ArbBot.__new__(ArbBot)
    bot.api = MagicMock()
    bot.api.get_market_trades = AsyncMock()

    from src.risk import load_risk_profile
    bot.risk_profile = load_risk_profile("conservative", {})
    return bot


def test_recent_trades_rejects_stale_market():
    bot = _make_mock_bot()
    # No recent trades returned
    bot.api.get_market_trades = AsyncMock(return_value={"trades": [], "cursor": ""})

    result = asyncio.get_event_loop().run_until_complete(
        bot._validate_recent_trades(["M1", "M2"])
    )
    assert result is False


def test_recent_trades_accepts_active_market():
    bot = _make_mock_bot()
    bot.api.get_market_trades = AsyncMock(return_value={
        "trades": [{"ticker": "M1", "count": 5, "created_time": "2026-05-12T17:00:00Z"}],
        "cursor": "",
    })

    result = asyncio.get_event_loop().run_until_complete(
        bot._validate_recent_trades(["M1", "M2"])
    )
    assert result is True
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_recent_trades.py -v`
Expected: FAIL — `AttributeError: 'ArbBot' object has no attribute '_validate_recent_trades'`

**Step 3: Add `_validate_recent_trades` to ArbBot**

In `src/main.py`, add after `_on_orderbook_update`:

```python
    async def _validate_recent_trades(self, tickers: list[str]) -> bool:
        if not self.risk_profile.require_recent_trades:
            return True
        for ticker in tickers:
            try:
                resp = await self.api.get_market_trades(ticker)
                if not resp.get("trades"):
                    logger.info("No recent trades for %s, skipping arb", ticker)
                    return False
            except Exception:
                logger.exception("Failed to check recent trades for %s", ticker)
                return False
        return True
```

Modify `_execute_and_track` in `src/main.py` to call validation before execution:

```python
    async def _execute_and_track(self, signal):
        try:
            tickers = [t for t, _ in signal.legs]
            if not await self._validate_recent_trades(tickers):
                logger.info("Recent trades check failed for %s, skipping", signal.event_ticker)
                return
            await self.executor.execute(signal)
            self._stats["arbs_executed"] += 1
        except Exception:
            logger.exception("Failed to execute arb for %s", signal.event_ticker)
            self._stats["arbs_failed"] += 1
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_recent_trades.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/main.py tests/test_recent_trades.py
git commit -m "feat: add async recent-trades validation before execution"
```

---

### Task 6: Tiered Auto-Unwind

**Files:**
- Modify: `src/executor.py` (add `_unwind_partial_fill` method, call from `_monitor_fills`)
- Modify: `tests/test_executor.py` (add unwind tests)

**Step 1: Write failing tests**

Add to `tests/test_executor.py`:

```python
from src.risk import load_risk_profile


def _make_executor_with_profile(mode="conservative", fill_timeout=1):
    profile = load_risk_profile(mode, {})
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.46", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.99", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = MagicMock()
    positions.record_fill = MagicMock()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=fill_timeout, risk_profile=profile,
    ), api, positions


def test_partial_fill_triggers_unwind():
    """Regression: 2026-05-12 — one leg fills, other doesn't. Unwind must fire."""
    executor, api, positions = _make_executor_with_profile(mode="conservative", fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    # Should have detected M2 as immediately filled
    assert executor.is_event_blacklisted("E1")

    # Unwind should have placed a buy-back order for M2
    # Check that batch_create_orders was called at least twice:
    # once for the original arb, then for the unwind
    assert api.batch_create_orders.call_count >= 2


def test_immediate_fills_are_tracked():
    """Regression: executor must parse status=executed from batch response."""
    executor, api, positions = _make_executor_with_profile(fill_timeout=1)
    signal = TradeSignal(
        event_ticker="E1",
        legs=[("M1", 0.46), ("M2", 0.99)],
        net_profit=0.43, profit_pct=43.0, exposure_ratio=1.3,
    )
    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    # positions.record_fill should have been called for the immediate fill on M2
    positions.record_fill.assert_called()
    call_args = [c.kwargs for c in positions.record_fill.call_args_list]
    tickers_filled = [c.get("ticker", "") for c in call_args]
    assert "M2" in tickers_filled
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_executor.py::test_partial_fill_triggers_unwind -v`
Expected: FAIL — `TypeError: ExecutionManager.__init__() got an unexpected keyword argument 'risk_profile'`

**Step 3: Modify ExecutionManager**

Update `__init__` to accept `risk_profile`:

```python
from src.risk import RiskProfile

class ExecutionManager:
    def __init__(self, api: KalshiAPI, positions: PositionTracker,
                 fill_timeout_secs: int, risk_profile: RiskProfile | None = None):
        self.api = api
        self.positions = positions
        self.fill_timeout_secs = fill_timeout_secs
        self._executing = False
        self._active: ArbExecution | None = None
        self._failed_events: set[str] = set()
        self._unwind_phase1_secs = risk_profile.unwind_phase1_secs if risk_profile else 15
        self._unwind_phase2_secs = risk_profile.unwind_phase2_secs if risk_profile else 30
        self._unwind_price_step_cents = risk_profile.unwind_price_step_cents if risk_profile else 3
```

Add the unwind method after `_monitor_fills`:

```python
    async def _unwind_partial_fill(self, execution: ArbExecution):
        filled_tickers = []
        for o_list_item in execution._batch_response:
            inner = o_list_item.get("order", o_list_item)
            if inner.get("order_id") in execution.filled:
                filled_tickers.append((
                    inner.get("ticker", ""),
                    float(inner.get("yes_price_dollars", 0)),
                    int(float(inner.get("fill_count_fp", 0))),
                ))

        for ticker, fill_price, qty in filled_tickers:
            if qty <= 0:
                continue
            logger.warning("Unwinding %d contracts of %s (filled @ %.2f)", qty, ticker, fill_price)

            step = self._unwind_price_step_cents / 100.0

            # Phase 1: tight limit
            phase1_price = min(fill_price + step, 0.99)
            phase1_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": round(phase1_price * 100), "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase1_order)
            order_inner = resp.get("orders", [{}])[0].get("order", resp.get("orders", [{}])[0])
            if order_inner.get("status") == "executed":
                logger.info("Unwind phase 1 filled for %s @ %.2f", ticker, phase1_price)
                continue
            phase1_oid = order_inner.get("order_id", "")

            await asyncio.sleep(self._unwind_phase1_secs)

            # Check if filled via WS in the meantime
            if phase1_oid in (execution.filled if self._active else {}):
                logger.info("Unwind phase 1 filled (via WS) for %s", ticker)
                continue

            # Phase 2: wider limit
            if phase1_oid:
                await self.api.cancel_order(phase1_oid)
            phase2_price = min(fill_price + 2 * step, 0.99)
            phase2_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": round(phase2_price * 100), "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase2_order)
            order_inner = resp.get("orders", [{}])[0].get("order", resp.get("orders", [{}])[0])
            if order_inner.get("status") == "executed":
                logger.info("Unwind phase 2 filled for %s @ %.2f", ticker, phase2_price)
                continue
            phase2_oid = order_inner.get("order_id", "")

            await asyncio.sleep(self._unwind_phase2_secs - self._unwind_phase1_secs)

            # Phase 3: market order
            if phase2_oid:
                await self.api.cancel_order(phase2_oid)
            phase3_order = [{
                "ticker": ticker, "action": "buy", "side": "yes",
                "type": "limit", "yes_price": 99, "count": qty,
            }]
            resp = await self.api.batch_create_orders(phase3_order)
            order_inner = resp.get("orders", [{}])[0].get("order", resp.get("orders", [{}])[0])
            logger.warning("Unwind phase 3 for %s: %s @ $0.99", ticker, order_inner.get("status"))
```

Store the batch response on ArbExecution for the unwind to use. Add to ArbExecution dataclass:

```python
@dataclass
class ArbExecution:
    signal: TradeSignal
    order_ids: list[str] = field(default_factory=list)
    filled: dict[str, float] = field(default_factory=dict)
    started_at: float = 0.0
    batch_response: list[dict] = field(default_factory=list)
```

In `execute()`, store the response: `execution.batch_response = order_list`

In `_monitor_fills`, replace the blacklist block to also spawn unwind:

```python
            if filled_count > 0:
                logger.error(
                    "PARTIAL FILL on %s: %d legs filled, %d cancelled — UNHEDGED EXPOSURE",
                    execution.signal.event_ticker, filled_count, len(unfilled),
                )
                self._failed_events.add(execution.signal.event_ticker)
                asyncio.create_task(self._unwind_partial_fill(execution))
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_executor.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/executor.py tests/test_executor.py
git commit -m "feat: add tiered auto-unwind for partial fills"
```

---

### Task 7: Wire Everything in main.py

**Files:**
- Modify: `src/main.py` (construct RiskProfile, pass to engine and executor)

**Step 1: Verify current state compiles**

Run: `python3 -c "from src.main import ArbBot; print('OK')"`
Expected: May fail if Config fields changed. Fix any import errors.

**Step 2: Update ArbBot.__init__**

Replace the engine and executor construction (lines 29-41):

```python
        from src.risk import load_risk_profile
        self.risk_profile = load_risk_profile(self.cfg.risk_mode, self.cfg.strategy_overrides)

        self.engine = ArbEngine(risk_profile=self.risk_profile)
        self.positions = PositionTracker()
        self.executor = ExecutionManager(
            api=self.api,
            positions=self.positions,
            fill_timeout_secs=self.cfg.fill_timeout_secs,
            risk_profile=self.risk_profile,
        )
```

Add risk mode to the startup log in `run()`:

```python
        logger.info("Starting Kalshi Arb Bot in %s mode (risk: %s)",
                     self.cfg.mode.upper(), self.cfg.risk_mode)
```

**Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add src/main.py
git commit -m "feat: wire RiskProfile into ArbBot, engine, and executor"
```

---

### Task 8: MCP Server Enhancements

**Files:**
- Modify: `src/mcp_server.py` (add `get_positions` and `get_risk_profile` tools)

**Step 1: Add tools**

```python
@mcp.tool()
async def get_positions() -> str:
    """View all current positions and balance without making changes."""
    api = await _get_api()
    try:
        positions_resp = await api.get_positions()
        market_positions = positions_resp.get("market_positions", [])

        lines = []
        for mp in market_positions:
            qty = float(mp.get("position_fp", "0"))
            if qty != 0:
                ticker = mp["ticker"]
                exposure = mp.get("market_exposure_dollars", "0")
                lines.append(f"  {ticker}: {int(qty)} contracts, exposure ${exposure}")

        if not lines:
            lines.append("No open positions")

        balance = await api.get_balance()
        cash = balance.get("balance", 0) / 100
        portfolio = balance.get("portfolio_value", 0) / 100
        lines.append(f"\nBalance: ${cash:.2f} cash, ${portfolio:.2f} portfolio")
        return "\n".join(lines)
    finally:
        await api.close()


@mcp.tool()
async def get_risk_profile() -> str:
    """Show the active risk profile and all thresholds."""
    from src.risk import load_risk_profile
    cfg = load_config(CONFIG_PATH)
    risk_mode = cfg.risk_mode
    profile = load_risk_profile(risk_mode, cfg.strategy_overrides)

    lines = [
        f"Risk mode: {risk_mode}",
        f"  min_volume_24h: {profile.min_volume_24h}",
        f"  min_bid_depth: {profile.min_bid_depth}",
        f"  min_profit_pct: {profile.min_profit_pct}%",
        f"  require_recent_trades: {profile.require_recent_trades}",
        f"  max_exposure_ratio: {profile.max_exposure_ratio}",
        f"  near_term_hours: {profile.near_term_hours}",
        f"  hurdle_rate_annual_pct: {profile.hurdle_rate_annual_pct}%",
        f"  unwind_phase1_secs: {profile.unwind_phase1_secs}",
        f"  unwind_phase2_secs: {profile.unwind_phase2_secs}",
        f"  unwind_price_step_cents: {profile.unwind_price_step_cents}",
    ]

    if cfg.strategy_overrides:
        lines.append(f"\nOverrides applied: {cfg.strategy_overrides}")

    return "\n".join(lines)
```

**Step 2: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: add get_positions and get_risk_profile MCP tools"
```

---

### Task 9: Regression Tests from Real Loss Scenarios

**Files:**
- Create: `tests/test_regression.py`

**Step 1: Write all regression tests**

```python
# tests/test_regression.py
"""
Regression tests derived from the 2026-05-12 trading session where the bot
lost money due to phantom liquidity and untracked partial fills.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.engine import ArbEngine
from src.executor import ExecutionManager
from src.models import Orderbook, OrderbookLevel, TradeSignal
from src.positions import PositionTracker
from src.risk import load_risk_profile


# --- Helpers ---

def _conservative_engine():
    return ArbEngine(risk_profile=load_risk_profile("conservative", {}))


def _partial_fill_executor(fill_timeout=1, mode="conservative"):
    """Executor where M2 fills immediately but M1 goes resting."""
    profile = load_risk_profile(mode, {})
    api = MagicMock()
    api.build_sell_order = MagicMock(side_effect=lambda ticker, yes_price, quantity: {
        "ticker": ticker, "action": "sell", "side": "yes",
        "type": "limit", "yes_price": round(yes_price * 100), "count": quantity,
    })
    api.batch_create_orders = AsyncMock(return_value={
        "orders": [
            {"order": {"order_id": "o1", "ticker": "M1", "status": "resting",
                       "yes_price_dollars": "0.4600", "fill_count_fp": "0.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
            {"order": {"order_id": "o2", "ticker": "M2", "status": "executed",
                       "yes_price_dollars": "0.9900", "fill_count_fp": "1.00",
                       "action": "sell", "side": "yes", "initial_count_fp": "1.00"}},
        ]
    })
    api.batch_cancel_orders = AsyncMock(return_value={})
    api.cancel_order = AsyncMock(return_value={})
    positions = PositionTracker()
    return ExecutionManager(
        api=api, positions=positions,
        fill_timeout_secs=fill_timeout, risk_profile=profile,
    ), api, positions


def _medlan_signal():
    return TradeSignal(
        event_ticker="KXATPSETWINNER-26MAY12MEDLAN-1",
        legs=[("KXATPSETWINNER-26MAY12MEDLAN-1-MED", 0.46),
              ("KXATPSETWINNER-26MAY12MEDLAN-1-LAN", 0.99)],
        net_profit=0.4319, profit_pct=43.19, exposure_ratio=1.29,
    )


# --- Tests ---

def test_phantom_liquidity_rejected_by_volume_check():
    """The MEDLAN event had bids but zero volume on the MED leg."""
    engine = _conservative_engine()
    orderbooks = {
        "KXATPSETWINNER-MED": Orderbook(
            yes_bids=[OrderbookLevel(price=0.46, quantity=10)], no_bids=[]),
        "KXATPSETWINNER-LAN": Orderbook(
            yes_bids=[OrderbookLevel(price=0.99, quantity=10)], no_bids=[]),
    }
    meta = {
        "KXATPSETWINNER-MED": {"volume_24h": 0},
        "KXATPSETWINNER-LAN": {"volume_24h": 500},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    assert signal is None, "Should reject arb when any leg has zero volume in conservative mode"


def test_partial_fill_detection_counts_correctly():
    """Executor must count 1/2 filled (not 0/2) when batch returns one executed leg."""
    executor, api, positions = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    # M2 should have been recorded as a fill
    m2_pos = positions.get_position("M2")
    assert m2_pos is not None, "M2 fill was not tracked from batch response"
    assert m2_pos.quantity == 1


def test_partial_fill_blacklists_event():
    """After partial fill + timeout, the event must be blacklisted."""
    executor, _, _ = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))
    assert executor.is_event_blacklisted(signal.event_ticker)


def test_repeat_execution_prevented():
    """Same event should not re-execute after a partial fill failure."""
    executor, api, _ = _partial_fill_executor(fill_timeout=1)
    signal = _medlan_signal()

    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))
    assert executor.is_event_blacklisted(signal.event_ticker)

    # Second execution attempt should be skipped via blacklist
    # (In real code, main.py checks is_event_blacklisted before calling execute.
    #  Here we verify the flag is set.)
    assert executor.is_event_blacklisted("KXATPSETWINNER-26MAY12MEDLAN-1")


def test_unwind_fires_on_partial_fill():
    """After partial fill, unwind must place a buy-back order."""
    executor, api, _ = _partial_fill_executor(fill_timeout=1, mode="conservative")
    signal = _medlan_signal()

    asyncio.get_event_loop().run_until_complete(executor.execute(signal, quantity=1))

    # batch_create_orders called for: (1) original arb, (2+) unwind phases
    assert api.batch_create_orders.call_count >= 2, \
        f"Expected unwind order, got {api.batch_create_orders.call_count} batch calls"

    # The unwind call should be a BUY order
    unwind_call = api.batch_create_orders.call_args_list[1]
    unwind_orders = unwind_call[0][0] if unwind_call[0] else unwind_call[1].get("orders", [])
    assert unwind_orders[0]["action"] == "buy"
    assert unwind_orders[0]["ticker"] == "M2"


def test_asymmetric_fill_rejected_conservative():
    """Conservative mode rejects arb where low-prob leg has thin depth."""
    profile = load_risk_profile("conservative", {})
    engine = ArbEngine(risk_profile=profile)
    orderbooks = {
        "M-FAVORITE": Orderbook(
            yes_bids=[OrderbookLevel(price=0.99, quantity=100)], no_bids=[]),
        "M-UNDERDOG": Orderbook(
            yes_bids=[OrderbookLevel(price=0.46, quantity=1)], no_bids=[]),
    }
    meta = {
        "M-FAVORITE": {"volume_24h": 500},
        "M-UNDERDOG": {"volume_24h": 100},
    }
    signal = engine.evaluate("E1", orderbooks, market_metadata=meta)
    # Conservative requires min_bid_depth=5, underdog has depth=1
    assert signal is None, "Should reject: underdog leg has depth 1, conservative requires 5"
```

**Step 2: Run tests**

Run: `python3 -m pytest tests/test_regression.py -v`
Expected: All 6 PASS

**Step 3: Commit**

```bash
git add tests/test_regression.py
git commit -m "test: add regression tests from 2026-05-12 loss scenarios"
```

---

### Task 10: Final Integration Test and Cleanup

**Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

**Step 2: Verify bot imports cleanly**

Run: `python3 -c "from src.main import ArbBot; print('OK')"`
Expected: `OK`

**Step 3: Update CLAUDE.md with new architecture details**

Add risk mode documentation to the Architecture section of `CLAUDE.md`.

**Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with risk modes architecture"
```
