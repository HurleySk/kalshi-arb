# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python asyncio bot that detects and executes arbitrage opportunities on Kalshi prediction markets. Multiple strategies run concurrently: sell-side taker arb (bid sum > $1 + fees), buy-side taker arb (ask sum < $1 - fees), near-expiry harvesting, monotone constraint arb, maker arb, and two-sided spread capture.

## Commands

```bash
# Run the bot (reads config.yaml, defaults to demo mode)
python3 -m src.main

# Run all tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_engine.py -v

# Run a specific test
python3 -m pytest tests/test_engine.py::test_evaluate_profitable_arb -v

# Install dependencies (Raspberry Pi requires --break-system-packages)
pip3 install -r requirements.txt --break-system-packages
```

## Architecture

### Ports & Adapters

The codebase uses a ports & adapters pattern to separate exchange-agnostic trading logic from exchange-specific implementations. This enables future support for PredictIt and IBKR ForecastEx alongside Kalshi.

**Dependency rule:** `src/core/` and `src/strategies/` depend only on `src/ports/` (abstract Protocol interfaces). They never import from `src/exchanges/`. `src/main.py` is the composition root that wires exchange adapters to core logic.

```
src/
  core/           — exchange-agnostic logic (engine, dispatch, models, fees, risk, recorder)
  ports/          — abstract Protocol interfaces (FeeModel, ExchangeAPI, OrderBuilder, etc.)
  exchanges/
    kalshi/       — Kalshi-specific adapters (API, auth, scanner, discovery, fee model)
  strategies/     — strategy implementations (taker, maker, two-sided, near-expiry, monotone)
  executor.py     — order execution (uses ExchangeAPI + OrderBuilder ports)
  main.py         — composition root (wires exchange → core → strategies)
```

### Runtime Components

Five async components run concurrently in `ArbBot.run()` (`src/main.py`):

1. **Event Discovery** (`exchanges/kalshi/discovery.py` → `KalshiDiscovery.poll_loop`) — REST polls Kalshi for mutually exclusive events with 2+ active markets. Full paginated scan on startup, then re-polls page 1 every 60s. Also runs `cleanup_loop` every 5 minutes to remove expired events.

2. **WebSocket Scanner** (`exchanges/kalshi/scanner.py` → `MarketScanner` + `core/orderbook_manager.py` → `OrderbookManager`) — Maintains real-time orderbook state via `orderbook_delta` channel. On every update, enqueues the market ticker to `_ob_update_queue`. Reconnects automatically on disconnect.

3. **Orderbook Processor** (`_process_orderbook_updates`) — Drains `_ob_update_queue` and routes each update through `Dispatcher` (`core/dispatch.py`). Dispatcher evaluates arb signals, guards duplicate execution per event, enforces signal cooldowns, and routes fills.

4. **Arb Detection → Execution** (`Dispatcher` → `core/engine.py:ArbEngine` → `strategies/` → `executor.py`) — The hot path. On each orderbook update, evaluates strategies via fee-model-parameterized profit calculations. If profitable and passes filters, fires a batch order via REST API.

5. **Maker Layer** (`core/engine.py:evaluate_maker` → `strategies/maker.py:MakerManager`) — When bid sum is between $1.00 and the taker threshold (~$1.07), posts limit orders at best bid prices as maker (0% fees). On fill, completes the arb via cancel_and_take or tighten_on_fill. Reprices on orderbook updates, cancels if arb disappears.

6. **Two-Sided Market Making** (`core/engine.py:evaluate_two_sided` → `strategies/two_sided.py:TwoSidedManager`) — Per-market spread capture. **Disabled by default** (`two_sided_max_inventory: 0`). When enabled and spread ≥ `two_sided_min_spread_cents + 2`, posts limit bid and ask each 1¢ inside NBBO. On fill, cancels the opposite side and unwinds. Times out unfilled pairs after `two_sided_timeout_secs`.

### Key modules

- `src/core/engine.py` — `ArbEngine`: thin coordinator, delegates to strategy modules
- `src/core/dispatch.py` — `Dispatcher`: orderbook update routing, pending guard, fill routing
- `src/core/models.py` — `Orderbook` (bids/asks), `TradeSignal`, `Event`, `Market`, `Fill`
- `src/core/fees.py` — profit/exposure calculations parameterized by `FeeModel`
- `src/core/orderbook_manager.py` — `OrderbookManager`: exchange-agnostic orderbook state
- `src/core/risk.py` — `RiskProfile` dataclass + `load_risk_profile`
- `src/core/recorder.py` — `DataRecorder`: DuckDB-backed recording
- `src/core/replay.py` — `ReplayEngine`: parameter sweep over recorded history
- `src/core/analytics.py` — `Analytics`: per-strategy PnL attribution, rejection funnel
- `src/ports/` — `FeeModel`, `ExchangeAPI`, `OrderBuilder`, `OrderbookFeed`, `MarketDiscovery`, `PositionConstraints`
- `src/exchanges/kalshi/` — `KalshiExchange` facade, `KalshiAPI`, `KalshiAuth`, `MarketScanner`, `KalshiDiscovery`, `KalshiFeeModel`, `KalshiOrderBuilder`
- `src/strategies/taker.py` — sell-side and buy-side taker arb evaluation
- `src/strategies/near_expiry.py` — near-expiry stale order harvesting
- `src/strategies/monotone.py` — monotone constraint arb
- `src/strategies/maker.py` — `MakerManager`: limit order posting and fill handling
- `src/strategies/two_sided.py` — `TwoSidedManager`: paired bid/ask order lifecycle
- `src/executor.py` — `ExecutionManager`: batch order execution, partial fill unwind

### Data flow

```
Exchange factory (src/exchanges/) → creates API, Scanner, Discovery adapters
REST /events → KalshiDiscovery.register_events → OrderbookManager.register_event → WS subscribe
               └─ MonotoneFamilyRegistry.try_register (group threshold markets)
WS orderbook_delta → OrderbookManager.apply_snapshot/apply_delta → _ob_update_queue
  → Dispatcher.process_orderbook_update
    → ArbEngine.evaluate → strategies/taker.evaluate_sell_side → ExecutionManager.execute
    → ArbEngine.evaluate_buy_side → strategies/taker.evaluate_buy_side → ExecutionManager.execute
    → ArbEngine.evaluate_near_expiry → strategies/near_expiry.evaluate → ExecutionManager.execute
    → ArbEngine.evaluate_monotone_pair → strategies/monotone.evaluate → ExecutionManager.execute
    → ArbEngine.evaluate_maker → MakerManager.post
    → MakerManager.on_orderbook_update (reprice active maker orders)
    → ArbEngine.evaluate_two_sided → TwoSidedManager.post
WS fill → TwoSidedManager.handle_fill OR Dispatcher.route_fill → executor.handle_fill OR maker.handle_fill
```

### Risk Modes (`src/core/risk.py`)

Three configurable risk profiles control all trading thresholds. Set `risk_mode` in `config.yaml`:

- **conservative** (default) — min_volume_24h=50, min_bid_depth=5, min_ask_depth=5, min_profit_pct=2.0%, require_recent_trades=true, max_exposure_ratio=2.0; near_expiry_window=30min; two_sided disabled, buy_side disabled
- **moderate** — min_volume_24h=10, min_bid_depth=2, min_ask_depth=2, min_profit_pct=1.0%, require_recent_trades=true, max_exposure_ratio=3.0; near_expiry_window=60min; two_sided disabled, buy_side disabled
- **aggressive** — min_volume_24h=0, min_bid_depth=1, min_ask_depth=1, min_profit_pct=0.5%, require_recent_trades=false, max_exposure_ratio=5.0; near_expiry_window=120min; two_sided disabled, buy_side disabled

Individual overrides in `config.yaml` take precedence over preset values.

**New strategy fields** (all presets have defaults; override in `config.yaml` to customize):
- `enable_buy_side_arb` — buy all YES outcomes when ask sum < $1 - fees (default: false — disabled due to structural ask-lifting risk)
- `near_expiry_window_minutes` — window before close to use relaxed taker filters (0 = disabled)
- `near_expiry_min_profit_pct`, `near_expiry_min_bid_depth`, `near_expiry_min_volume_24h` — near-expiry specific thresholds
- `two_sided_max_inventory` — max total contracts in resting two-sided pairs (0 = disabled)
- `two_sided_min_spread_cents` — minimum spread required to post (need spread ≥ this + 2 to post 1¢ inside each side)
- `two_sided_timeout_secs` — cancel unfilled pairs after this many seconds
- `two_sided_min_volume_24h` — volume floor for two-sided candidates
- `maker_min_volume_24h` — separate volume floor for maker strategy (lower than taker since makers create liquidity; conservative=10, moderate/aggressive=0)

### Key filtering pipeline in `strategies/taker.evaluate_sell_side()`

1. All markets must have a best bid
2. `min_bid_depth` check (from risk profile)
3. `min_ask_depth` check — rejects one-sided markets (bids only, no asks) as likely stale/phantom
4. `min_volume_24h` check — rejects legs with insufficient 24h trading volume
5. `arb_profit(bid_prices, fee_model) > 0` (sum of bids > $1 + fees per exchange fee model)
6. `min_profit_pct` check — signal must exceed minimum profit threshold
7. `exposure_ratio <= max_exposure_ratio` (worst-case loss / net premium)
8. Async `_validate_recent_trades` — when enabled, verifies each leg has recent trade activity before execution

### Partial Fill Protection

**Buy-side immediate cancel:** When a buy-side arb's batch response has any leg "resting" (no fill), all resting legs are cancelled immediately and filled legs are unwound — no waiting for `fill_timeout_secs`.

On partial fill (some legs fill, others don't), the executor:
1. Blacklists the event permanently (no re-execution)
2. Releases the executor lock (`_executing = False`) immediately so new arbs can be processed
3. Launches a **detached async task** (`_launch_unwind`) for graduated unwind — does NOT block the main trading pipeline
4. Runs 5-phase graduated unwind on filled legs:
   - Phase 1: fill_price ± 1×step (immediate)
   - Phase 2: fill_price ± 2×step (after `unwind_phase1_secs`)
   - Phase 3: fill_price ± 4×step (after `unwind_phase2_secs - phase1`)
   - Phase 4: 50% toward floor/ceiling, monotonically bounded (after `unwind_phase2_secs`)
   - Phase 5: absolute floor $0.01 / ceiling $0.99 (after `unwind_phase2_secs`)
5. Each unwind API call has a 15s timeout; cancel calls have 10s timeout. If a phase times out, it advances to the next phase instead of hanging.
6. Overall unwind has a timeout of `(phase1 + phase2) × num_legs + 60s`. On timeout, records worst-case loss and trips circuit breaker.
7. Phase timings are configurable per risk mode via `unwind_phase1_secs`, `unwind_phase2_secs`, `unwind_price_step_cents`
8. Emergency shutdown (`_emergency_shutdown`) retries 3× with exponential backoff; cancel and close operations are independent so a 429 on cancels doesn't block position closes

### Execution Fidelity — Defense in Depth

Eight layers of timeout protection prevent hanging API calls from freezing the bot:

1. **HTTP transport** — `aiohttp.ClientTimeout(total=30, connect=10, sock_read=15)` on the session. No HTTP request can hang >30s.
2. **Executor API calls** — `asyncio.wait_for` on `batch_create_orders` (15s), `batch_cancel_orders` (10s), `get_balance` (10s) inside `execute()` and `_monitor_fills()`.
3. **Emergency shutdown** — 60s overall timeout, 15s per API call inside. Idempotency guard prevents duplicate concurrent invocations.
4. **Boot reconcile** — 60s timeout. On timeout, proceeds without full reconciliation.
5. **WebSocket reconnect** — 30s timeout on `connect()` inside `_reconnect()`.
6. **Orderbook staleness** — `OrderbookManager.market_age()` tracks seconds since last update. Dispatcher skips signal evaluation when any market in the event is >2s stale.
7. **SIGTERM handler** — `signal.SIGTERM`/`SIGINT` trigger graceful shutdown: cancel tasks, await unwinds, close connections.
8. **Recent trades** — 10s initial timeout + 5s retry on `get_market_trades()`. On double timeout or retry failure, treats as "no recent trades" (rejects the signal).
9. **Sequential leg execution** — Legs executed one at a time, highest price first. If any leg goes resting, cancel it immediately and unwind only the already-filled legs. Eliminates the worst partial-fill scenario where multiple expensive legs fill before a cheap leg fails.
10. **Ask-side depth check** — `_validate_legs` verifies each market has sufficient ask-side depth (`min_ask_depth`). One-sided markets (bids only, no asks) are rejected as likely stale/phantom.

### MCP Server (`src/mcp_server.py`)

Tools: `close_all_positions`, `close_position`, `get_positions`, `get_risk_profile`, `get_performance_report`, `get_parameter_sensitivity`, `get_near_misses`, `get_signal_history`, `get_replay_comparison`. Configured in `.claude/settings.local.json`.

## Fee Math

Fee calculations are parameterized by `FeeModel` (`src/ports/fee_model.py`). Each exchange provides its own implementation. Profit functions in `src/core/fees.py` accept a `FeeModel` and apply `taker_fee`, `maker_fee`, and `profit_fee` per exchange.

**Kalshi** (`src/exchanges/kalshi/fee_model.py`): Taker fee = `0.07 * price * (1 - price)` per contract. Maker fee = 0%. Profit fee = 0%. The taker fee is symmetric and maximized at 50¢ (1.75¢/contract).

## API Specifics

- **Auth**: RSA-PSS SHA256 signing. Message = `{timestamp_ms}{METHOD}{path}` (path without query params). Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`.
- **Rate limiting**: Token budget system. Basic tier = 200 read tokens/sec, 100 write/sec. Most requests cost 10 tokens. Bot throttles to 100ms minimum between requests with 429 retry + exponential backoff.
- **Batch order response**: Nested structure `{"orders": [{"order": {"order_id": "...", ...}}]}` — note the extra `"order"` wrapper.
- **Live API URL**: `api.elections.kalshi.com` (not `trading-api.kalshi.com`, which redirects).

## Config

`config.yaml` (gitignored) with `exchange: kalshi`, `mode: demo|live`, and `risk_mode: conservative|moderate|aggressive`. See `config.example.yaml` for all params. Keys live at `~/.kalshi/{demo,live}_private_key.pem`. Credentials support both flat format (`credentials.demo`) and nested format (`credentials.kalshi.demo`) for multi-exchange.

**IMPORTANT:** After any change to config parsing, risk profiles, or strategy parameters, always diff `config.yaml` against `config.example.yaml` and remove stale overrides. Old strategy fields (e.g. `min_bid_depth: 1`) silently override risk profile defaults and can neutralize new protections.

`recording:` section controls data recording to DuckDB (enabled by default). All sessions are stored in a single DuckDB file (`data/arb_history.duckdb`). Cleanup prunes oldest sessions' rows when DB size exceeds `retention_max_db_size_mb`, then checkpoints to reclaim disk space. Orderbook snapshots are buffered and flushed in batches (`write_buffer_size`, default 50) for performance. Use `python3 -m src.analytics` for reports, `python3 -m src.replay --sweep` for parameter sweep analysis, and `python3 -m src.dry_run` for replay with fault injection. See `config.example.yaml` for recording options.

## Observability

Near-miss signals are logged at DEBUG level (set `logging.level: DEBUG` in config to enable):
- `taker near-miss <event>: bid_sum=X.XXXX` — passed all filters but bid sum < taker breakeven (~$1.07)
- `maker near-miss <event>: bid_sum=X.XXXX` — passed all filters but bid sum < $1.00
- `near-miss <event>: bid_sum=X.XXXX blocked — <ticker> depth/volume < min` — price was in range but depth/volume filter rejected
- `maker horizon-filtered <event>: ... closes_in=Xh horizon=Yh` — maker-profitable signal blocked by horizon cutoff
- `buy-side coverage-filtered <event>: ask_sum=X.XXXX max_ask=X.XXXX — likely missing high-probability outcome legs` — buy-side arb rejected by the two-guard coverage filter: ask_sum < 0.60 (only low-prob outcomes registered) OR max_ask < 0.20 (no single outcome has meaningful probability). Both guards are needed: sum alone is fooled by many mid-prob outcomes; max alone is fooled by a single slightly-elevated outcome in an incomplete set.
- `monotone_arb_detected pair=<upper>|<lower>` — threshold constraint violation detected (logged at INFO)
- STATUS line includes `maker_horizon=N` — count of events closing within `maker_max_horizon_hours` right now

## Known Limitations

- `calculate_event_pnl` in `core/positions.py` assumes equal fill quantities across all legs
- `profit_pct` is relative to $1 max payout, not capital at risk
