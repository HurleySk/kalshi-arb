# Maker Strategy Design

## Problem

The taker strategy requires bid sum > ~$1.07 (after 7% taker fees) to be profitable. Conservative mode correctly filters out phantom liquidity, but this leaves almost zero opportunities — 10 minutes across 3,326 events yielded zero arbs. Meanwhile, many events have bid sums between $1.00 and $1.07 that would be profitable at maker fee rates (0%).

## Design

### Two-Layer Architecture

The bot runs two strategies through the same pipeline:

**Taker layer (existing)** — Bid sum > $1.00 + taker fees (~$1.07). Immediately cross the spread on all legs. Unchanged behavior, same conservative filters.

**Maker layer (new)** — Bid sum between $1.00 and $1.07. Post limit orders at current best bid prices on all legs as maker (0% fees). Wait for the market to fill them. Gated by `max_maker_events` (default 3) to manage capital.

Both layers share `ArbEngine.evaluate()` with the same volume, depth, and recent-trade filters.

### Maker Order Lifecycle

1. **Post** — Place sell-yes limit orders on all legs at current best bid. Track as active maker event.

2. **Monitor** — Watch for fills via WebSocket. On each orderbook update, reprice orders to match current best bid. If bid sum drops below $1.00, cancel all unfilled legs and free the slot.

3. **First fill triggers completion** — Configurable via `maker_fill_mode`:
   - `cancel_and_take` (default): Cancel remaining maker orders, place taker orders to complete the arb. Saves maker fee on the filled leg, pays taker on the rest. Guaranteed completion, no lingering exposure.
   - `tighten_on_fill`: Move unfilled orders to more aggressive prices. If not filled within phase 1 timeout, tighten again. If still unfilled after phase 2, cross the spread. Reuses existing tiered urgency timing from risk profile.

4. **Reprice** — On orderbook updates, if a leg's best bid moved, cancel and repost at new price. Throttle to max once per second per event for rate limiting.

### Edge Cases

- **All legs fill as maker** — Pure profit, zero fees. Free the slot.
- **Taker arb appears on active maker event** — Cancel maker orders, let taker layer handle it.
- **Event settles while resting** — Kalshi cancels orders. Maker manager detects and cleans up slot.
- **Circuit breaker trips** — Cancel all resting maker orders across all events immediately.
- **Bid sum drops below $1.00** — Cancel unfilled legs. Any filled legs trigger existing unwind system.

### Config

```yaml
strategy:
  maker_enabled: true
  maker_fill_mode: cancel_and_take  # cancel_and_take | tighten_on_fill
  max_maker_events: 3               # max simultaneous events with maker orders
```

### File Changes

- `src/models.py` — Add `signal_type: str` to `TradeSignal` (default "taker")
- `src/engine.py` — Return maker signals for bid sum $1.00–$1.07
- `src/maker.py` (new) — `MakerManager` class: post, monitor, reprice, complete
- `src/main.py` — Route maker signals to MakerManager, call reprice on orderbook updates
- `src/config.py` — Parse maker config fields

Unchanged: fees.py, risk.py, scanner.py, positions.py, executor.py, all existing tests.

If `maker_enabled: false`, the bot behaves identically to today.

### Fee Math

Taker fee: `0.07 × price × (1 - price)` per contract.
Maker fee: 0%.

For a 2-leg event with bids at $0.55 and $0.50 (sum $1.05):
- As taker: fees = 0.07×0.55×0.45 + 0.07×0.50×0.50 = $0.0173 + $0.0175 = $0.035. Profit = $1.05 - $1.00 - $0.035 = $0.015 (1.5%)
- As maker: fees = $0. Profit = $1.05 - $1.00 = $0.05 (5.0%)
- As hybrid (cancel_and_take, first leg fills as maker): fees on second leg only = $0.0175. Profit = $1.05 - $1.00 - $0.0175 = $0.0325 (3.25%)
