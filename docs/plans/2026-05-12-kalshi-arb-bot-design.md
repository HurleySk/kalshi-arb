# Kalshi Arbitrage Bot — Design Document

## Purpose

A Python bot that runs continuously on a Raspberry Pi, monitoring Kalshi multi-outcome events for arbitrage opportunities where the sum of "yes" bid prices across all outcomes exceeds $1.00 after fees. When an opportunity is found that meets configured profit and risk thresholds, the bot batch-submits sell orders on all outcomes to lock in guaranteed profit.

Designed for reuse: the core arbitrage logic is exchange-agnostic so it can be extended to intra-Polymarket and cross-platform (Kalshi ↔ Polymarket) arbitrage later.

## Core Concepts

### The Arbitrage

In a multi-outcome event on Kalshi (e.g., "What will the Fed rate be?"), exactly one outcome resolves yes ($1.00) and all others resolve no ($0.00). If the sum of best bid prices across all outcomes exceeds $1.00, you can sell "yes" on every outcome. You collect all the premiums and only pay out $1.00 on whichever outcome wins — the difference is profit.

### Fee Model

Kalshi charges maker fees on filled orders:

```
maker_fee(price) = 0.0175 × price × (1 - price)
```

Maximum ~0.44¢ per contract at the 50¢ price point, tapering toward the extremes. No fees on unfilled or cancelled orders. No settlement fees.

The bot subtracts fees from every arb calculation so only genuinely profitable opportunities are acted on.

### Risk Management

**Minimum profit threshold:** Configurable minimum profit percentage after fees (default 2%). Opportunities below this are skipped.

**Exposure ratio:** Protects against partial fill risk. When selling N outcomes but one leg fails to fill, you have unhedged exposure. When you sell "yes" and the outcome resolves yes, you owe exactly $1.00 regardless of sale price. The worst case is: the most expensive leg fails to fill (you miss the most premium), and any filled leg resolves yes (you owe $1). The ratio measures:

```
worst_loss = max(0, 1.0 - (sum_of_bids - max_bid))
exposure_ratio = worst_loss / net_premium_after_fees
```

Uses net premium (after fees) as the denominator for a conservative risk picture. Only arbs below the configured max exposure ratio (default 3.0) are attempted. This naturally makes the bot more conservative on high-leg-count events.

**Fill timeout:** After batch-submitting orders, unfilled legs are cancelled after a configurable timeout (default 30s). Cancellation is free on Kalshi.

## Architecture

### Components

```
Kalshi WebSocket
    │
    ├── orderbook_delta channel
    │       │
    │       ▼
    │   Market Scanner
    │   (maintains orderbook state per market)
    │       │
    │       ▼ (on every update)
    │   Arbitrage Engine
    │   ├── sum best bids across all outcomes for event
    │   ├── subtract maker fees per leg
    │   ├── check: profit > min threshold?
    │   ├── check: exposure ratio < max?
    │   └── if both pass → emit trade signal
    │       │
    │       ▼
    │   Execution Manager
    │   ├── batch submit limit orders at bid prices
    │   ├── monitor fills via WebSocket fill channel
    │   ├── timeout → cancel unfilled legs
    │   └── log position + P&L
    │
    ├── fill channel → Execution Manager
    ├── user_orders channel → Execution Manager
    └── market_lifecycle channel → Market Scanner
```

**Market Scanner** — Manages WebSocket connection to Kalshi. Subscribes to `orderbook_delta` for all active multi-outcome events. Maintains in-memory orderbook state per market. Handles reconnection with exponential backoff and full snapshot resync on sequence gaps.

**Arbitrage Engine** — On every orderbook update, recalculates arb opportunity for that event. Applies fee model, profit threshold, and exposure ratio filter. Exchange-agnostic — operates on abstract bid price lists.

**Execution Manager** — Receives trade signals, batch-submits limit orders via REST API, monitors fills via WebSocket `fill` and `user_orders` channels. Handles timeout-based cancellation. Tracks all open positions and P&L.

**Event Discovery** — Lightweight REST poller running every ~60 seconds, calling `GET /events` to discover new multi-outcome events and subscribe to their orderbook channels. Only REST polling in the system.

### State Management

All state is in-memory:
- Orderbooks: rebuilt from WebSocket snapshots
- Open positions: fetched from `GET /portfolio/positions` on startup
- Open orders: fetched from `GET /portfolio/orders` on startup

No database required. On restart, state is reconstructed from the API.

WebSocket deltas include sequence numbers. On gap detection, the bot fetches a full orderbook snapshot via REST before trusting deltas again.

### Demo / Live Mode

Config toggle switches between environments:

| Mode | REST Base | WebSocket |
|------|-----------|-----------|
| demo | `https://external-api.demo.kalshi.co/trade-api/v2` | `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2` |
| live | `https://external-api.kalshi.com/trade-api/v2` | `wss://external-api-ws.kalshi.com/trade-api/ws/v2` |

Default mode is `demo`. Separate API keys per environment. Same code paths for both.

### Authentication

Kalshi uses RSA-PSS key signing. Three headers per request:
- `KALSHI-ACCESS-KEY` — API key ID
- `KALSHI-ACCESS-TIMESTAMP` — millisecond Unix timestamp
- `KALSHI-ACCESS-SIGNATURE` — base64 RSA-PSS/SHA-256 signature of `timestamp + method + path`

Private key stored locally, path configured in `config.yaml`.

## Project Structure

```
kalshi-arb/
├── config.yaml
├── config.example.yaml
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── main.py                 # entry point
│   ├── config.py               # loads & validates config
│   ├── auth.py                 # RSA-PSS request signing
│   ├── models.py               # dataclasses: Event, Market, Order, Position
│   ├── fees.py                 # maker_fee, arb_profit, exposure_ratio
│   ├── scanner.py              # WebSocket connection, orderbook state
│   ├── engine.py               # arbitrage detection logic
│   ├── executor.py             # order placement, fill monitoring, cancellation
│   ├── positions.py            # position tracking, P&L calculation
│   └── api.py                  # REST client (event discovery, order mgmt)
├── tests/
│   ├── test_fees.py
│   ├── test_engine.py
│   └── test_executor.py
└── logs/
```

### Reusability Boundary

- **Exchange-agnostic core:** `engine.py`, `fees.py` (parameterized by exchange), `models.py`, `positions.py`
- **Exchange-specific adapters:** `scanner.py`, `api.py`, `auth.py`

To add Polymarket: create `scanner_poly.py`, `api_poly.py`, `auth_poly.py` implementing the same interfaces. The engine and execution logic remain unchanged.

## Configuration

```yaml
mode: demo  # demo | live

credentials:
  demo:
    api_key_id: "your-demo-key-id"
    private_key_path: "~/.kalshi/demo_private_key.pem"
  live:
    api_key_id: "your-live-key-id"
    private_key_path: "~/.kalshi/live_private_key.pem"

strategy:
  min_profit_pct: 2.0          # minimum profit % after fees
  max_exposure_ratio: 3.0      # max worst-case-loss / total-premium
  fill_timeout_secs: 30        # cancel unfilled legs after this
  event_poll_interval_secs: 60 # REST poll interval for new events

logging:
  level: INFO
  file: logs/arb_bot.log
```

## Logging

Every arb evaluation and execution is logged as structured JSON:

```json
{
  "timestamp": "2026-05-12T14:30:00Z",
  "event_ticker": "FED-RATE-DEC25",
  "num_outcomes": 5,
  "bid_prices": [0.30, 0.25, 0.20, 0.15, 0.15],
  "sum": 1.05,
  "fees": 0.0098,
  "net_profit": 0.0402,
  "profit_pct": 4.02,
  "exposure_ratio": 2.1,
  "action": "EXECUTE",
  "fills": [],
  "unfilled": []
}
```

## Future Extensions

- **Intra-Polymarket arbitrage:** Add Polymarket scanner/API adapters, reuse engine
- **Cross-platform arbitrage (Kalshi ↔ Polymarket):** Engine compares orderbooks across exchanges for the same event
- **Alerting:** Push notifications on arb execution or anomalies
- **Dashboard:** Simple web UI showing live positions, P&L, opportunity history
