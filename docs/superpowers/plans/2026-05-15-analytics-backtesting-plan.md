# Analytics, Backtesting & Strategy Tuning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:using-git-worktrees to create an isolated worktree before starting implementation.

**Goal:** Add data recording, replay-based backtesting, performance analytics, MCP tools, and strategy review skills to the Kalshi arb bot so trading strategies can be measured and tuned with evidence.

**Architecture:** A SQLite-backed `DataRecorder` records orderbook snapshots, signal evaluations, executions, fills, and balances inline at existing decision points. A `ReplayEngine` feeds recorded state back through `ArbEngine.evaluate*()` with parameter sweeps. An `Analytics` module computes per-strategy PnL, rejection funnels, and near-miss distributions. Five new MCP tools expose this data conversationally. Three new skills guide post-run analysis, parameter tuning, and strategy-change review.

**Tech Stack:** Python 3, SQLite3 (stdlib), existing `src/` modules (engine, fees, risk, models), FastMCP

**Spec:** `docs/superpowers/specs/2026-05-15-analytics-backtesting-design.md`

**Worktree:** Create an isolated git worktree on branch `feat/analytics-backtesting` before starting. Use `superpowers:using-git-worktrees` to set it up. All work happens in the worktree; merge back to main when complete.

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `src/recorder.py` | New | `DataRecorder` class — SQLite schema init, `record_signal()`, `record_execution()`, `record_fill()`, `record_orderbook_snapshot()`, `record_balance()`, `start_session()`, `end_session()` |
| `src/replay.py` | New | `ReplayEngine` class — load snapshots, reconstruct orderbooks, parameter sweep, train/test split, plateau detection. CLI entry point via `__main__`. |
| `src/analytics.py` | New | `Analytics` class — strategy breakdown, rejection funnel, partial fill analysis, balance curve, near-miss distribution. CLI entry point via `__main__`. |
| `src/config.py` | Modify | Parse `recording:` section from config, add `RecordingConfig` dataclass |
| `src/dispatch.py` | Modify | Call `recorder.record_signal()` after each evaluate call |
| `src/executor.py` | Modify | Call `recorder.record_execution()` after batch order responses |
| `src/positions.py` | Modify | Call `recorder.record_fill()` on fills |
| `src/main.py` | Modify | Init `DataRecorder`, start/end session, periodic snapshot/balance tasks |
| `src/mcp_server.py` | Modify | Add 5 new MCP tools |
| `config.example.yaml` | Modify | Add `recording:` section |
| `.gitignore` | Modify | Add `data/` and `*.db` |
| `.claude/skills/strategy-tuning/SKILL.md` | New | Guided parameter tuning session skill |
| `.claude/skills/post-run-analyst/SKILL.md` | New | Post-run debrief subagent skill |
| `.claude/skills/strategy-review/SKILL.md` | New | Financial review of strategy-affecting changes |
| `.claude/skills/analyze-positions/SKILL.md` | Modify | Extend with performance report call |
| `.claude/skills/live-test/SKILL.md` | Modify | Wire in post-run-analyst subagent |
| `tests/test_recorder.py` | New | DataRecorder unit tests |
| `tests/test_replay.py` | New | ReplayEngine unit tests |
| `tests/test_analytics.py` | New | Analytics unit tests |

---

## Task 1: Configuration — `RecordingConfig` and Config Parsing

**Files:**
- Modify: `src/config.py`
- Modify: `config.example.yaml`
- Modify: `.gitignore`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_recording_config_defaults(tmp_path):
    """Recording config should have sensible defaults when section is omitted."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: test
    private_key_path: /tmp/fake.pem
strategy:
  risk_mode: conservative
""")
    cfg = load_config(str(cfg_file))
    assert cfg.recording_enabled is True
    assert cfg.recording_db_path == "data/arb_history.db"
    assert cfg.recording_snapshot_interval_secs == 5
    assert cfg.recording_balance_poll_interval_secs == 300


def test_recording_config_custom(tmp_path):
    """Recording config should read custom values from yaml."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: test
    private_key_path: /tmp/fake.pem
strategy:
  risk_mode: conservative
recording:
  enabled: false
  db_path: custom/path.db
  snapshot_interval_secs: 10
  balance_poll_interval_secs: 600
""")
    cfg = load_config(str(cfg_file))
    assert cfg.recording_enabled is False
    assert cfg.recording_db_path == "custom/path.db"
    assert cfg.recording_snapshot_interval_secs == 10
    assert cfg.recording_balance_poll_interval_secs == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py::test_recording_config_defaults tests/test_config.py::test_recording_config_custom -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'recording_enabled'`

- [ ] **Step 3: Add recording fields to Config dataclass and load_config**

In `src/config.py`, add four fields to the `Config` dataclass after `log_file`:

```python
    recording_enabled: bool
    recording_db_path: str
    recording_snapshot_interval_secs: int
    recording_balance_poll_interval_secs: int
```

In `load_config()`, after `logging_cfg = raw.get("logging", {})`, add:

```python
    recording_cfg = raw.get("recording", {})
```

And in the `return Config(...)` call, add these four lines after `log_file=...`:

```python
        recording_enabled=recording_cfg.get("enabled", True),
        recording_db_path=recording_cfg.get("db_path", "data/arb_history.db"),
        recording_snapshot_interval_secs=int(recording_cfg.get("snapshot_interval_secs", 5)),
        recording_balance_poll_interval_secs=int(recording_cfg.get("balance_poll_interval_secs", 300)),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Update config.example.yaml**

Add at the end of `config.example.yaml`, before or after the `logging:` section:

```yaml

recording:
  enabled: true                      # Record orderbook snapshots, signals, fills (default: true)
  db_path: data/arb_history.db       # SQLite database path (default: data/arb_history.db)
  snapshot_interval_secs: 5          # How often to snapshot each event's orderbooks (default: 5)
  balance_poll_interval_secs: 300    # How often to poll and record balance (default: 300)
```

- [ ] **Step 6: Update .gitignore**

Add to `.gitignore`:

```
# Analytics data
data/
*.db
```

- [ ] **Step 7: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add src/config.py config.example.yaml .gitignore tests/test_config.py
git commit -m "feat: add recording config fields to Config dataclass"
```

---

## Task 2: DataRecorder — Schema and Core Record Methods

**Files:**
- Create: `src/recorder.py`
- Create: `tests/test_recorder.py`

- [ ] **Step 1: Write failing tests for DataRecorder**

Create `tests/test_recorder.py`:

```python
import json
import sqlite3
import time

import pytest

from src.recorder import DataRecorder


@pytest.fixture
def recorder(tmp_path):
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    return rec


def test_start_and_end_session(recorder):
    sid = recorder.start_session({"risk_mode": "conservative"})
    assert sid == 1
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchall()
    assert len(rows) == 1
    assert rows[0][2] is None  # end_time is NULL

    recorder.end_session()
    rows = recorder._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchall()
    assert rows[0][2] is not None  # end_time is set


def test_record_signal(recorder):
    recorder.start_session({})
    recorder.record_signal(
        event_ticker="EVT-A",
        strategy="taker",
        outcome="fire",
        reject_reason=None,
        bid_sum=1.08,
        ask_sum=None,
        profit_pct=2.5,
        exposure_ratio=1.5,
        legs=[{"ticker": "MKT-1", "price": 0.55, "depth": 10}],
        metadata={"extra": "data"},
    )
    rows = recorder._conn.execute("SELECT * FROM signal_evaluations").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "EVT-A"  # event_ticker
    assert rows[0][4] == "taker"  # strategy
    assert rows[0][5] == "fire"   # outcome


def test_record_execution(recorder):
    recorder.start_session({})
    recorder.record_execution(
        event_ticker="EVT-A",
        strategy="taker",
        legs=[{"ticker": "MKT-1", "action": "sell", "price": 0.55, "quantity": 1}],
        result="full_fill",
        fill_details={"order_1": "executed"},
        unwind_cost=0.0,
    )
    rows = recorder._conn.execute("SELECT * FROM executions").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "EVT-A"
    assert rows[0][5] == "full_fill"


def test_record_fill(recorder):
    recorder.start_session({})
    recorder.record_fill(
        ticker="MKT-1",
        side="yes",
        action="sell",
        price=0.55,
        quantity=1,
        realized_pnl=None,
    )
    rows = recorder._conn.execute("SELECT * FROM fills").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == "MKT-1"
    assert rows[0][6] == 0.55


def test_record_balance(recorder):
    recorder.start_session({})
    recorder.record_balance(cash_cents=10000, portfolio_cents=10500)
    rows = recorder._conn.execute("SELECT * FROM balances").fetchall()
    assert len(rows) == 1
    assert rows[0][3] == 10000
    assert rows[0][4] == 10500


def test_record_orderbook_snapshot(recorder):
    recorder.start_session({})
    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A",
        market_ticker="MKT-1",
        yes_bids={55: 10.0, 50: 5.0},
        no_bids={45: 8.0},
    )
    rows = recorder._conn.execute("SELECT * FROM orderbook_snapshots").fetchall()
    assert len(rows) == 1
    data = json.loads(rows[0][5])  # yes_bids_json
    assert data == {"55": 10.0, "50": 5.0}


def test_no_recording_without_session(recorder):
    """record_signal should silently no-op if no session is active."""
    recorder.record_signal(
        event_ticker="EVT-A", strategy="taker", outcome="fire",
        reject_reason=None, bid_sum=1.08, ask_sum=None,
        profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None,
    )
    rows = recorder._conn.execute("SELECT * FROM signal_evaluations").fetchall()
    assert len(rows) == 0


def test_recorder_disabled():
    """DataRecorder(None) creates a no-op recorder."""
    rec = DataRecorder(None)
    rec.start_session({})
    rec.record_signal(
        event_ticker="EVT-A", strategy="taker", outcome="fire",
        reject_reason=None, bid_sum=1.08, ask_sum=None,
        profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None,
    )
    # Should not raise — all methods are no-ops
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.recorder'`

- [ ] **Step 3: Implement DataRecorder**

Create `src/recorder.py`:

```python
import json
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL,
    config_json TEXT
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    yes_bids_json TEXT NOT NULL,
    no_bids_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reject_reason TEXT,
    bid_sum REAL,
    ask_sum REAL,
    profit_pct REAL,
    exposure_ratio REAL,
    leg_count INTEGER,
    legs_json TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    legs_json TEXT NOT NULL,
    result TEXT NOT NULL,
    fill_details_json TEXT,
    unwind_cost REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    realized_pnl REAL
);

CREATE TABLE IF NOT EXISTS balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    cash_cents INTEGER NOT NULL,
    portfolio_cents INTEGER NOT NULL
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_snap_time ON orderbook_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snap_event ON orderbook_snapshots(event_ticker);
CREATE INDEX IF NOT EXISTS idx_sig_time ON signal_evaluations(timestamp);
CREATE INDEX IF NOT EXISTS idx_sig_strategy ON signal_evaluations(strategy);
CREATE INDEX IF NOT EXISTS idx_sig_outcome ON signal_evaluations(outcome);
CREATE INDEX IF NOT EXISTS idx_exec_time ON executions(timestamp);
CREATE INDEX IF NOT EXISTS idx_fill_time ON fills(timestamp);
CREATE INDEX IF NOT EXISTS idx_fill_ticker ON fills(ticker);
CREATE INDEX IF NOT EXISTS idx_bal_time ON balances(timestamp);
"""


class DataRecorder:
    def __init__(self, db_path: str | None):
        self._enabled = db_path is not None
        self._conn: sqlite3.Connection | None = None
        self._session_id: int | None = None
        if self._enabled:
            self._conn = sqlite3.connect(db_path)
            self._conn.executescript(SCHEMA_SQL)
            self._conn.executescript(INDEX_SQL)
            self._conn.commit()

    def start_session(self, config: dict) -> int | None:
        if not self._enabled:
            return None
        now = time.time()
        cur = self._conn.execute(
            "INSERT INTO sessions (start_time, config_json) VALUES (?, ?)",
            (now, json.dumps(config)),
        )
        self._conn.commit()
        self._session_id = cur.lastrowid
        return self._session_id

    def end_session(self):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            "UPDATE sessions SET end_time = ? WHERE id = ?",
            (time.time(), self._session_id),
        )
        self._conn.commit()

    def record_signal(
        self,
        event_ticker: str,
        strategy: str,
        outcome: str,
        reject_reason: str | None,
        bid_sum: float | None,
        ask_sum: float | None,
        profit_pct: float | None,
        exposure_ratio: float | None,
        legs: list[dict] | None,
        metadata: dict | None,
    ):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            """INSERT INTO signal_evaluations
               (session_id, timestamp, event_ticker, strategy, outcome,
                reject_reason, bid_sum, ask_sum, profit_pct, exposure_ratio,
                leg_count, legs_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._session_id, time.time(), event_ticker, strategy, outcome,
                reject_reason, bid_sum, ask_sum, profit_pct, exposure_ratio,
                len(legs) if legs else 0,
                json.dumps(legs) if legs else None,
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._conn.commit()

    def record_execution(
        self,
        event_ticker: str,
        strategy: str,
        legs: list[dict],
        result: str,
        fill_details: dict | None,
        unwind_cost: float,
    ):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            """INSERT INTO executions
               (session_id, timestamp, event_ticker, strategy, legs_json,
                result, fill_details_json, unwind_cost)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self._session_id, time.time(), event_ticker, strategy,
                json.dumps(legs), result,
                json.dumps(fill_details) if fill_details else None,
                unwind_cost,
            ),
        )
        self._conn.commit()

    def record_fill(
        self,
        ticker: str,
        side: str,
        action: str,
        price: float,
        quantity: float,
        realized_pnl: float | None,
    ):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            """INSERT INTO fills
               (session_id, timestamp, ticker, side, action, price, quantity, realized_pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (self._session_id, time.time(), ticker, side, action, price, quantity, realized_pnl),
        )
        self._conn.commit()

    def record_balance(self, cash_cents: int, portfolio_cents: int):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            """INSERT INTO balances
               (session_id, timestamp, cash_cents, portfolio_cents)
               VALUES (?, ?, ?, ?)""",
            (self._session_id, time.time(), cash_cents, portfolio_cents),
        )
        self._conn.commit()

    def record_orderbook_snapshot(
        self,
        event_ticker: str,
        market_ticker: str,
        yes_bids: dict[int, float],
        no_bids: dict[int, float],
    ):
        if not self._enabled or self._session_id is None:
            return
        self._conn.execute(
            """INSERT INTO orderbook_snapshots
               (session_id, timestamp, event_ticker, market_ticker,
                yes_bids_json, no_bids_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                self._session_id, time.time(), event_ticker, market_ticker,
                json.dumps(yes_bids), json.dumps(no_bids),
            ),
        )
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recorder.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/recorder.py tests/test_recorder.py
git commit -m "feat: DataRecorder with SQLite schema and record methods"
```

---

## Task 3: Wire DataRecorder into Bot Lifecycle

**Files:**
- Modify: `src/main.py`
- Modify: `src/dispatch.py`
- Modify: `src/executor.py`
- Modify: `src/positions.py`
- Test: `tests/test_dispatch.py` (add recording assertions)

- [ ] **Step 1: Write failing tests for recorder integration in dispatcher**

Add to `tests/test_dispatch.py`:

```python
def test_dispatcher_records_fire_signal(self):
    """Dispatcher should call recorder.record_signal with outcome='fire' when a signal fires."""
    recorder = MagicMock()
    self.dispatcher.recorder = recorder
    # Set up a profitable arb
    self.ob_mgr.apply_snapshot("MKT-1", {"yes_dollars_fp": [("0.55", "10")], "no_dollars_fp": []})
    self.ob_mgr.apply_snapshot("MKT-2", {"yes_dollars_fp": [("0.56", "10")], "no_dollars_fp": []})
    signal = self.dispatcher.process_orderbook_update("MKT-1")
    if signal:
        recorder.record_signal.assert_called()
        call_kwargs = recorder.record_signal.call_args
        assert call_kwargs[1]["outcome"] == "fire" or call_kwargs[0][2] == "fire"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dispatch.py::TestDispatcher::test_dispatcher_records_fire_signal -v`
Expected: FAIL with `AttributeError: 'Dispatcher' object has no attribute 'recorder'`

- [ ] **Step 3: Add recorder parameter to Dispatcher.__init__**

In `src/dispatch.py`, add `recorder=None` parameter to `__init__`:

```python
    def __init__(
        self,
        engine: ArbEngine,
        executor: ExecutionManager,
        maker,
        orderbook_mgr: OrderbookManager,
        market_metadata: dict[str, dict],
        signal_cooldown: float = 60.0,
        enable_buy_side_arb: bool = True,
        near_expiry_window_minutes: int = 0,
        monotone_registry=None,
        event_total_markets: dict[str, int] | None = None,
        recorder=None,
    ):
```

Add `self.recorder = recorder` in the body.

- [ ] **Step 4: Add record_signal calls to process_orderbook_update**

In `Dispatcher.process_orderbook_update()`, after the taker `evaluate()` call and its handling:

Add a helper method to `Dispatcher` that computes bid_sum from signal legs and records:

```python
    def _record_fire(self, signal: TradeSignal, strategy: str):
        if not self.recorder:
            return
        bid_sum = sum(p for _, p in signal.legs) if signal.signal_type != "buy_side_taker" else None
        ask_sum = sum(p for _, p in signal.legs) if signal.signal_type == "buy_side_taker" else None
        self.recorder.record_signal(
            event_ticker=signal.event_ticker, strategy=strategy, outcome="fire",
            reject_reason=None, bid_sum=bid_sum, ask_sum=ask_sum,
            profit_pct=signal.profit_pct, exposure_ratio=signal.exposure_ratio,
            legs=[{"ticker": t, "price": p} for t, p in signal.legs],
            metadata={"signal_type": signal.signal_type, "quantity": signal.quantity},
        )
```

Call `self._record_fire(signal, "taker")` before the `return signal` on line 78 (taker fire).

Call `self._record_fire(buy_signal, "buy_side")` before the `return buy_signal` on line 104.

Call `self._record_fire(ne_signal, "near_expiry")` before the `return ne_signal` on line 123.

Call `self._record_fire(mono_signal, "monotone")` before the `return mono_signal` on line 155.

For **reject recording**: Add a `_record_reject` helper that the dispatcher calls when a signal is blocked by a guard:

```python
    def _record_reject(self, event_ticker: str, strategy: str, reason: str):
        if not self.recorder:
            return
        self.recorder.record_signal(
            event_ticker=event_ticker, strategy=strategy, outcome="reject",
            reject_reason=reason, bid_sum=None, ask_sum=None,
            profit_pct=None, exposure_ratio=None, legs=None, metadata=None,
        )
```

Call `self._record_reject(event_ticker, "taker", "blacklisted")` where `is_event_blacklisted` returns True (line 61).

Call `self._record_reject(event_ticker, "taker", "cooldown")` where cooldown check blocks (line 64).

Call `self._record_reject(event_ticker, "taker", "executing")` where `is_executing()` blocks (line 60).

- [ ] **Step 5: Add recorder to ExecutionManager**

In `src/executor.py`, add `recorder=None` parameter to `__init__`:

```python
    def __init__(self, api: KalshiAPI, positions: PositionTracker,
                 fill_timeout_secs: int, risk_profile: RiskProfile | None = None,
                 max_session_loss: float = 1.0, circuit_breaker_on_any_loss: bool = True,
                 recorder=None):
```

Add `self.recorder = recorder` in the body.

In `execute()`, after `await self._monitor_fills(execution)` on line 117, add:

```python
            if self.recorder:
                filled_count = len(execution.filled)
                total_count = len(execution.order_ids)
                if filled_count == total_count:
                    result = "full_fill"
                elif filled_count > 0:
                    result = "partial_fill"
                else:
                    result = "failed"
                self.recorder.record_execution(
                    event_ticker=signal.event_ticker,
                    strategy=signal.signal_type,
                    legs=[{"ticker": t, "action": (signal.leg_actions[i] if signal.leg_actions else "sell"), "price": p, "quantity": quantity} for i, (t, p) in enumerate(signal.legs)],
                    result=result,
                    fill_details={oid: price for oid, price in execution.filled.items()},
                    unwind_cost=0.0,
                )
```

- [ ] **Step 6: Add recorder to PositionTracker**

In `src/positions.py`, add `recorder=None` parameter to `__init__`:

```python
    def __init__(self, recorder=None):
        self._positions: dict[str, TrackedPosition] = {}
        self.realized_pnl: float = 0.0
        self.recorder = recorder
```

In `record_fill()`, at the end of the method (after all the logging), add:

```python
        if self.recorder:
            self.recorder.record_fill(
                ticker=ticker, side=side, action=action,
                price=price, quantity=quantity,
                realized_pnl=pnl if pos is not None and pos.opened_by != action else None,
            )
```

Note: `pnl` is only defined in the close branch. Restructure the end of `record_fill` so `pnl` is available:

Actually, the cleanest approach is to track `realized_this_fill` at the top of the method, set it in the close branch, and use it at the end:

At the top of `record_fill()`, add:
```python
        realized_this_fill = None
```

In the close branch (where `pnl` is computed), add:
```python
            realized_this_fill = pnl
```

Then at the very end:
```python
        if self.recorder:
            self.recorder.record_fill(
                ticker=ticker, side=side, action=action,
                price=price, quantity=quantity,
                realized_pnl=realized_this_fill,
            )
```

- [ ] **Step 7: Wire recorder into ArbBot.__init__ and run()**

In `src/main.py`, add import:

```python
from src.recorder import DataRecorder
```

In `ArbBot.__init__()`, after `self.risk_profile = ...`, add:

```python
        db_path = self.cfg.recording_db_path if self.cfg.recording_enabled else None
        self.recorder = DataRecorder(db_path)
```

Pass `recorder=self.recorder` to `PositionTracker`, `ExecutionManager`, and `Dispatcher`:

```python
        self.positions = PositionTracker(recorder=self.recorder)
        self.executor = ExecutionManager(
            ...,
            recorder=self.recorder,
        )
        self.dispatcher = Dispatcher(
            ...,
            recorder=self.recorder,
        )
```

In `ArbBot.run()`, after `self._setup_logging()` and before `logger.info("Starting...")`:

```python
        self.recorder.start_session({
            "mode": self.cfg.mode,
            "risk_mode": self.cfg.risk_mode,
            "recording_enabled": self.cfg.recording_enabled,
        })
```

In the `finally:` block of `run()`, before `self._print_summary()`:

```python
            self.recorder.end_session()
            self.recorder.close()
```

Add periodic snapshot and balance tasks. In `run()`, after creating `ob_task`:

```python
        if self.cfg.recording_enabled:
            snapshot_task = asyncio.create_task(self._snapshot_loop())
            balance_task = asyncio.create_task(self._balance_loop())
            tasks.extend([snapshot_task, balance_task])
```

Add the two loop methods to `ArbBot`:

```python
    async def _snapshot_loop(self):
        interval = self.cfg.recording_snapshot_interval_secs
        while True:
            await asyncio.sleep(interval)
            for event_ticker in list(self.discovery.event_tickers):
                for mt in self.orderbook_mgr.get_event_markets(event_ticker):
                    book = self.orderbook_mgr.get_orderbook(mt)
                    if book:
                        self.recorder.record_orderbook_snapshot(
                            event_ticker=event_ticker,
                            market_ticker=mt,
                            yes_bids=book.yes_bids,
                            no_bids=book.no_bids,
                        )

    async def _balance_loop(self):
        interval = self.cfg.recording_balance_poll_interval_secs
        while True:
            await asyncio.sleep(interval)
            try:
                bal = await self.api.get_balance()
                self.recorder.record_balance(
                    cash_cents=bal.get("balance", 0),
                    portfolio_cents=bal.get("portfolio_value", 0),
                )
            except Exception:
                logger.exception("Failed to record balance")
```

- [ ] **Step 8: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (existing tests should not break since recorder defaults to None)

- [ ] **Step 9: Commit**

```bash
git add src/main.py src/dispatch.py src/executor.py src/positions.py tests/test_dispatch.py
git commit -m "feat: wire DataRecorder into bot lifecycle — dispatch, executor, positions, main"
```

---

## Task 4: Engine Near-Miss Recording

**Files:**
- Modify: `src/engine.py`
- Test: `tests/test_engine.py`

The engine already has DEBUG-level near-miss logging. We add `recorder` to `ArbEngine` so near-miss signals get persisted to SQLite.

- [ ] **Step 1: Write failing test**

Add to `tests/test_engine.py`:

```python
from unittest.mock import MagicMock

def test_evaluate_records_near_miss(self):
    """Engine should call recorder.record_signal for taker near-misses."""
    recorder = MagicMock()
    self.engine.recorder = recorder
    books = {
        "MKT-1": Orderbook(yes_bids={49: 10}),
        "MKT-2": Orderbook(yes_bids={49: 10}),
    }
    # bid_sum = 0.98, which is >= 0.97 near-miss threshold but not profitable
    result = self.engine.evaluate("EVT-A", books)
    assert result is None
    recorder.record_signal.assert_called_once()
    call_kwargs = recorder.record_signal.call_args[1]
    assert call_kwargs["outcome"] == "near_miss"
    assert call_kwargs["strategy"] == "taker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine.py::TestArbEngine::test_evaluate_records_near_miss -v`
Expected: FAIL

- [ ] **Step 3: Add recorder to ArbEngine**

In `ArbEngine.__init__()`, add `recorder=None` parameter and `self.recorder = recorder`.

In `evaluate()`, at the near-miss logging point (line 63-64), add after the `logger.debug`:

```python
            if self.recorder:
                self.recorder.record_signal(
                    event_ticker=event_ticker, strategy="taker", outcome="near_miss",
                    reject_reason=None, bid_sum=bid_sum, ask_sum=None,
                    profit_pct=None, exposure_ratio=None,
                    legs=[{"ticker": t, "price": p, "depth": d} for t, p, d in legs],
                    metadata=None,
                )
```

In `evaluate_maker()`, at line 157 (the `logger.debug("maker near-miss ...")` point), add after the logger.debug:

```python
            if self.recorder:
                self.recorder.record_signal(
                    event_ticker=event_ticker, strategy="maker", outcome="near_miss",
                    reject_reason=None, bid_sum=bid_sum, ask_sum=None,
                    profit_pct=None, exposure_ratio=None,
                    legs=[{"ticker": t, "price": p, "depth": d} for t, p, d in legs],
                    metadata=None,
                )
```

In `_validate_legs()`, at line 112-116 (depth filter near-miss log) and line 123-127 (volume filter near-miss log), add after each `logger.debug`:

```python
                    if self.recorder:
                        self.recorder.record_signal(
                            event_ticker=event_ticker, strategy="taker", outcome="near_miss",
                            reject_reason="depth_filter",  # or "volume_filter" for the volume case
                            bid_sum=bid_sum, ask_sum=None,
                            profit_pct=None, exposure_ratio=None,
                            legs=[{"ticker": t, "price": p, "depth": d} for t, p, d in legs],
                            metadata=None,
                        )
```

- [ ] **Step 4: Wire recorder from ArbBot to ArbEngine**

In `src/main.py`, pass `recorder=self.recorder` to the `ArbEngine` constructor:

```python
        self.engine = ArbEngine(
            risk_profile=self.risk_profile,
            maker_max_horizon_hours=self.cfg.maker_max_horizon_hours,
            max_contracts_per_arb=self.cfg.max_contracts_per_arb,
            recorder=self.recorder,
        )
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_engine.py tests/test_recorder.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run full suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/engine.py src/main.py tests/test_engine.py
git commit -m "feat: record near-miss signals from engine to DataRecorder"
```

---

## Task 5: ReplayEngine — Load Snapshots and Parameter Sweep

**Files:**
- Create: `src/replay.py`
- Create: `tests/test_replay.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_replay.py`:

```python
import json
import time

import pytest

from src.models import Orderbook
from src.recorder import DataRecorder
from src.replay import ReplayEngine


@pytest.fixture
def db_with_data(tmp_path):
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    sid = rec.start_session({"risk_mode": "conservative"})

    # Insert two snapshots for the same event at the same timestamp
    now = time.time()
    rec._conn.execute(
        """INSERT INTO orderbook_snapshots
           (session_id, timestamp, event_ticker, market_ticker, yes_bids_json, no_bids_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (sid, now, "EVT-A", "MKT-1", json.dumps({"55": 10.0}), json.dumps({})),
    )
    rec._conn.execute(
        """INSERT INTO orderbook_snapshots
           (session_id, timestamp, event_ticker, market_ticker, yes_bids_json, no_bids_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (sid, now, "EVT-A", "MKT-2", json.dumps({"56": 10.0}), json.dumps({})),
    )
    rec._conn.commit()
    rec.end_session()
    rec.close()
    return db_path


def test_load_snapshots(db_with_data):
    engine = ReplayEngine(db_with_data)
    snapshots = engine.load_snapshots()
    assert len(snapshots) == 1  # one timestamp group
    ts, events = snapshots[0]
    assert "EVT-A" in events
    assert "MKT-1" in events["EVT-A"]
    assert "MKT-2" in events["EVT-A"]
    book = events["EVT-A"]["MKT-1"]
    assert isinstance(book, Orderbook)
    assert book.best_yes_bid() == 0.55


def test_sweep_single_param(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = engine.sweep({"min_profit_pct": [0.5, 1.0, 2.0, 3.0]})
    assert len(results) == 4
    for r in results:
        assert "params" in r
        assert "signal_count" in r
        assert "theoretical_profit" in r


def test_sweep_train_test_split(db_with_data):
    engine = ReplayEngine(db_with_data)
    results = engine.sweep(
        {"min_profit_pct": [0.5, 1.0]},
        train_end=time.time() + 1000,
        test_start=time.time() + 1000,
    )
    for r in results:
        assert "train_signal_count" in r
        assert "test_signal_count" in r
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_replay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.replay'`

- [ ] **Step 3: Implement ReplayEngine**

Create `src/replay.py`:

```python
import argparse
import json
import sqlite3
import sys
import time
from itertools import product

from src.engine import ArbEngine
from src.fees import arb_profit
from src.models import Orderbook
from src.risk import RiskProfile, load_risk_profile, PRESETS


class ReplayEngine:
    def __init__(self, db_path: str, risk_mode: str = "conservative"):
        self._conn = sqlite3.connect(db_path)
        self._risk_mode = risk_mode

    def load_snapshots(
        self, start: float | None = None, end: float | None = None,
    ) -> list[tuple[float, dict[str, dict[str, Orderbook]]]]:
        query = "SELECT timestamp, event_ticker, market_ticker, yes_bids_json, no_bids_json FROM orderbook_snapshots"
        conditions = []
        params = []
        if start is not None:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            conditions.append("timestamp <= ?")
            params.append(end)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp"

        rows = self._conn.execute(query, params).fetchall()

        grouped: dict[float, dict[str, dict[str, Orderbook]]] = {}
        for ts, event_ticker, market_ticker, yes_json, no_json in rows:
            if ts not in grouped:
                grouped[ts] = {}
            if event_ticker not in grouped[ts]:
                grouped[ts][event_ticker] = {}
            yes_bids = {int(k): v for k, v in json.loads(yes_json).items()}
            no_bids = {int(k): v for k, v in json.loads(no_json).items()}
            grouped[ts][event_ticker][market_ticker] = Orderbook(
                yes_bids=yes_bids, no_bids=no_bids,
            )

        return [(ts, events) for ts, events in sorted(grouped.items())]

    def sweep(
        self,
        param_ranges: dict[str, list[float]],
        start: float | None = None,
        end: float | None = None,
        train_end: float | None = None,
        test_start: float | None = None,
    ) -> list[dict]:
        snapshots = self.load_snapshots(start=start, end=end)
        if not snapshots:
            return []

        base_values = dict(PRESETS[self._risk_mode])
        param_names = list(param_ranges.keys())
        param_value_lists = [param_ranges[k] for k in param_names]

        results = []
        for combo in product(*param_value_lists):
            overrides = dict(zip(param_names, combo))
            profile = load_risk_profile(self._risk_mode, overrides)
            engine = ArbEngine(risk_profile=profile)

            if train_end is not None and test_start is not None:
                train_signals, train_profit = self._evaluate_snapshots(
                    engine, [s for s in snapshots if s[0] < train_end])
                test_signals, test_profit = self._evaluate_snapshots(
                    engine, [s for s in snapshots if s[0] >= test_start])
                results.append({
                    "params": overrides,
                    "train_signal_count": train_signals,
                    "train_theoretical_profit": round(train_profit, 6),
                    "test_signal_count": test_signals,
                    "test_theoretical_profit": round(test_profit, 6),
                    "signal_count": train_signals + test_signals,
                    "theoretical_profit": round(train_profit + test_profit, 6),
                })
            else:
                signal_count, total_profit = self._evaluate_snapshots(engine, snapshots)
                results.append({
                    "params": overrides,
                    "signal_count": signal_count,
                    "theoretical_profit": round(total_profit, 6),
                })

        return results

    def _evaluate_snapshots(
        self,
        engine: ArbEngine,
        snapshots: list[tuple[float, dict[str, dict[str, Orderbook]]]],
    ) -> tuple[int, float]:
        signal_count = 0
        total_profit = 0.0
        seen_events: set[str] = set()
        for ts, events in snapshots:
            for event_ticker, books in events.items():
                signal = engine.evaluate(event_ticker, books)
                if signal and event_ticker not in seen_events:
                    signal_count += 1
                    total_profit += signal.net_profit
                    seen_events.add(event_ticker)
                if not signal:
                    buy_signal = engine.evaluate_buy_side(event_ticker, books)
                    if buy_signal and event_ticker not in seen_events:
                        signal_count += 1
                        total_profit += buy_signal.net_profit
                        seen_events.add(event_ticker)
        return signal_count, total_profit

    def find_plateaus(self, results: list[dict], param_name: str, threshold: float = 0.10) -> list[tuple[float, float]]:
        if not results:
            return []
        sorted_results = sorted(results, key=lambda r: r["params"][param_name])
        profits = [r["theoretical_profit"] for r in sorted_results]
        values = [r["params"][param_name] for r in sorted_results]

        max_profit = max(abs(p) for p in profits) if profits else 1
        if max_profit == 0:
            return [(values[0], values[-1])]

        plateaus = []
        start_idx = 0
        for i in range(1, len(profits)):
            if abs(profits[i] - profits[i - 1]) / max_profit > threshold:
                if i - start_idx >= 2:
                    plateaus.append((values[start_idx], values[i - 1]))
                start_idx = i
        if len(profits) - start_idx >= 2:
            plateaus.append((values[start_idx], values[-1]))

        return plateaus

    def close(self):
        self._conn.close()


def _parse_sweep_arg(arg: str) -> tuple[str, list[float]]:
    name, range_str = arg.split("=", 1)
    parts = range_str.split(":")
    if len(parts) != 3:
        raise ValueError(f"Sweep format: name=start:end:step, got {arg}")
    start, end, step = float(parts[0]), float(parts[1]), float(parts[2])
    values = []
    v = start
    while v <= end + step / 2:
        values.append(round(v, 6))
        v += step
    return name, values


def main():
    parser = argparse.ArgumentParser(description="Replay engine for parameter sweep analysis")
    parser.add_argument("--db", default="data/arb_history.db", help="Path to SQLite database")
    parser.add_argument("--risk-mode", default="conservative", help="Base risk mode")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--train-end", help="Train period end date (YYYY-MM-DD)")
    parser.add_argument("--test-start", help="Test period start date (YYYY-MM-DD)")
    parser.add_argument("--sweep", nargs="+", help="Parameter sweeps: name=start:end:step")
    args = parser.parse_args()

    from datetime import datetime, timezone
    def parse_date(s):
        if s is None:
            return None
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    engine = ReplayEngine(args.db, risk_mode=args.risk_mode)
    param_ranges = {}
    if args.sweep:
        for s in args.sweep:
            name, values = _parse_sweep_arg(s)
            param_ranges[name] = values

    if not param_ranges:
        print("No sweep parameters specified. Use --sweep name=start:end:step")
        sys.exit(1)

    results = engine.sweep(
        param_ranges,
        start=parse_date(args.start),
        end=parse_date(args.end),
        train_end=parse_date(args.train_end),
        test_start=parse_date(args.test_start),
    )

    for r in results:
        params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        line = f"  {params_str} │ {r['signal_count']:3d} signals │ ${r['theoretical_profit']:8.4f}"
        if "train_signal_count" in r:
            line += f" │ train={r['train_signal_count']} test={r['test_signal_count']}"
        print(line)

    if len(param_ranges) == 1:
        param_name = list(param_ranges.keys())[0]
        plateaus = engine.find_plateaus(results, param_name)
        if plateaus:
            print(f"\nPlateau regions for {param_name}:")
            for lo, hi in plateaus:
                print(f"  {lo} → {hi}")

    engine.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_replay.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/replay.py tests/test_replay.py
git commit -m "feat: ReplayEngine with parameter sweep and plateau detection"
```

---

## Task 6: Analytics — Performance Report and CLI

**Files:**
- Create: `src/analytics.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_analytics.py`:

```python
import json
import time

import pytest

from src.recorder import DataRecorder
from src.analytics import Analytics


@pytest.fixture
def db_with_signals(tmp_path):
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    sid = rec.start_session({"risk_mode": "conservative"})

    rec.record_signal(event_ticker="EVT-A", strategy="taker", outcome="fire",
                      reject_reason=None, bid_sum=1.08, ask_sum=None,
                      profit_pct=2.5, exposure_ratio=1.5, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-B", strategy="taker", outcome="reject",
                      reject_reason="depth_filter", bid_sum=1.01, ask_sum=None,
                      profit_pct=0.5, exposure_ratio=1.0, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-C", strategy="taker", outcome="near_miss",
                      reject_reason=None, bid_sum=0.98, ask_sum=None,
                      profit_pct=None, exposure_ratio=None, legs=[], metadata=None)
    rec.record_signal(event_ticker="EVT-D", strategy="buy_side", outcome="fire",
                      reject_reason=None, bid_sum=None, ask_sum=0.90,
                      profit_pct=3.0, exposure_ratio=0.0, legs=[], metadata=None)

    rec.record_execution(event_ticker="EVT-A", strategy="taker",
                         legs=[], result="full_fill", fill_details=None, unwind_cost=0.0)
    rec.record_execution(event_ticker="EVT-E", strategy="taker",
                         legs=[], result="partial_fill", fill_details=None, unwind_cost=0.15)

    rec.record_fill(ticker="MKT-1", side="yes", action="sell", price=0.55, quantity=1, realized_pnl=None)
    rec.record_fill(ticker="MKT-1", side="yes", action="buy", price=0.50, quantity=1, realized_pnl=0.05)

    rec.record_balance(cash_cents=10000, portfolio_cents=10500)
    rec.record_balance(cash_cents=10200, portfolio_cents=10700)

    rec.end_session()
    rec.close()
    return db_path


def test_strategy_breakdown(db_with_signals):
    analytics = Analytics(db_with_signals)
    breakdown = analytics.strategy_breakdown()
    assert "taker" in breakdown
    assert breakdown["taker"]["fire_count"] == 1
    assert breakdown["taker"]["reject_count"] == 1
    assert breakdown["taker"]["near_miss_count"] == 1
    assert "buy_side" in breakdown
    assert breakdown["buy_side"]["fire_count"] == 1
    analytics.close()


def test_rejection_funnel(db_with_signals):
    analytics = Analytics(db_with_signals)
    funnel = analytics.rejection_funnel()
    assert funnel["depth_filter"] == 1
    analytics.close()


def test_partial_fill_analysis(db_with_signals):
    analytics = Analytics(db_with_signals)
    pf = analytics.partial_fill_analysis()
    assert pf["partial_count"] == 1
    assert pf["total_executions"] == 2
    assert pf["total_unwind_cost"] == 0.15
    analytics.close()


def test_balance_curve(db_with_signals):
    analytics = Analytics(db_with_signals)
    curve = analytics.balance_curve()
    assert curve["start_cash_cents"] == 10000
    assert curve["end_cash_cents"] == 10200
    analytics.close()


def test_near_miss_analysis(db_with_signals):
    analytics = Analytics(db_with_signals)
    nm = analytics.near_miss_analysis()
    assert nm["total_near_misses"] == 1
    analytics.close()


def test_full_report_string(db_with_signals):
    analytics = Analytics(db_with_signals)
    report = analytics.full_report()
    assert "taker" in report
    assert "Strategy Breakdown" in report
    analytics.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_analytics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.analytics'`

- [ ] **Step 3: Implement Analytics**

Create `src/analytics.py`:

```python
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone


class Analytics:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)

    def strategy_breakdown(
        self, start: float | None = None, end: float | None = None,
    ) -> dict[str, dict]:
        where, params = self._time_filter(start, end)
        rows = self._conn.execute(
            f"SELECT strategy, outcome, COUNT(*) FROM signal_evaluations {where} GROUP BY strategy, outcome",
            params,
        ).fetchall()

        breakdown: dict[str, dict] = {}
        for strategy, outcome, count in rows:
            if strategy not in breakdown:
                breakdown[strategy] = {"fire_count": 0, "reject_count": 0, "near_miss_count": 0, "total": 0}
            breakdown[strategy][f"{outcome}_count"] = count
            breakdown[strategy]["total"] += count

        # Add realized PnL from fills
        fill_rows = self._conn.execute(
            f"SELECT SUM(realized_pnl) FROM fills {where} AND realized_pnl IS NOT NULL",
            params,
        ).fetchone()
        total_realized = fill_rows[0] if fill_rows[0] else 0.0

        for strat in breakdown:
            breakdown[strat]["realized_pnl"] = total_realized  # TODO: per-strategy PnL requires linking fills to strategies

        return breakdown

    def rejection_funnel(
        self, start: float | None = None, end: float | None = None,
    ) -> dict[str, int]:
        where, params = self._time_filter(start, end)
        rows = self._conn.execute(
            f"SELECT reject_reason, COUNT(*) FROM signal_evaluations {where} AND outcome = 'reject' AND reject_reason IS NOT NULL GROUP BY reject_reason",
            params,
        ).fetchall()
        return {reason: count for reason, count in rows}

    def partial_fill_analysis(
        self, start: float | None = None, end: float | None = None,
    ) -> dict:
        where, params = self._time_filter(start, end)
        rows = self._conn.execute(
            f"SELECT result, COUNT(*), SUM(unwind_cost) FROM executions {where} GROUP BY result",
            params,
        ).fetchall()
        total = sum(count for _, count, _ in rows)
        partials = 0
        total_unwind = 0.0
        for result, count, unwind_sum in rows:
            if result == "partial_fill":
                partials = count
                total_unwind = unwind_sum or 0.0
        return {
            "total_executions": total,
            "partial_count": partials,
            "partial_rate": partials / total if total > 0 else 0.0,
            "total_unwind_cost": round(total_unwind, 6),
            "avg_unwind_cost": round(total_unwind / partials, 6) if partials > 0 else 0.0,
        }

    def balance_curve(
        self, start: float | None = None, end: float | None = None,
    ) -> dict:
        where, params = self._time_filter(start, end)
        rows = self._conn.execute(
            f"SELECT timestamp, cash_cents, portfolio_cents FROM balances {where} ORDER BY timestamp",
            params,
        ).fetchall()
        if not rows:
            return {"start_cash_cents": 0, "end_cash_cents": 0, "change_cents": 0, "max_drawdown_cents": 0}

        start_cash = rows[0][1]
        end_cash = rows[-1][1]
        peak = start_cash
        max_drawdown = 0
        for _, cash, _ in rows:
            if cash > peak:
                peak = cash
            drawdown = peak - cash
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return {
            "start_cash_cents": start_cash,
            "end_cash_cents": end_cash,
            "change_cents": end_cash - start_cash,
            "max_drawdown_cents": max_drawdown,
            "snapshots": len(rows),
        }

    def near_miss_analysis(
        self, start: float | None = None, end: float | None = None,
    ) -> dict:
        where, params = self._time_filter(start, end)
        rows = self._conn.execute(
            f"SELECT strategy, bid_sum, event_ticker FROM signal_evaluations {where} AND outcome = 'near_miss' ORDER BY bid_sum DESC",
            params,
        ).fetchall()
        by_strategy: dict[str, int] = {}
        best_miss = None
        for strategy, bid_sum, event_ticker in rows:
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
            if best_miss is None or (bid_sum and bid_sum > best_miss["bid_sum"]):
                best_miss = {"event_ticker": event_ticker, "bid_sum": bid_sum, "strategy": strategy}
        return {
            "total_near_misses": len(rows),
            "by_strategy": by_strategy,
            "best_miss": best_miss,
        }

    def full_report(
        self, start: float | None = None, end: float | None = None,
    ) -> str:
        breakdown = self.strategy_breakdown(start, end)
        funnel = self.rejection_funnel(start, end)
        pf = self.partial_fill_analysis(start, end)
        curve = self.balance_curve(start, end)
        nm = self.near_miss_analysis(start, end)

        lines = []
        lines.append("=" * 60)
        lines.append("Strategy Breakdown")
        lines.append("-" * 60)
        for strat, data in sorted(breakdown.items()):
            lines.append(
                f"  {strat:15s} │ {data['fire_count']:3d} fired │ "
                f"{data['reject_count']:3d} rejected │ {data['near_miss_count']:3d} near-miss"
            )

        lines.append("")
        lines.append("Rejection Funnel")
        lines.append("-" * 60)
        for reason, count in sorted(funnel.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: {count}")

        lines.append("")
        lines.append("Partial Fill Analysis")
        lines.append("-" * 60)
        lines.append(f"  {pf['partial_count']}/{pf['total_executions']} executions partial ({pf['partial_rate']:.1%})")
        lines.append(f"  Total unwind cost: ${pf['total_unwind_cost']:.4f}")
        lines.append(f"  Avg unwind cost: ${pf['avg_unwind_cost']:.4f}")

        lines.append("")
        lines.append("Balance Curve")
        lines.append("-" * 60)
        lines.append(f"  Start: ${curve['start_cash_cents'] / 100:.2f}")
        lines.append(f"  End:   ${curve['end_cash_cents'] / 100:.2f}")
        lines.append(f"  Change: ${curve['change_cents'] / 100:.2f}")
        lines.append(f"  Max drawdown: ${curve['max_drawdown_cents'] / 100:.2f}")

        lines.append("")
        lines.append("Near-Miss Analysis")
        lines.append("-" * 60)
        lines.append(f"  Total near-misses: {nm['total_near_misses']}")
        if nm["best_miss"]:
            lines.append(f"  Best missed: {nm['best_miss']['event_ticker']} bid_sum={nm['best_miss']['bid_sum']:.4f}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _time_filter(self, start: float | None, end: float | None) -> tuple[str, list]:
        conditions = ["1=1"]
        params = []
        if start is not None:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            conditions.append("timestamp <= ?")
            params.append(end)
        return "WHERE " + " AND ".join(conditions), params

    def close(self):
        self._conn.close()


def main():
    parser = argparse.ArgumentParser(description="Performance analytics report")
    parser.add_argument("--db", default="data/arb_history.db", help="Path to SQLite database")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    args = parser.parse_args()

    from datetime import datetime as dt, timezone
    def parse_date(s):
        if s is None:
            return None
        return dt.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    analytics = Analytics(args.db)
    start = parse_date(args.start)
    end = parse_date(args.end)

    if args.format == "json":
        data = {
            "strategy_breakdown": analytics.strategy_breakdown(start, end),
            "rejection_funnel": analytics.rejection_funnel(start, end),
            "partial_fill_analysis": analytics.partial_fill_analysis(start, end),
            "balance_curve": analytics.balance_curve(start, end),
            "near_miss_analysis": analytics.near_miss_analysis(start, end),
        }
        print(json.dumps(data, indent=2))
    else:
        print(analytics.full_report(start, end))

    analytics.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_analytics.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/analytics.py tests/test_analytics.py
git commit -m "feat: Analytics module with strategy breakdown, rejection funnel, and CLI"
```

---

## Task 7: MCP Tools — Expose Analytics via MCP

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: Add the 5 new MCP tools**

Add to `src/mcp_server.py`, after the existing tools:

```python
@mcp.tool()
async def get_performance_report(days: int = 7) -> str:
    """Get strategy performance report for the last N days.
    Includes per-strategy PnL, rejection funnel, fill rates, and balance curve.

    Args:
        days: Number of days to look back (default: 7)
    """
    import time as _time
    from src.analytics import Analytics
    cfg = load_config(CONFIG_PATH)
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    analytics = Analytics(db_path)
    end = _time.time()
    start = end - (days * 86400)
    report = analytics.full_report(start=start, end=end)
    analytics.close()
    return report


@mcp.tool()
async def get_parameter_sensitivity(
    parameter: str,
    range_start: float,
    range_end: float,
    step: float,
    days: int = 7,
) -> str:
    """Run a parameter sweep and return sensitivity analysis.
    Shows signal count and theoretical profit at each parameter value.
    Highlights plateau regions for robust parameter selection.

    Args:
        parameter: Parameter name (e.g. min_profit_pct, min_bid_depth)
        range_start: Start of sweep range
        range_end: End of sweep range
        step: Step size
        days: Days of data to use (default: 7)
    """
    import time as _time
    from src.replay import ReplayEngine
    cfg = load_config(CONFIG_PATH)
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    engine = ReplayEngine(db_path, risk_mode=cfg.risk_mode)

    values = []
    v = range_start
    while v <= range_end + step / 2:
        values.append(round(v, 6))
        v += step

    end = _time.time()
    start = end - (days * 86400)
    results = engine.sweep({parameter: values}, start=start, end=end)

    lines = [f"Parameter Sensitivity: {parameter}", "-" * 50]
    max_profit = max((abs(r["theoretical_profit"]) for r in results), default=1) or 1
    for r in results:
        val = r["params"][parameter]
        sc = r["signal_count"]
        profit = r["theoretical_profit"]
        bar_len = int(abs(profit) / max_profit * 20)
        bar = "█" * bar_len
        lines.append(f"  {val:8.2f} │ {sc:3d} signals │ ${profit:8.4f} │ {bar}")

    plateaus = engine.find_plateaus(results, parameter)
    if plateaus:
        lines.append(f"\nPlateau regions (robust values):")
        for lo, hi in plateaus:
            lines.append(f"  {lo} → {hi}")

    engine.close()
    return "\n".join(lines)


@mcp.tool()
async def get_near_misses(
    strategy: str = "all",
    threshold_pct: float = 0.5,
    days: int = 1,
) -> str:
    """Get signals that nearly fired but were rejected.
    Useful for identifying if thresholds are too tight.

    Args:
        strategy: Filter by strategy type, or "all" (default: "all")
        threshold_pct: How close to firing threshold to include (default: 0.5%)
        days: Days to look back (default: 1)
    """
    import time as _time
    from src.analytics import Analytics
    cfg = load_config(CONFIG_PATH)
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    analytics = Analytics(db_path)
    end = _time.time()
    start = end - (days * 86400)
    nm = analytics.near_miss_analysis(start=start, end=end)
    analytics.close()

    lines = [f"Near-Miss Analysis (last {days} day(s))", "-" * 50]
    lines.append(f"Total near-misses: {nm['total_near_misses']}")
    if nm["by_strategy"]:
        for strat, count in sorted(nm["by_strategy"].items(), key=lambda x: -x[1]):
            if strategy == "all" or strategy == strat:
                lines.append(f"  {strat}: {count}")
    if nm["best_miss"]:
        lines.append(f"\nBest missed opportunity:")
        lines.append(f"  {nm['best_miss']['event_ticker']} (bid_sum={nm['best_miss']['bid_sum']:.4f})")

    return "\n".join(lines)


@mcp.tool()
async def get_signal_history(
    strategy: str = "all",
    outcome: str = "all",
    days: int = 7,
    limit: int = 50,
) -> str:
    """Get historical signal evaluations with filtering.

    Args:
        strategy: Filter by strategy type (taker, buy_side, near_expiry, monotone, maker, two_sided, or "all")
        outcome: Filter by outcome (fire, reject, near_miss, or "all")
        days: Days to look back (default: 7)
        limit: Max results to return (default: 50)
    """
    import sqlite3
    import time as _time
    cfg = load_config(CONFIG_PATH)
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    conn = sqlite3.connect(db_path)

    end = _time.time()
    start = end - (days * 86400)
    conditions = ["timestamp >= ?", "timestamp <= ?"]
    params: list = [start, end]
    if strategy != "all":
        conditions.append("strategy = ?")
        params.append(strategy)
    if outcome != "all":
        conditions.append("outcome = ?")
        params.append(outcome)

    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT timestamp, event_ticker, strategy, outcome, bid_sum, profit_pct FROM signal_evaluations WHERE {where} ORDER BY timestamp DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()

    lines = [f"Signal History (last {days} day(s), {strategy}/{outcome})", "-" * 70]
    from datetime import datetime, timezone
    for ts, event, strat, out, bid_sum, profit_pct in rows:
        dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        bid_str = f"bid={bid_sum:.4f}" if bid_sum else "bid=N/A"
        pct_str = f"pct={profit_pct:.2f}%" if profit_pct else ""
        lines.append(f"  {dt_str} │ {strat:12s} │ {out:9s} │ {event} │ {bid_str} {pct_str}")

    lines.append(f"\n{len(rows)} results (limit {limit})")
    return "\n".join(lines)


@mcp.tool()
async def get_replay_comparison(
    parameter: str,
    current_value: float,
    proposed_value: float,
    days: int = 7,
) -> str:
    """Compare current vs proposed parameter values using replay.
    Shows side-by-side signal counts, theoretical profit, and risk metrics.
    Uses train/test split automatically (first half train, second half test).

    Args:
        parameter: Parameter to compare
        current_value: Current parameter value
        proposed_value: Proposed new value
        days: Days of data to use (default: 7)
    """
    import time as _time
    from src.replay import ReplayEngine
    cfg = load_config(CONFIG_PATH)
    db_path = cfg.recording_db_path if cfg.recording_enabled else "data/arb_history.db"
    engine = ReplayEngine(db_path, risk_mode=cfg.risk_mode)

    end = _time.time()
    start = end - (days * 86400)
    midpoint = start + (end - start) / 2

    results = engine.sweep(
        {parameter: [current_value, proposed_value]},
        start=start, end=end,
        train_end=midpoint, test_start=midpoint,
    )
    engine.close()

    if len(results) < 2:
        return "Insufficient data for comparison."

    current = results[0]
    proposed = results[1]

    lines = [
        f"Replay Comparison: {parameter}",
        "=" * 60,
        f"{'':20s} │ {'Current':>12s} │ {'Proposed':>12s}",
        "-" * 60,
        f"{'Value':20s} │ {current_value:12.2f} │ {proposed_value:12.2f}",
        f"{'Train signals':20s} │ {current['train_signal_count']:12d} │ {proposed['train_signal_count']:12d}",
        f"{'Train profit':20s} │ ${current['train_theoretical_profit']:11.4f} │ ${proposed['train_theoretical_profit']:11.4f}",
        f"{'Test signals':20s} │ {current['test_signal_count']:12d} │ {proposed['test_signal_count']:12d}",
        f"{'Test profit':20s} │ ${current['test_theoretical_profit']:11.4f} │ ${proposed['test_theoretical_profit']:11.4f}",
        f"{'Total signals':20s} │ {current['signal_count']:12d} │ {proposed['signal_count']:12d}",
        f"{'Total profit':20s} │ ${current['theoretical_profit']:11.4f} │ ${proposed['theoretical_profit']:11.4f}",
        "=" * 60,
    ]

    delta_signals = proposed["signal_count"] - current["signal_count"]
    delta_profit = proposed["theoretical_profit"] - current["theoretical_profit"]
    lines.append(f"Delta: {delta_signals:+d} signals, ${delta_profit:+.4f} profit")
    if proposed["test_theoretical_profit"] < current["test_theoretical_profit"]:
        lines.append("⚠ Proposed value performs WORSE on out-of-sample test data")

    return "\n".join(lines)
```

- [ ] **Step 2: Verify MCP server loads without errors**

Run: `python3 -c "from src.mcp_server import mcp; print('MCP server loaded OK')"`
Expected: `MCP server loaded OK`

- [ ] **Step 3: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: add 5 analytics MCP tools — performance, sensitivity, near-misses, history, replay"
```

---

## Task 8: Skills — Strategy Tuning, Post-Run Analyst, Strategy Review

**Files:**
- Create: `.claude/skills/strategy-tuning/SKILL.md`
- Create: `.claude/skills/post-run-analyst/SKILL.md`
- Create: `.claude/skills/strategy-review/SKILL.md`
- Modify: `.claude/skills/analyze-positions/SKILL.md`
- Modify: `.claude/skills/live-test/SKILL.md`

- [ ] **Step 1: Create strategy-tuning skill**

Create `.claude/skills/strategy-tuning/SKILL.md`:

```markdown
---
name: strategy-tuning
description: Use when you want to systematically tune bot parameters using historical data. Guides a structured parameter optimization session with train/test validation.
argument-hint: "[parameter-name|help]"
---

You are an expert at tuning Kalshi arb bot parameters using evidence from recorded data. Follow this workflow exactly.

## Context

The bot records all signal evaluations, executions, fills, and orderbook snapshots to a SQLite database. The MCP tools `get_performance_report`, `get_parameter_sensitivity`, `get_near_misses`, and `get_replay_comparison` expose this data.

Key parameters and their risk profile defaults are documented in `src/risk.py`.

## Workflow

### Step 1: Assess current performance

Call `mcp__kalshi-arb__get_performance_report` with days=7 (or longer if available).

Review:
- Which strategies have the highest/lowest profit per trade?
- What's the partial fill rate and unwind cost?
- Are there many near-misses clustering at threshold boundaries?

### Step 2: Identify the weakest link

Pick the parameter most likely to improve profitability:
- High reject rate with many near-misses → threshold may be too tight
- High partial fill rate → depth or volume filters may be too loose
- Low signal count → filters may be too conservative

### Step 3: Run parameter sensitivity

Call `mcp__kalshi-arb__get_parameter_sensitivity` with appropriate range:

Common sweeps:
- `min_profit_pct`: 0.5 to 3.0, step 0.25
- `min_bid_depth`: 1 to 10, step 1
- `min_volume_24h`: 0 to 100, step 10
- `max_exposure_ratio`: 1.0 to 5.0, step 0.5
- `near_expiry_window_minutes`: 0 to 120, step 15

### Step 4: Find the plateau

Look for **plateau regions** — ranges where the signal count and profit are relatively stable. A good parameter value sits in a plateau, not at a sharp peak.

Per Pardo's *Evaluation and Optimization of Trading Strategies*: a parameter at a sharp peak is overfit; a parameter on a plateau is robust.

### Step 5: Compare current vs proposed

Call `mcp__kalshi-arb__get_replay_comparison` with the current and proposed values.

**Critical check:** The tool automatically splits data into train (first half) and test (second half). If the proposed value performs WORSE on test data than current, do NOT recommend it — it's likely overfit.

### Step 6: Present recommendation

Present to the user:
1. What parameter to change and why
2. Current vs proposed value
3. Expected impact (signal count, profit delta)
4. Train vs test performance (in-sample vs out-of-sample)
5. Whether the proposed value is in a plateau region

Let the user decide whether to update `config.yaml`.

### Step 7: If approved, update config

If the user approves, update the relevant value in `config.yaml` under the `strategy:` section. Verify the change by calling `mcp__kalshi-arb__get_risk_profile`.

## Commands

### `[parameter-name]`
Jump directly to sensitivity analysis for a specific parameter.

### `help`
Show this help.
```

- [ ] **Step 2: Create post-run-analyst skill**

Create `.claude/skills/post-run-analyst/SKILL.md`:

```markdown
---
name: post-run-analyst
description: Use after a bot run completes to get an independent analysis of performance, anomalies, and tuning recommendations. Dispatched as a subagent by analyze-positions and live-test.
argument-hint: "[days=1]"
---

You are a quantitative analyst reviewing the performance of a Kalshi prediction market arb bot. Your job is to produce an independent, evidence-based assessment.

## Data Sources

Use these MCP tools to gather data:
- `mcp__kalshi-arb__get_performance_report` — strategy breakdown, rejection funnel, fill rates
- `mcp__kalshi-arb__get_positions` — current open positions
- `mcp__kalshi-arb__get_near_misses` — signals that nearly fired
- `mcp__kalshi-arb__get_risk_profile` — active risk parameters

## Workflow

### Step 1: Pull the performance report

Call `mcp__kalshi-arb__get_performance_report` with the appropriate lookback (default 1 day for post-run, 7 days for periodic review).

### Step 2: Check current positions

Call `mcp__kalshi-arb__get_positions` to see what's currently open.

### Step 3: Pull near-misses

Call `mcp__kalshi-arb__get_near_misses` with the same lookback period.

### Step 4: Check risk profile

Call `mcp__kalshi-arb__get_risk_profile` to understand active thresholds.

### Step 5: Anomaly detection

Compare this session's metrics against baseline expectations:

**Red flags:**
- Partial fill rate > 15% → depth/volume filters may be too loose
- Unwind cost > 50% of gross profit → execution quality is poor
- Near-miss count > 3x fire count for any strategy → threshold is too tight
- Any strategy with 0 fires but > 5 near-misses → strongly consider loosening
- Open positions from strategies that should be flat (taker, buy-side) → possible bug

**Health indicators:**
- Partial fill rate < 5% → good execution quality
- Near-miss count < fire count → thresholds are well-calibrated
- Balance increased over the session → profitable

### Step 6: Write assessment

Produce a structured report:

```
═══ Post-Run Analysis ═══

Session Overview:
  [Duration, events monitored, total signals]

Strategy Performance:
  [Per-strategy fire count, profit, issues]

Anomalies Detected:
  [List any red flags from Step 5]

Open Positions:
  [Current positions and whether they look legitimate]

Recommendations:
  [Specific, actionable parameter changes with reasoning]
  [Each recommendation should reference the data that supports it]

Risk Assessment:
  [Overall health: green/yellow/red]
  [Key risk: what could go wrong next session]
```

## Key Principle

Every recommendation must cite specific data. "Consider raising min_bid_depth" is not enough. "min_bid_depth=2 produced 3 partial fills out of 8 taker executions (37.5%) — raising to 5 would have filtered 2 of those events based on depth data in the near-miss log" is what's needed.
```

- [ ] **Step 3: Create strategy-review skill**

Create `.claude/skills/strategy-review/SKILL.md`:

```markdown
---
name: strategy-review
description: Use when reviewing any code change that affects trading strategy parameters, fee math, signal evaluation, or risk bounds. Provides independent financial review alongside code review.
argument-hint: "[branch-name|commit-range]"
---

You are a financial risk reviewer for a Kalshi prediction market arb bot. You review code changes that could affect trading profitability or risk exposure. This is an independent review — assume you have not seen the change before.

## When to Use

This skill should be invoked for any change touching:
- `src/engine.py` — signal evaluation logic
- `src/fees.py` — fee calculations
- `src/risk.py` — risk profiles and thresholds
- `src/executor.py` — execution and unwind logic
- `src/dispatch.py` — signal routing and filtering
- `config.yaml` or `config.example.yaml` — parameter changes
- Any new strategy implementation

## Review Checklist

### 1. Fee Math Verification

For any change to fee calculations:
- Verify `taker_fee(p) = 0.07 * p * (1 - p)` is correctly applied
- Check that fees are computed per-leg, not per-trade
- Verify profit calculations: `sum(bids) - 1.0 - sum(fees)` for sell-side
- Verify buy-side: `1.0 - sum(asks) - sum(fees)`
- Run the fee tests: `python3 -m pytest tests/test_fees.py -v`

### 2. Negative-EV Check

For any parameter or threshold change:
- Could this change cause the bot to take trades with negative expected value?
- What's the worst case? Walk through a scenario where every assumption goes wrong.
- If a filter is being loosened, what previously-rejected trades would now fire?

If replay data is available, run:
```
mcp__kalshi-arb__get_replay_comparison with current and proposed values
```
to check whether the change improves or degrades performance on out-of-sample data.

### 3. Risk Bound Verification

- Are `max_exposure_ratio` bounds still respected?
- Does the circuit breaker still function?
- Are partial fill unwind paths still correct?
- Could this change increase maximum possible loss per trade?

### 4. Edge Case Analysis

- What happens at price boundaries ($0.01, $0.99)?
- What happens with 0 depth, 0 volume, or missing metadata?
- What happens near market close (within near_expiry_window)?
- Does the change interact unexpectedly with other strategies?

### 5. Replay Validation (if data available)

Call `mcp__kalshi-arb__get_parameter_sensitivity` for any changed parameter to verify:
- The new value sits in a plateau region (not a sharp peak)
- The change doesn't significantly reduce signal count without proportional risk reduction
- Out-of-sample performance is not worse than in-sample

### 6. Report

Produce a structured report:

```
═══ Strategy Review ═══

Changes Reviewed:
  [List files and what changed]

Fee Math: ✅/⚠️/❌
  [Verification details]

Negative-EV Risk: ✅/⚠️/❌
  [Analysis of worst-case scenarios]

Risk Bounds: ✅/⚠️/❌
  [Are all safety bounds maintained?]

Edge Cases: ✅/⚠️/❌
  [Any edge cases that could cause issues]

Replay Validation: ✅/⚠️/❌ (or N/A if no data)
  [Results from parameter sensitivity analysis]

Overall Assessment: APPROVE / APPROVE WITH CONCERNS / BLOCK
  [Summary and any required changes before merge]
```

## Key Principle

This is a financial review, not a code style review. The question is not "is this code clean?" but "could this code lose money?" Be conservative — it's better to flag a false positive than miss a real risk.
```

- [ ] **Step 4: Update analyze-positions skill**

Add to the end of `.claude/skills/analyze-positions/SKILL.md`, before the `## Notes` section:

```markdown
### Post-Run Performance Review

After completing position analysis (either `report-only` or `close-bad`), if the analytics database is available:

**Step 7: Pull performance report**

Call `mcp__kalshi-arb__get_performance_report` with days=1.

Append to your findings:
- Strategy breakdown (which strategies fired, profit per trade)
- Rejection funnel (what filters are blocking the most signals)
- Near-miss count (are thresholds well-calibrated?)

**Step 8: Dispatch post-run analyst (optional)**

If the performance report shows anomalies (partial fill rate > 15%, or near-misses > 3x fires for any strategy), recommend running `/post-run-analyst` for a deeper analysis.
```

- [ ] **Step 5: Update live-test skill**

In `.claude/skills/live-test/SKILL.md`, update Step 6 to mention the post-run analyst. After the existing `/analyze-positions close-bad` line, add:

```markdown
If the bot ran for more than 60 seconds and analytics recording is enabled, also run `/post-run-analyst` for a detailed performance assessment with tuning recommendations.
```

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/strategy-tuning/SKILL.md .claude/skills/post-run-analyst/SKILL.md .claude/skills/strategy-review/SKILL.md .claude/skills/analyze-positions/SKILL.md .claude/skills/live-test/SKILL.md
git commit -m "feat: add strategy-tuning, post-run-analyst, and strategy-review skills"
```

---

## Task 9: Final Integration Test and Cleanup

**Files:**
- Test: all test files
- Modify: `CLAUDE.md` (update architecture docs)

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Verify the MCP server loads**

Run: `python3 -c "from src.mcp_server import mcp; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify replay CLI loads**

Run: `python3 -m src.replay --help`
Expected: Shows argparse help with `--db`, `--sweep`, etc.

- [ ] **Step 4: Verify analytics CLI loads**

Run: `python3 -m src.analytics --help`
Expected: Shows argparse help with `--db`, `--start`, `--end`, `--format`

- [ ] **Step 5: Update CLAUDE.md**

Add to the Architecture section after the existing modules list:

```markdown
- `src/recorder.py` — `DataRecorder`: SQLite-backed recording of orderbook snapshots, signal evaluations, executions, fills, balances
- `src/replay.py` — `ReplayEngine`: parameter sweep over recorded orderbook history, train/test split, plateau detection
- `src/analytics.py` — `Analytics`: per-strategy PnL attribution, rejection funnel, partial fill analysis, balance curve, near-miss distribution
```

Add to the MCP Server section:

```markdown
Additional tools: `get_performance_report`, `get_parameter_sensitivity`, `get_near_misses`, `get_signal_history`, `get_replay_comparison`.
```

Add to the Config section:

```markdown
`recording:` section controls data recording (enabled by default). See `config.example.yaml`.
```

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with analytics, replay, and recording architecture"
```

- [ ] **Step 7: Final full test run**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS
