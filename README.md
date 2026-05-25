# kalshi-arb

An asyncio bot that detects and executes arbitrage opportunities on prediction markets. It monitors mutually exclusive event markets in real time via WebSocket and fires trades when mispricings exceed fees.

## How It Works

Prediction markets price outcomes as contracts between $0 and $1. For a mutually exclusive event (e.g. "Which candidate wins?"), the prices of all outcomes should sum to $1. When they don't, there's an arbitrage opportunity.

The bot runs five concurrent strategies:

| Strategy | Signal | Execution |
|----------|--------|-----------|
| **Sell-side taker** | Bid sum > $1 + fees | Sell all outcomes at best bid (IOC) |
| **Buy-side taker** | Ask sum < $1 − fees | Buy all outcomes at best ask (IOC) |
| **Maker** | Bid sum > $1 but below taker threshold | Post limit orders at best bid, complete on fill |
| **Near-expiry** | Stale mispricing near market close | Relaxed filters, same taker execution |
| **Monotone constraint** | Threshold market price inversions | Buy low / sell high across related contracts |

All taker orders use immediate-or-cancel. Maker orders auto-expire after a configurable TTL. Partial fills trigger graduated unwind across 5 price phases.

## Architecture

Ports & adapters pattern — strategy logic depends only on abstract Protocol interfaces, never on exchange-specific code.

```
src/
  core/           Exchange-agnostic logic (engine, dispatch, fees, risk, recorder)
  ports/          Abstract interfaces (FeeModel, ExchangeAPI, OrderBuilder, etc.)
  exchanges/
    kalshi/       Kalshi REST + WebSocket adapters
  strategies/     Strategy implementations (taker, maker, near-expiry, monotone)
  executor.py     Order execution with partial fill protection
  main.py         Composition root
```

## Setup

**Prerequisites:** Python 3.11+, a Kalshi account with API access.

```bash
pip install -r requirements.txt
```

Generate an RSA key pair for Kalshi API auth and place the private key at:
- Demo: `~/.kalshi/demo_private_key.pem`
- Live: `~/.kalshi/live_private_key.pem`

Copy and edit the config:

```bash
cp config.example.yaml config.yaml
```

Set your `api_key`, `mode` (demo/live), and `risk_mode` (conservative/moderate/aggressive).

## Usage

```bash
# Run the bot
python3 -m src.main

# Run tests
python3 -m pytest tests/ -v

# Parameter sweep over recorded history
python3 -m src.replay --sweep

# Analytics report
python3 -m src.analytics
```

The bot starts in demo mode by default. It discovers events, subscribes to orderbook updates via WebSocket, evaluates strategies on every tick, and executes when signals pass all filters.

## Risk Modes

| Parameter | Conservative | Moderate | Aggressive |
|-----------|-------------|----------|------------|
| min_profit_pct | 2.0% | 1.0% | 0.5% |
| min_volume_24h | 50 | 10 | 0 |
| min_bid_depth | 5 | 2 | 1 |
| max_exposure_ratio | 2.0 | 3.0 | 5.0 |
| require_recent_trades | yes | yes | no |

Individual parameters can be overridden in `config.yaml`.

## MCP Server

The bot includes an MCP server for inspecting state from Claude Code or other MCP clients:

`get_positions`, `close_position`, `close_all_positions`, `get_risk_profile`, `get_performance_report`, `get_parameter_sensitivity`, `get_near_misses`, `get_signal_history`, `get_replay_comparison`

## License

[MIT](LICENSE)
