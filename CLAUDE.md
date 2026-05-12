# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python asyncio bot that detects and executes arbitrage opportunities on Kalshi prediction markets. The strategy: sell "yes" on all outcomes of mutually exclusive events where the sum of best bids exceeds $1 + fees.

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

Three async components run concurrently in `ArbBot.run()` (`src/main.py`):

1. **Event Discovery** (`_discover_events` → `api.fetch_events_page`) — REST polls Kalshi for mutually exclusive events with 2+ active markets. Does a full paginated scan on startup (~70 pages, sorted by close_time so near-term events subscribe first), then re-polls page 1 every 60s for new events.

2. **WebSocket Scanner** (`scanner.MarketScanner` + `scanner.OrderbookManager`) — Maintains real-time orderbook state via `orderbook_delta` channel. On every update, fires `_on_orderbook_update` callback synchronously.

3. **Arb Detection → Execution** (`_on_orderbook_update` → `engine.evaluate` → `executor.execute`) — The hot path. On each orderbook update, evaluates whether selling yes on all outcomes is profitable after taker fees (7%). If profitable and passes filters, fires a batch order via REST API.

### Data flow

```
REST /events → parse_events → register with OrderbookManager → WS subscribe
WS orderbook_delta → OrderbookManager.apply_snapshot/apply_delta
  → _on_orderbook_update → ArbEngine.evaluate (with market metadata for close_time)
    → ExecutionManager.execute → batch POST /portfolio/orders/batched
      → monitor fills → cancel unfilled legs after timeout
```

### Key filtering pipeline in `ArbEngine.evaluate()`

1. All markets must have a best yes bid
2. `min_bid_depth` check (configurable, default 1)
3. `arb_profit(bid_prices) > 0` (sum of bids > $1 + taker fees)
4. Time-horizon check: events within `near_term_hours` (24h) use flat `min_profit_pct` (1.0%); longer-dated must beat `hurdle_rate_annual_pct` (10%) annualized
5. `exposure_ratio <= max_exposure_ratio` (worst-case loss / net premium)

## Fee Math

Taker fee: `0.07 * price * (1 - price)` per contract. All orders cross the spread (limit orders at best bid fill immediately as taker). The fee is symmetric and maximized at 50¢ (1.75¢/contract).

## API Specifics

- **Auth**: RSA-PSS SHA256 signing. Message = `{timestamp_ms}{METHOD}{path}` (path without query params). Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE`.
- **Rate limiting**: Token budget system. Basic tier = 200 read tokens/sec, 100 write/sec. Most requests cost 10 tokens. Bot throttles to 100ms minimum between requests with 429 retry + exponential backoff.
- **Batch order response**: Nested structure `{"orders": [{"order": {"order_id": "...", ...}}]}` — note the extra `"order"` wrapper.
- **Live API URL**: `api.elections.kalshi.com` (not `trading-api.kalshi.com`, which redirects).

## Config

`config.yaml` (gitignored) with `mode: demo|live`. See `config.example.yaml` for all params. Keys live at `~/.kalshi/{demo,live}_private_key.pem`.

## Known Limitations

- `calculate_event_pnl` in `positions.py` assumes equal fill quantities across all legs
- `profit_pct` is relative to $1 max payout, not capital at risk
- No WebSocket reconnection logic — a disconnect stops all monitoring
- `volume_24h`, `open_interest`, `liquidity` fields are extracted from the API but not yet used for filtering
