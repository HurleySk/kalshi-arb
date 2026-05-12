# Risk Modes and Loss Elimination Design

## Problem

The bot creates naked exposure on every trade due to partial fills. One leg fills instantly (the high-probability outcome) while the other leg's bid is phantom liquidity. The bot then re-executes the same failing arb every 60 seconds, compounding losses. No mechanism exists to unwind partial fills.

## Design

### Risk Mode Configuration

A `risk_mode` field in `config.yaml` selects a preset (`conservative`, `moderate`, `aggressive`). Individual overrides take precedence.

```yaml
strategy:
  risk_mode: conservative
  # Optional overrides:
  # min_volume_24h: 50
  # min_bid_depth: 5
```

Presets:

| Parameter | Conservative | Moderate | Aggressive |
|---|---|---|---|
| `min_volume_24h` | 50 | 10 | 0 |
| `min_bid_depth` | 5 | 2 | 1 |
| `min_profit_pct` | 2.0 | 1.0 | 0.5 |
| `require_recent_trades` | true | true | false |
| `max_exposure_ratio` | 2.0 | 3.0 | 5.0 |
| `unwind_phase1_secs` | 15 | 30 | 45 |
| `unwind_phase2_secs` | 30 | 60 | 90 |
| `unwind_price_step_cents` | 3 | 5 | 8 |

`RiskProfile` dataclass in `src/risk.py` holds all thresholds. `load_risk_profile(config)` returns the active profile with overrides applied.

### Liquidity Validation (Pre-Execution Gate)

Three checks run on every leg before execution, after existing profit/exposure filters:

1. **Volume check** — `volume_24h` (already parsed, currently unused) must meet `min_volume_24h`.
2. **Depth check** — Accumulated bid depth at best price must meet `min_bid_depth` (existing check, threshold raised).
3. **Recent trade check** — When `require_recent_trades` is true, call `GET /markets/{ticker}/history` to verify trading activity in the last N minutes. Only in Conservative and Moderate modes.

Pipeline position:

```
orderbook_delta → engine.evaluate() → profit/exposure filters
  → validate_liquidity()  ← NEW
  → executor.execute()
```

### Tiered Auto-Unwind

On partial fill, a three-phase unwind runs as an independent async task:

```
PARTIAL_FILL → Phase 1 (tight limit) → Phase 2 (widened limit) → Phase 3 (market) → CLOSED
```

Price widening example (sold yes on AND at $0.60, need to buy back):

- Phase 1 (0 to `unwind_phase1_secs`): Limit buy at $0.63 (fill price + `unwind_price_step_cents`)
- Phase 2 (phase1 to `unwind_phase2_secs`): Limit buy at $0.66 (fill price + 2x step)
- Phase 3 (phase2+): Buy at $0.99 (market-equivalent)

The unwind task runs independently of the main execution loop. The event stays blacklisted during and after unwinding.

Worst-case loss per contract is bounded: $1.00 - fill_price + fees. Resolves within 30-90 seconds depending on mode.

### File Changes

- `src/risk.py` (new) — `RiskProfile` dataclass, three presets, override logic
- `src/engine.py` (modified) — `validate_liquidity()` call after existing filters
- `src/executor.py` (modified) — `_unwind_partial_fill()` async task on partial fills
- `src/config.py` (modified) — Parse `risk_mode` and overrides
- `src/api.py` (modified) — `get_market_trades(ticker)` for recent-trade check
- `src/mcp_server.py` (modified) — `get_positions` and `get_risk_profile` tools

Core arb math, fee calculations, WebSocket, and event discovery are unchanged.

### Testing

- Unit: `RiskProfile` loading and overrides
- Unit: `validate_liquidity()` with mocked market data
- Unit: Unwind state machine with mocked timers and API
- Integration: Simulate partial fill, verify unwind sequence
- Existing 51 tests unchanged

#### Regression tests from real loss scenarios

Tests derived from the 2026-05-12 session where the bot lost money. These encode the exact conditions that caused losses so they can never regress:

1. **Phantom liquidity rejection** — Orderbook shows bid at $0.46 but `volume_24h=0`. Verify `validate_liquidity()` rejects the arb before execution. Uses real ticker/price data from the MEDLAN event.

2. **Partial fill detection** — Batch response returns one leg `status: executed`, one `status: resting`. Verify executor records the immediate fill, counts 1/2 filled (not 0/2), and flags unhedged exposure.

3. **Partial fill blacklist** — After a partial fill timeout, verify the event is blacklisted and subsequent orderbook updates for the same event do not trigger execution.

4. **Repeat execution prevention** — Simulate the exact MEDLAN scenario: arb detected, partial fill, timeout, same orderbook still shows arb 60s later. Verify the bot does NOT re-execute.

5. **Unwind fires on partial fill** — One leg fills at $0.60, other leg times out. Verify unwind task spawns, places buy-back order at phase 1 price, and escalates through phases if unfilled.

6. **Asymmetric fill simulation** — Two-leg arb where leg A ($0.99, high probability) always fills and leg B ($0.46, low probability) never fills. Verify the bot rejects this in Conservative mode due to low volume/depth on leg B, even though the arb math looks profitable.
