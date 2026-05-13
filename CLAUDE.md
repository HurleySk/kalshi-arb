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

Five async components run concurrently in `ArbBot.run()` (`src/main.py`):

1. **Event Discovery** (`src/discovery.py` → `EventDiscovery.poll_loop`) — REST polls Kalshi for mutually exclusive events with 2+ active markets. Does a full paginated scan on startup (~70 pages, sorted by close_time so near-term events subscribe first), then re-polls page 1 every 60s for new events. Also runs a `cleanup_loop` every 5 minutes to remove expired events from `OrderbookManager` and `market_metadata`.

2. **WebSocket Scanner** (`scanner.MarketScanner` + `scanner.OrderbookManager`) — Maintains real-time orderbook state via `orderbook_delta` channel. On every update, enqueues the market ticker to `_ob_update_queue`. Reconnects automatically on disconnect.

3. **Orderbook Processor** (`_process_orderbook_updates`) — Drains `_ob_update_queue` and routes each update through `Dispatcher` (`src/dispatch.py`). Dispatcher evaluates arb signals, guards duplicate execution per event (pending set), enforces signal cooldowns, and routes fills.

4. **Arb Detection → Execution** (`Dispatcher.process_orderbook_update` → `engine.evaluate` → `executor.execute`) — The hot path. On each orderbook update, evaluates whether selling yes on all outcomes is profitable after taker fees (7%). If profitable and passes filters, fires a batch order via REST API.

5. **Maker Layer** (`engine.evaluate_maker` → `maker.MakerManager`) — When bid sum is between $1.00 and the taker threshold (~$1.07), posts limit orders at best bid prices as maker (0% fees). On fill, completes the arb via cancel_and_take (cancel remaining, taker-complete) or tighten_on_fill (progressively tighten prices). Reprices on orderbook updates, cancels if arb disappears.

6. **Two-Sided Market Making** (`engine.evaluate_two_sided` → `two_sided.TwoSidedManager`) — Per-market spread capture. When spread ≥ `two_sided_min_spread_cents + 2`, posts limit bid and ask each 1¢ inside NBBO. On fill, cancels the opposite side and unwinds. Times out unfilled pairs after `two_sided_timeout_secs`.

### Key modules

- `src/discovery.py` — `EventDiscovery`: REST scan, register events, cleanup expired; `MonotoneFamilyRegistry`: groups threshold markets by template key
- `src/dispatch.py` — `Dispatcher`: orderbook update routing, pending guard, fill routing
- `src/engine.py` — `ArbEngine`: taker, maker, buy-side, near-expiry, monotone, and two-sided signal evaluation
- `src/executor.py` — `ExecutionManager`: batch order execution, partial fill unwind
- `src/maker.py` — `MakerManager`: limit order posting and fill handling
- `src/scanner.py` — `MarketScanner` + `OrderbookManager`: WebSocket + orderbook state
- `src/two_sided.py` — `TwoSidedManager`: paired bid/ask order lifecycle, inventory cap, timeout/unwind

### Data flow

```
REST /events → EventDiscovery.register_events → OrderbookManager.register_event → WS subscribe
               └─ MonotoneFamilyRegistry.try_register (group threshold markets)
WS orderbook_delta → OrderbookManager.apply_snapshot/apply_delta → _ob_update_queue
  → Dispatcher.process_orderbook_update
    → ArbEngine.evaluate (taker: bid sum > $1.07) → ExecutionManager.execute
    → ArbEngine.evaluate_buy_side (buy all outcomes: ask sum < $1 - fees) → ExecutionManager.execute
    → ArbEngine.evaluate_near_expiry (relaxed taker within near_expiry_window_minutes) → ExecutionManager.execute
    → ArbEngine.evaluate_monotone_pair (stacked threshold violation: sell upper, buy lower) → ExecutionManager.execute
    → ArbEngine.evaluate_maker (maker: bid sum $1.00-$1.07) → MakerManager.post
    → MakerManager.on_orderbook_update (reprice active maker orders)
    → ArbEngine.evaluate_two_sided (spread ≥ min_spread+2¢ per market) → TwoSidedManager.post
WS fill → TwoSidedManager.handle_fill OR Dispatcher.route_fill → executor.handle_fill OR maker.handle_fill
```

### Risk Modes (`src/risk.py`)

Three configurable risk profiles control all trading thresholds. Set `risk_mode` in `config.yaml`:

- **conservative** (default) — min_volume_24h=50, min_bid_depth=5, min_profit_pct=2.0%, require_recent_trades=true, max_exposure_ratio=2.0; near_expiry_window=30min; two_sided_max_inventory=10, spread≥6¢
- **moderate** — min_volume_24h=10, min_bid_depth=2, min_profit_pct=1.0%, require_recent_trades=true, max_exposure_ratio=3.0; near_expiry_window=60min; two_sided_max_inventory=25, spread≥4¢
- **aggressive** — min_volume_24h=0, min_bid_depth=1, min_profit_pct=0.5%, require_recent_trades=false, max_exposure_ratio=5.0; near_expiry_window=120min; two_sided_max_inventory=50, spread≥2¢

Individual overrides in `config.yaml` take precedence over preset values.

**New strategy fields** (all presets have defaults; override in `config.yaml` to customize):
- `enable_buy_side_arb` — buy all YES outcomes when ask sum < $1 - fees (default: true)
- `near_expiry_window_minutes` — window before close to use relaxed taker filters (0 = disabled)
- `near_expiry_min_profit_pct`, `near_expiry_min_bid_depth`, `near_expiry_min_volume_24h` — near-expiry specific thresholds
- `two_sided_max_inventory` — max total contracts in resting two-sided pairs (0 = disabled)
- `two_sided_min_spread_cents` — minimum spread required to post (need spread ≥ this + 2 to post 1¢ inside each side)
- `two_sided_timeout_secs` — cancel unfilled pairs after this many seconds
- `two_sided_min_volume_24h` — volume floor for two-sided candidates

### Key filtering pipeline in `ArbEngine.evaluate()`

1. All markets must have a best yes bid
2. `min_bid_depth` check (from risk profile)
3. `min_volume_24h` check — rejects legs with insufficient 24h trading volume
4. `arb_profit(bid_prices) > 0` (sum of bids > $1 + taker fees)
5. Time-horizon check: events within `near_term_hours` (24h) use flat `min_profit_pct`; longer-dated must beat `hurdle_rate_annual_pct` annualized
6. `exposure_ratio <= max_exposure_ratio` (worst-case loss / net premium)
7. Async `_validate_recent_trades` — when enabled, verifies each leg has recent trade activity before execution

### Partial Fill Protection

On partial fill (some legs fill, others don't), the executor:
1. Blacklists the event permanently (no re-execution)
2. Runs tiered auto-unwind on filled legs: Phase 1 (tight limit) → Phase 2 (wider limit) → Phase 3 (market order at $0.99)
3. Phase timings are configurable per risk mode via `unwind_phase1_secs`, `unwind_phase2_secs`, `unwind_price_step_cents`

### MCP Server (`src/mcp_server.py`)

Tools: `close_all_positions`, `close_position`, `get_positions`, `get_risk_profile`. Configured in `.claude/settings.local.json`.

## Fee Math

Taker fee: `0.07 * price * (1 - price)` per contract. All orders cross the spread (limit orders at best bid fill immediately as taker). The fee is symmetric and maximized at 50¢ (1.75¢/contract).

## API Specifics

- **Auth**: RSA-PSS SHA256 signing. Message = `{timestamp_ms}{METHOD}{path}` (path without query params). Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`.
- **Rate limiting**: Token budget system. Basic tier = 200 read tokens/sec, 100 write/sec. Most requests cost 10 tokens. Bot throttles to 100ms minimum between requests with 429 retry + exponential backoff.
- **Batch order response**: Nested structure `{"orders": [{"order": {"order_id": "...", ...}}]}` — note the extra `"order"` wrapper.
- **Live API URL**: `api.elections.kalshi.com` (not `trading-api.kalshi.com`, which redirects).

## Config

`config.yaml` (gitignored) with `mode: demo|live` and `risk_mode: conservative|moderate|aggressive`. See `config.example.yaml` for all params. Keys live at `~/.kalshi/{demo,live}_private_key.pem`.

**IMPORTANT:** After any change to config parsing, risk profiles, or strategy parameters, always diff `config.yaml` against `config.example.yaml` and remove stale overrides. Old strategy fields (e.g. `min_bid_depth: 1`) silently override risk profile defaults and can neutralize new protections.

## Observability

Near-miss signals are logged at DEBUG level (set `logging.level: DEBUG` in config to enable):
- `taker near-miss <event>: bid_sum=X.XXXX` — passed all filters but bid sum < taker breakeven (~$1.07)
- `maker near-miss <event>: bid_sum=X.XXXX` — passed all filters but bid sum < $1.00
- `near-miss <event>: bid_sum=X.XXXX blocked — <ticker> depth/volume < min` — price was in range but depth/volume filter rejected
- `maker horizon-filtered <event>: ... closes_in=Xh horizon=Yh` — maker-profitable signal blocked by horizon cutoff
- `buy-side coverage-filtered <event>: ask_sum=X.XXXX (threshold 0.60) — ask sum too low, possible incomplete outcome registration` — buy-side arb rejected because registered outcomes cost far less than 60¢ combined, indicating the bot likely only sees a partial subset of the event's markets
- `monotone_arb_detected pair=<upper>|<lower>` — threshold constraint violation detected (logged at INFO)
- STATUS line includes `maker_horizon=N` — count of events closing within `maker_max_horizon_hours` right now

## Known Limitations

- `calculate_event_pnl` in `positions.py` assumes equal fill quantities across all legs
- `profit_pct` is relative to $1 max payout, not capital at risk
