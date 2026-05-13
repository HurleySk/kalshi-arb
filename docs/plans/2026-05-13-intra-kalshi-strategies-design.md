# Intra-Kalshi Strategy Expansion — Design

## Overview

Four new strategies added to the existing taker-arb + maker-layer pipeline. All slot into the current architecture without changes to the WebSocket scanner, orderbook manager, or discovery core. Risk profiles gate parameters, not strategy eligibility — conservative gets conservatively calibrated versions of every strategy.

Ordered from least to most directional risk.

---

## Architecture

`ArbEngine` grows new `evaluate_*` methods alongside `evaluate()` and `evaluate_maker()`. `Dispatcher` routes signals to new executor/manager classes. `risk.py` gains new fields per strategy.

`OrderbookManager.get_orderbook_snapshot()` exposes `best_yes_ask` (already tracked in orderbook state, just not surfaced). No schema changes needed elsewhere.

New risk profile fields (all strategies):
- `enable_buy_side_arb: bool`
- `near_expiry_window_minutes: int`
- `min_monotone_pair_profit_pct: float`
- `min_spread_cents: int`
- `max_two_sided_inventory: int`
- `two_sided_timeout_secs: int`

---

## Strategy 1: Buy-Side Structural Arb

**Signal:** `sum(asks) < $1 - fees` across all outcomes of a mutually exclusive event.

**Edge:** Buy YES on every outcome for less than the guaranteed $1 payout. Zero directional risk — same math as sell-side taker arb, mirrored.

**Fee math:** `fee = 0.07 * price * (1 - price)` per leg. Profit = `$1 - sum(asks) - sum(fees)`.

**Implementation:**
- `ArbEngine.evaluate_buy_side()` — reads `best_yes_ask` instead of `best_yes_bid`, checks `sum < 1 - fees`
- Execution: buy-limit orders at ask price (crosses spread, fills as taker)
- Same batch order structure and partial fill protection as sell-side

**Risk calibration:**
| Profile | Status | Notes |
|---|---|---|
| conservative | enabled | same `min_profit_pct`, `min_bid_depth` as sell-side |
| moderate | enabled | lighter filters |
| aggressive | enabled | minimal filters |

Signal frequency lower than sell-side (ask-side mispricings are rarer), but identical edge quality when triggered.

---

## Strategy 2: Monotone Constraint Arb

**Signal:** Stacked threshold markets (e.g. "S&P above 5000 / 5100 / 5200") where price ordering is violated — `price(above 5100) > price(above 5000)`.

**Edge:** Sell the overpriced leg, buy the underpriced leg. Profit locked regardless of outcome. Zero directional risk.

**Implementation:**
- `EventDiscovery.poll_loop` — regex pass over event titles to detect threshold families, register in a new `monotone_families` registry
- `ArbEngine.evaluate_monotone()` — pairwise comparison across family members, signal fires when ordering violation exceeds `min_monotone_pair_profit_pct` after fees
- Execution: two-leg batch order (one buy, one sell) via existing batch order path

**Discovery complexity:** Family grouping is the main work — parse event titles for shared underlying + incrementing thresholds. Kalshi naming is consistent enough for regex.

**Risk calibration:**
| Profile | Status | Notes |
|---|---|---|
| conservative | enabled | strict `min_monotone_pair_profit_pct`, `min_bid_depth` |
| moderate | enabled | looser thresholds, wider family detection |
| aggressive | enabled | minimal filters, detect weekly/daily series cross-family |

---

## Strategy 3: Near-Expiry Stale Order Harvesting

**Signal:** Within `near_expiry_window_minutes` of event close, run the existing taker arb signal with relaxed filters. Stale resting limit orders that haven't been cancelled are the target.

**Edge:** Near expiry, prices converge to 0 or 100. Orders placed hours ago at stale prices become free money for a taker. The time gate substitutes for the filters that protect regular trading.

**Implementation:**
- `near_expiry_window_minutes` added to risk profile
- `Dispatcher.process_orderbook_update` checks `time_to_close` against window before routing
- `ArbEngine.evaluate_near_expiry()` — same logic as `evaluate()` but uses relaxed thresholds from a near-expiry sub-profile
- Close-time data already available in `market_metadata` from discovery

**Risk calibration:**
| Profile | `near_expiry_window_minutes` | Filters |
|---|---|---|
| conservative | 30 | `min_profit_pct` still required |
| moderate | 60 | lighter depth/volume filters |
| aggressive | 120 | minimal filters |

---

## Strategy 4: Two-Sided Market Making

**Signal:** A market is eligible when `spread_width >= min_spread_cents` and both sides have sufficient depth.

**Edge:** Post bid at `best_bid + 1¢` and ask at `best_ask - 1¢`. When both fill, earn the spread at 0% maker fee. Inventory risk between fills.

**Inventory risk management:**
- On one-side fill: hold inventory, wait for other side up to `two_sided_timeout_secs`
- On timeout: cancel open leg, unwind filled leg via existing tiered unwind logic (Phase 1 tight limit → Phase 2 wider → Phase 3 market)
- `max_two_sided_inventory` caps total contracts held across all active two-sided positions

**Implementation:**
- `ArbEngine.evaluate_two_sided()` — new signal method
- `MakerManager` extended to track paired bid/ask order IDs per market, add timeout/unwind logic on partial fill
- Alternatively: new `TwoSidedManager` class if separation is cleaner

**Risk calibration:**
| Profile | `min_spread_cents` | `max_two_sided_inventory` | `two_sided_timeout_secs` | `min_volume_24h` |
|---|---|---|---|---|
| conservative | 6 | 10 | 120 | 50 |
| moderate | 4 | 25 | 180 | 10 |
| aggressive | 2 | 50 | 300 | 0 |

---

## Implementation Order

1. Buy-side structural arb — minimal, highest confidence, reuses existing path
2. Monotone constraint arb — discovery layer work, zero execution risk
3. Near-expiry stale order harvesting — dispatcher routing + relaxed sub-profile
4. Two-sided market making — most implementation work, new inventory management

---

## Open Questions

- Should monotone family detection run at startup scan only, or re-evaluate on each poll cycle?
- Two-sided manager: extend `MakerManager` or new class? Depends on how much the fill/unwind logic diverges.
- Near-expiry window: should it disable the maker layer (don't post new limit orders near expiry) or run both in parallel?
