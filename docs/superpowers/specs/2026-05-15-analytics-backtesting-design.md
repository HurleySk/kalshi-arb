# Analytics, Backtesting & Strategy Tuning Infrastructure

**Date:** 2026-05-15
**Status:** Approved design, pending implementation

## Problem

The kalshi-arb bot runs six concurrent strategies (taker, buy-side, near-expiry, monotone, maker, two-sided) but has no systematic way to answer:
- Which strategies are actually profitable?
- Are the current parameter thresholds optimal or leaving money on the table?
- What's the cost of partial fills and unwinds?
- How would different parameters have performed historically?

The bot is "stable-ish" but needs proactive analytics infrastructure to move from reactive tuning to evidence-based optimization.

## Approach

Build a bespoke analytics and backtesting layer directly in this repo rather than adopting off-the-shelf trading skills. Rationale: prediction market arb with taker fees (`0.07 * p * (1-p)`), completeness constraints (bid sum > $1 + fees), and mutually exclusive binary outcomes doesn't map to any existing backtesting framework's assumptions about continuous price series and standard technical indicators.

### Prior art considered and rejected
- [jeremylongshore/claude-code-plugins-plus-skills](https://github.com/jeremylongshore/claude-code-plugins-plus-skills) — 1,537 skills including backtesting, but oriented toward crypto/equity markets
- [agiprolabs/claude-trading-skills](https://github.com/agiprolabs/claude-trading-skills) — 62 skills with market microstructure category, but assumes standard exchange mechanics
- [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) — equity swing trading focused
- [VoltAgent quant-analyst subagent](https://github.com/VoltAgent/awesome-claude-code-subagents/blob/main/categories/07-specialized-domains/quant-analyst.md) — generic "bring your own data" analyst

The adapter cost to make any of these work with Kalshi's prediction market structure would exceed the cost of building purpose-built tooling.

## Architecture

Three new modules plus MCP/skill integration:

```
src/recorder.py ─── inline recording at decision points ──→ data/arb_history.db
src/replay.py ──── replay engine, parameter sweeps ────────→ sweep results
src/analytics.py ── PnL attribution, rejection funnel ────→ CLI reports / structured export

src/mcp_server.py ── 5 new tools exposing analytics via MCP
.claude/skills/strategy-tuning.md ── guided tuning session skill
.claude/skills/post-run-analyst.md ── post-run debrief subagent
```

## Component 1: Data Recorder (`src/recorder.py`)

### Schema

SQLite database at `data/arb_history.db` (configurable). Five tables:

**`sessions`**
```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL,                     -- NULL while running
    config_json TEXT                   -- snapshot of config at start
);
```

**`orderbook_snapshots`**
```sql
CREATE TABLE orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    market_ticker TEXT NOT NULL,
    yes_bids_json TEXT NOT NULL,       -- JSON: {price_cents: quantity}
    no_bids_json TEXT NOT NULL
);
CREATE INDEX idx_snap_time ON orderbook_snapshots(timestamp);
CREATE INDEX idx_snap_event ON orderbook_snapshots(event_ticker);
```

**`signal_evaluations`**
```sql
CREATE TABLE signal_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,            -- taker, buy_side, near_expiry, monotone, maker, two_sided
    outcome TEXT NOT NULL,             -- fire, reject, near_miss
    reject_reason TEXT,                -- depth_filter, volume_filter, profit_threshold, exposure_ratio, cooldown, blacklisted, horizon
    bid_sum REAL,
    ask_sum REAL,
    profit_pct REAL,
    exposure_ratio REAL,
    leg_count INTEGER,
    legs_json TEXT,                    -- JSON: [{ticker, price, depth}]
    metadata_json TEXT                 -- JSON: strategy-specific fields
);
CREATE INDEX idx_sig_time ON signal_evaluations(timestamp);
CREATE INDEX idx_sig_strategy ON signal_evaluations(strategy);
CREATE INDEX idx_sig_outcome ON signal_evaluations(outcome);
```

**`executions`**
```sql
CREATE TABLE executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    legs_json TEXT NOT NULL,           -- JSON: [{ticker, action, price, quantity}]
    result TEXT NOT NULL,              -- full_fill, partial_fill, failed
    fill_details_json TEXT,            -- JSON: per-leg fill results
    unwind_cost REAL DEFAULT 0.0
);
CREATE INDEX idx_exec_time ON executions(timestamp);
```

**`fills`**
```sql
CREATE TABLE fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,              -- sell, buy
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    realized_pnl REAL                 -- NULL for opens
);
CREATE INDEX idx_fill_time ON fills(timestamp);
CREATE INDEX idx_fill_ticker ON fills(ticker);
```

**`balances`**
```sql
CREATE TABLE balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    timestamp REAL NOT NULL,
    cash_cents INTEGER NOT NULL,
    portfolio_cents INTEGER NOT NULL
);
CREATE INDEX idx_bal_time ON balances(timestamp);
```

### Integration points

`DataRecorder` exposes simple synchronous methods called inline at existing decision points:

| Call site | Method | What's recorded |
|-----------|--------|-----------------|
| `Dispatcher.process_orderbook_update` after each `evaluate*()` call | `record_signal()` | Every signal evaluation with outcome and all parameters |
| `ExecutionManager.execute` after batch order response | `record_execution()` | Execution attempt with fill results |
| `PositionTracker.record_fill` | `record_fill()` | Every fill event |
| `ArbBot.run()` startup | `start_session()` | New session row with config snapshot |
| `ArbBot.run()` shutdown | `end_session()` | Set session end_time |
| New periodic task in `ArbBot.run()` | `record_balance()` | Balance snapshots |
| New periodic task in `ArbBot.run()` | `record_orderbook_snapshot()` | Sampled orderbook state |

SQLite writes are synchronous but fast enough — this bot processes hundreds of updates/sec at peak, and SQLite handles tens of thousands of simple inserts/sec.

### Signal outcome classification

The dispatcher determines the `outcome` field for `record_signal()`:

- **fire** — `evaluate*()` returned a `TradeSignal` and it passed all dispatcher guards (cooldown, pending, blacklist)
- **reject** — `evaluate*()` returned `None`, OR it returned a signal but the dispatcher blocked it (cooldown, blacklist). The `reject_reason` is set by the dispatcher based on which guard blocked it.
- **near_miss** — the existing DEBUG-level near-miss logging in `ArbEngine.evaluate()` already detects these (bid_sum close to threshold but below it). The engine will call `recorder.record_signal()` directly at these log points with outcome `near_miss` and the relevant metrics (bid_sum, threshold distance).

This means `record_signal()` is called from two places: the engine (for near-misses detected during evaluation) and the dispatcher (for fires and rejects). The recorder doesn't need to understand the evaluation logic — it just persists what it's told.

### Snapshot sampling

Orderbook snapshots are sampled every `snapshot_interval_secs` (default: 5) per event. On each sample tick, iterate registered events in `OrderbookManager` and persist the current state of all their market orderbooks. This captures the decision-time state without recording every delta.

### Configuration

```yaml
recording:
  enabled: true
  db_path: data/arb_history.db
  snapshot_interval_secs: 5
  balance_poll_interval_secs: 300
```

Recording is on by default. The SQLite file is gitignored.

## Component 2: Replay Engine (`src/replay.py`)

### Purpose

Feed recorded orderbook states back through `ArbEngine.evaluate*()` with different parameters to answer "what would have happened if we used these thresholds?"

### How it works

1. **Load** `orderbook_snapshots` for a date range from SQLite
2. **Reconstruct** `Orderbook` objects from the stored JSON
3. **Group** snapshots by event and timestamp to recreate the full event state at each sample point
4. **Sweep** parameters: for each snapshot, instantiate `ArbEngine` with a modified `RiskProfile` and call the relevant `evaluate*()` functions
5. **Compare** replay results against `signal_evaluations` to see what changed

### Parameter sweep

Supported sweep dimensions:

| Parameter | Default range | Step |
|-----------|--------------|------|
| `min_profit_pct` | 0.5% → 3.0% | 0.25% |
| `min_bid_depth` | 1 → 10 | 1 |
| `min_volume_24h` | 0 → 100 | 10 |
| `max_exposure_ratio` | 1.0 → 5.0 | 0.5 |
| `near_expiry_window_minutes` | 0 → 120 | 15 |
| `two_sided_min_spread_cents` | 2 → 8 | 1 |

Custom ranges via CLI: `--sweep min_profit_pct=0.5:3.0:0.25`

### Output per parameter combination

- Signal count (how many arbs would have fired)
- Theoretical profit (sum of `net_profit` on fired signals)
- Reject breakdown by reason
- Overlap with actual executions

### Limitations

The replay engine evaluates **signals, not fills**. If the engine says "sell at best bid $0.45," we assume that fill would have happened at that price. This is reasonable for 1-contract arbs on markets with decent depth, but does not account for:
- Market impact / slippage
- Orderbook changes between signal and execution
- Other participants racing for the same arb

This is an honest limitation documented prominently. Per [Ernest Chan's *Algorithmic Trading*](https://www.wiley.com/en-us/Algorithmic+Trading%3A+Winning+Strategies+and+Their+Rationale-p-9781118460146), implementation shortfall (the gap between theoretical signal profit and realized profit) is often the dominant cost in systematic trading. Our `executions` table with `unwind_cost` tracks the real-world shortfall, so we can compare theoretical replay results against actual execution quality.

### Train/test split

Per [Robert Pardo's *The Evaluation and Optimization of Trading Strategies*](https://www.wiley.com/en-us/The+Evaluation+and+Optimization+of+Trading+Strategies%2C+2nd+Edition-p-9780470128015), optimizing parameters in-sample and then deploying them without validation is the single most common cause of strategy failure. The replay engine supports:

- `--train-end DATE` — optimize parameters using data before this date
- `--test-start DATE` — validate the optimized parameters on data after this date

The analytics output clearly labels which results are in-sample vs. out-of-sample.

### Plateau detection

Per Pardo, the goal is not to find the *best* parameter value but a parameter value in a **plateau region** — a range where small perturbations don't drastically change performance. A parameter at a sharp peak is overfit; a parameter on a plateau is robust. The sweep output highlights plateau regions where performance varies less than 10% across adjacent parameter values.

### CLI entry point

```bash
# Basic sweep
python3 -m src.replay --start 2026-05-01 --end 2026-05-15 --sweep min_profit_pct=0.5:3.0:0.25

# Train/test split
python3 -m src.replay --start 2026-05-01 --train-end 2026-05-08 --test-start 2026-05-08 --end 2026-05-15 --sweep min_profit_pct=0.5:3.0:0.25

# Multi-parameter sweep
python3 -m src.replay --start 2026-05-01 --end 2026-05-15 --sweep min_profit_pct=0.5:2.0:0.5 min_bid_depth=1:5:1
```

## Component 3: Analytics (`src/analytics.py`)

### CLI report

`python3 -m src.analytics --start 2026-05-01 --end 2026-05-15`

Outputs:

1. **Strategy breakdown** — per-strategy signal count, fire count, reject count, realized PnL, avg profit/trade
2. **Rejection funnel** — aggregate counts by rejection reason across all strategies
3. **Partial fill analysis** — partial fill rate, total unwind cost, avg unwind cost per partial
4. **Balance curve** — start/end balance, net change, max drawdown
5. **Near-miss analysis** — count of near-misses within configurable threshold of each strategy's fire point, best missed opportunity

### Key metrics

| Metric | What it measures | Reference |
|--------|-----------------|-----------|
| Per-strategy PnL | Which strategies carry the book | [Lopez de Prado, *Advances in Financial Machine Learning*](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086) — factor attribution |
| Fill rate / partial fill cost | Execution quality | [Chan, *Algorithmic Trading*](https://www.wiley.com/en-us/Algorithmic+Trading%3A+Winning+Strategies+and+Their+Rationale-p-9781118460146) — implementation shortfall |
| Rejection funnel | Filter effectiveness | [Walk-forward analysis](https://www.investopedia.com/terms/w/walk-forward-testing.asp) |
| Near-miss distribution | Threshold sensitivity | [Pardo, *Evaluation and Optimization of Trading Strategies*](https://www.wiley.com/en-us/The+Evaluation+and+Optimization+of+Trading+Strategies%2C+2nd+Edition-p-9780470128015) — parameter landscape |
| Max drawdown | Risk exposure | Standard risk metric per [Sortino ratio](https://en.wikipedia.org/wiki/Sortino_ratio) framework |

### Parameter sensitivity table

For each swept parameter, output an ASCII bar chart showing signal count and theoretical profit at each value. Highlight plateau regions (adjacent values with <10% performance variation).

### Export

`--format json` or `--format csv` for programmatic consumption. The MCP tools use the structured output directly.

## Component 4: MCP Tools

Five new tools added to `src/mcp_server.py`:

### `get_performance_report`
```python
@mcp.tool()
async def get_performance_report(days: int = 7) -> str:
    """Get strategy performance report for the last N days.
    Includes per-strategy PnL, rejection funnel, fill rates, and balance curve.
    Args:
        days: Number of days to look back (default: 7)
    """
```

### `get_parameter_sensitivity`
```python
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
```

### `get_near_misses`
```python
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
```

### `get_signal_history`
```python
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
```

### `get_replay_comparison`
```python
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
```

## Component 5: Skills & Subagents

### Updated `analyze-positions` skill

Extend the existing skill to call `get_performance_report` and `get_signal_history` after inspecting positions, providing a full post-run debrief rather than just a position snapshot.

### New `strategy-tuning` skill (`.claude/skills/strategy-tuning.md`)

Guides a structured parameter tuning session:

1. Pull current performance via `get_performance_report`
2. Identify the weakest-performing strategy (lowest profit/signal or highest rejection rate)
3. Run `get_parameter_sensitivity` on the relevant parameters
4. Look for plateau regions in the sweep results
5. Run `get_replay_comparison` with current vs. proposed parameters
6. Present recommendation with evidence, clearly labeling in-sample vs. out-of-sample results
7. User decides whether to update `config.yaml`

### New `post-run-analyst` subagent (`.claude/skills/post-run-analyst.md`)

A subagent invoked by `analyze-positions` and `live-test` skills after a bot run completes:

1. Pull performance report, signal history, and near-misses via MCP
2. Cross-reference with current open positions
3. Identify anomalies (comparing this session against the prior 5 sessions in the `sessions` table):
   - Strategies with unusually high partial fill rates
   - Fill rates that dropped vs. prior sessions
   - Near-miss clusters suggesting threshold tightness
   - Events where unwind cost exceeded signal profit
4. Produce a written assessment with specific, actionable recommendations (e.g., "near_expiry fired 7 times but 3 were partial fills on low-liquidity markets — consider raising `near_expiry_min_volume_24h` from 10 to 25")

## Configuration

New `config.yaml` fields:

```yaml
recording:
  enabled: true                      # default: true
  db_path: data/arb_history.db       # default: data/arb_history.db
  snapshot_interval_secs: 5          # default: 5
  balance_poll_interval_secs: 300    # default: 300
```

Add to `config.example.yaml` with comments. The `data/` directory and `*.db` files are gitignored.

## Files changed

| File | Change |
|------|--------|
| `src/recorder.py` | New — DataRecorder class, SQLite schema, record methods |
| `src/replay.py` | New — ReplayEngine, parameter sweep, train/test split, CLI entry point |
| `src/analytics.py` | New — Analytics class, CLI report, structured export |
| `src/mcp_server.py` | Modified — 5 new MCP tools |
| `src/dispatch.py` | Modified — call `recorder.record_signal()` after each evaluate |
| `src/executor.py` | Modified — call `recorder.record_execution()` after batch orders |
| `src/positions.py` | Modified — call `recorder.record_fill()` on fills |
| `src/main.py` | Modified — initialize DataRecorder, add snapshot/balance periodic tasks |
| `src/config.py` | Modified — parse `recording:` config section |
| `config.example.yaml` | Modified — add `recording:` section |
| `.gitignore` | Modified — add `data/` and `*.db` |
| `.claude/skills/strategy-tuning.md` | New — guided tuning session skill |
| `.claude/skills/post-run-analyst.md` | New — post-run analyst subagent skill |
| `.claude/skills/analyze-positions.md` | Modified — extend with performance data |
| `tests/test_recorder.py` | New — DataRecorder tests |
| `tests/test_replay.py` | New — ReplayEngine tests |
| `tests/test_analytics.py` | New — Analytics tests |

## References

- [Lopez de Prado, *Advances in Financial Machine Learning*](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086) — PnL attribution by signal source
- [Chan, *Algorithmic Trading*](https://www.wiley.com/en-us/Algorithmic+Trading%3A+Winning+Strategies+and+Their+Rationale-p-9781118460146) — implementation shortfall measurement
- [Pardo, *Evaluation and Optimization of Trading Strategies*](https://www.wiley.com/en-us/The+Evaluation+and+Optimization+of+Trading+Strategies%2C+2nd+Edition-p-9780470128015) — parameter landscape analysis, plateau detection, walk-forward validation
- [Investopedia: Walk-forward testing](https://www.investopedia.com/terms/w/walk-forward-testing.asp) — filter effectiveness evaluation
- [Investopedia: Lookahead bias](https://www.investopedia.com/terms/l/lookaheadbias.asp) — avoiding in-sample overfitting
