# PredictIt Integration — Design Spec

**Date:** 2026-05-17
**Status:** Draft
**Goal:** Add PredictIt as a second exchange adapter, enabling both intra-PredictIt arbitrage (reusing existing strategies) and future cross-exchange arbitrage against Kalshi.

## Context

PredictIt has no trading API. The only machine-readable data source is a public read-only JSON endpoint (`/api/marketdata/all/`) that refreshes every ~60 seconds. All trading must happen through browser automation against the web UI.

After winning its CFTC lawsuit (July 2025) and receiving DCM approval (September 2025), PredictIt raised per-contract position limits from $850 to $3,500. Fee structure: 10% on trade profits, 5% on withdrawals.

The existing ports & adapters architecture (Phase 1 of the exchange abstraction spec) was designed for this. Core trading logic in `src/core/` and `src/strategies/` depends only on abstract Protocol interfaces in `src/ports/`. PredictIt needs to implement those same 6 ports.

## Constraints

- **No API** — Data polling via public JSON endpoint; execution via Playwright browser automation
- **Bot detection risk** — PredictIt has anti-bot measures. All scraping goes through Decodo residential proxies. Manual login to establish authenticated sessions; bot reuses saved session state.
- **60-second data staleness** — JSON endpoint refreshes every ~60s. Arb opportunities must persist for minutes, not milliseconds. Wider profit thresholds than Kalshi.
- **Decodo for everything** — Opportunity identification always goes through Decodo proxies, even in production. No real IP for scraping, ever.

## Proxy Integration

Follow the same pattern as the ebay-analysis-toolkit project:

- **Credentials** in `.env` (gitignored), loaded via `python-dotenv`:
  ```
  DECODO_PROXY_URL=http://user:pass@us.decodo.com:10001
  PREDICTIT_SESSION_DIR=~/.kalshi/predictit_session
  ```
- **HTTP polling** — `httpx` with `HTTPTransport(proxy=proxy_url)` for JSON endpoint
- **Browser automation** — Same Decodo URL as Playwright `proxy` launch argument. Sticky sessions (same IP for browser session duration).
- **Anti-detection** — User-agent rotation from a pool of 5+, random 2-5s delays between requests, full browser-like headers (Accept, Accept-Language, DNT, etc.)

No separate proxy abstraction module. Proxy URL passed directly from config into the PredictIt adapter.

## PredictIt Adapter Structure

```
src/exchanges/predictit/
  __init__.py          # PredictItExchange facade
  scraper.py           # JSON endpoint polling via httpx + Decodo
  discovery.py         # PredictItDiscovery (MarketDiscovery) — parse JSON into Event/Market
  scanner.py           # PredictItScanner (OrderbookFeed) — diff-based updates from polls
  fee_model.py         # PredictItFeeModel (10% profit + 5% withdrawal)
  order_builder.py     # PredictItOrderBuilder — UI interaction descriptors
  constraints.py       # PredictItConstraints ($3,500 per contract)
  browser.py           # Playwright session manager (login, navigation, orders)
  api.py               # PredictItAPI (ExchangeAPI) — backed by Playwright
  anti_detect.py       # User-agent rotation, timing delays, header randomization
```

### Port Implementations

**FeeModel** (`fee_model.py`):
- `taker_fee(price)` — Returns 0. PredictIt doesn't charge per-trade taker fees.
- `maker_fee(price)` — Returns 0.
- `profit_fee(gross_profit)` — Returns `0.10 * gross_profit` (10% profit fee). When `include_withdrawal_fee` is enabled (default: true), returns `0.145 * gross_profit` (10% profit + 5% of remaining on withdrawal; combined effective rate).

Note: PredictIt's fee model differs structurally from Kalshi's. Kalshi charges per-contract taker fees upfront. PredictIt charges on profits at settlement. The `FeeModel` protocol is flexible enough to handle both — `profit_fee()` was designed for this case.

**PositionConstraints** (`constraints.py`):
- `max_position_size(ticker)` — Returns 3500 (contracts, at $1 max payout = $3,500)
- `max_total_exposure()` — Returns None (no account-wide cap beyond per-contract)

**MarketDiscovery** (`discovery.py`):
- `full_scan()` — Fetches `/api/marketdata/all/` through Decodo, parses into `Event`/`Market` models
- `poll_loop()` — Re-fetches every 60s (matching PredictIt's data refresh cadence)
- `cleanup_loop()` — Removes markets past their close date
- Maps PredictIt's data structure: `markets[].contracts[]` with fields `bestBuyYesCost`, `bestBuyNoCost`, `bestSellYesCost`, `bestSellNoCost`, `lastTradePrice`

**OrderbookFeed** (`scanner.py`):
- No WebSocket available. Implements the `OrderbookFeed` protocol via polling:
  - `connect()` — Starts the polling loop
  - `listen()` — Yields orderbook update events by diffing current vs previous poll data
  - `subscribe(tickers)` — Tracks which markets to monitor
- Each poll produces synthetic "delta" events for markets whose prices changed
- Update frequency: ~60s (PredictIt's data refresh rate)

**ExchangeAPI** (`api.py`):
- Every method delegates to `browser.py` Playwright actions:
  - `batch_create_orders(orders)` — Navigate to market page, fill order form, submit. Sequential execution (one order at a time through the UI).
  - `cancel_order(order_id)` — Navigate to open orders, find and cancel
  - `get_positions()` — Navigate to portfolio page, scrape positions table
  - `get_balance()` — Scrape balance from account page
  - `get_market_trades(ticker)` — Scrape recent trades from market page (requires browser, not available via JSON endpoint)
- All browser actions include random human-like delays (typing speed, click timing)

**OrderBuilder** (`order_builder.py`):
- Instead of building JSON payloads, builds structured dicts describing UI interactions:
  ```python
  {
      "market_url": "https://www.predictit.org/markets/detail/7456",
      "contract_id": 28541,
      "action": "buy",     # buy or sell
      "outcome": "yes",    # yes or no
      "shares": 10,
      "price": 45,         # in cents
  }
  ```
- The browser module consumes these descriptors to drive Playwright

### Browser Session Manager (`browser.py`)

- **Manual login flow:** Launches Playwright browser with Decodo proxy. You log in manually (handle CAPTCHA/2FA). Bot saves session state (cookies, localStorage) to `PREDICTIT_SESSION_DIR`.
- **Session reuse:** On subsequent runs, loads saved session state. Verifies session is still valid by checking for logged-in indicators on a page load.
- **Session expiry detection:** If session is expired, alerts the user to re-login manually. Does not attempt programmatic login.
- **Human-like behavior:** Random delays between actions (0.5-2s), realistic mouse movements, randomized typing speed.
- **Headless mode:** Configurable. Headless for production, headed for debugging and manual login.

### Anti-Detection (`anti_detect.py`)

Shared utilities reused across scraper and browser:
- User-agent pool (5+ diverse browser strings, rotated per session)
- Browser-like HTTP headers (Accept, Accept-Language, Accept-Encoding, DNT)
- Random delay generator (configurable min/max, default 2-5s for scraping, 0.5-2s for in-page actions)
- Viewport randomization for Playwright (slight variations in window size)

## Fee Math

PredictIt profit calculation differs from Kalshi:

**Kalshi:** `net_profit = sum(bid_prices) - $1.00 - sum(taker_fees)`
- Taker fee charged upfront per contract: `0.07 * price * (1 - price)`
- No profit fee

**PredictIt:** `net_profit = (gross_profit - profit_fee) * (1 - withdrawal_rate)`
- No per-trade fee
- 10% profit fee on winning trades at settlement
- 5% withdrawal fee on funds leaving the platform
- For arb (guaranteed profit): `net = gross * 0.90 * 0.95 = gross * 0.855`

The existing `arb_profit()` and `arb_profit_buy_side()` functions in `src/core/fees.py` already accept a `FeeModel` parameter. PredictIt's `PredictItFeeModel` returns the right values from `profit_fee()`, and the profit calculations work unchanged.

## Intra-PredictIt Strategies

All existing strategies from `src/strategies/` work against PredictIt data via the port interfaces:

- **Sell-side taker** — bid_sum > $1 + fees. Works, but profit threshold needs to be higher to account for 60s data staleness and browser execution latency.
- **Buy-side taker** — ask_sum < $1 - fees. Works, same latency caveats.
- **Near-expiry** — Works. PredictIt markets near expiry may have wider spreads.
- **Maker** — Limit order posting. Requires browser automation to place and monitor limit orders. Significantly more complex due to UI-based order management.
- **Monotone** — Works if PredictIt has threshold-based markets (e.g., "Will unemployment be above 5%?" at multiple thresholds).
- **Two-sided** — Paired bid/ask posting. Same complexity as maker — UI-based order lifecycle.

**Recommended initial strategy subset:** Sell-side taker and buy-side taker only for Phase 3. Maker and two-sided require robust browser-based order monitoring that should be a later enhancement.

## Cross-Exchange Arbitrage (Phase 4, Future)

**Market correlation** — Manual YAML mapping file (`config/market_mappings.yaml`):
```yaml
mappings:
  - name: "2026 Presidential Election"
    kalshi_event: "PRES-2026"
    predictit_market_id: 7456
    contracts:
      - kalshi_ticker: "PRES-2026-DEM"
        predictit_contract_id: 28541
        label: "Democratic nominee wins"
```

**Cross-exchange engine** (`src/core/cross_exchange.py`):
- Subscribes to price updates from both exchanges
- Compares prices for mapped market pairs
- Identifies directional arbs ("Buy YES on PredictIt at $0.45, Sell YES on Kalshi at $0.55")
- Profit calculation accounts for both fee models
- Wider minimum spread (5-10%) to account for PredictIt data staleness

**Execution:** Sequential — Kalshi first (faster, REST API), then PredictIt (slower, Playwright). If PredictIt leg fails, unwind Kalshi position.

## Configuration

```yaml
# config.yaml additions
exchange: predictit  # or kalshi (existing)

# PredictIt-specific
predictit:
  poll_interval_secs: 60
  session_dir: ~/.kalshi/predictit_session
  headless: true
  include_withdrawal_fee: true
  # Strategy tuning for slower data
  min_profit_pct: 5.0        # higher than Kalshi due to staleness
  execution_timeout_secs: 30  # browser actions are slow
```

`.env` additions:
```
DECODO_PROXY_URL=http://user:pass@us.decodo.com:10001
```

## Phased Delivery

### Phase 1 — Data Layer
- `scraper.py` — JSON endpoint polling via httpx + Decodo
- `discovery.py` — Parse markets into Event/Market models
- `scanner.py` — Diff-based orderbook updates
- `fee_model.py`, `constraints.py`
- `anti_detect.py` — shared anti-detection utilities
- `.env` setup with Decodo credentials
- **Validation:** Poll through Decodo for 24+ hours, verify no blocks, verify data quality

### Phase 2 — Browser Automation
- `browser.py` — Playwright session manager with Decodo proxy
- Manual login flow, session persistence
- Market page navigation, depth scraping
- `api.py` — ExchangeAPI backed by Playwright
- `order_builder.py` — UI interaction descriptors
- **Validation:** Navigate markets, read depth data, place a test order (smallest position)

### Phase 3 — Full Integration
- Wire into `src/exchanges/__init__.py` factory
- Config support for `exchange: predictit`
- Taker strategies (sell-side, buy-side) working against PredictIt
- Risk profile tuning for PredictIt characteristics
- **Validation:** Run in signal-only mode, verify strategy logic produces sensible signals

### Phase 4 — Cross-Exchange Arb (Future)
- Market mapping YAML
- `src/core/cross_exchange.py`
- Dual-exchange bot mode in `main.py`
- Simultaneous Kalshi + PredictIt operation

## Multi-Exchange Extensibility

The adapter pattern keeps exchange-specific code fully isolated. Adding a third exchange (e.g., IBKR ForecastEx, Polymarket) follows the same template:
1. Create `src/exchanges/<name>/` with the 6 port implementations
2. Register in `src/exchanges/__init__.py` EXCHANGES dict
3. Add config section
4. All strategies work unchanged via port interfaces

Nothing in `src/core/`, `src/strategies/`, or `src/ports/` changes when adding exchanges.
