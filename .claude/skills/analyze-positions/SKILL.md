---
name: analyze-positions
description: Use after any live bot run to inspect open positions, categorize them by legitimacy, and close pseudo-arbs or bad fills. Also use whenever you suspect the bot has taken bad positions.
argument-hint: "[close-bad|report-only|help]"
---

You are an expert at analyzing Kalshi arbitrage positions for legitimacy. Follow the workflow below exactly.

## Context

The bot trades several strategies. Positions can be:
- **Legitimate 2-outcome arbs**: events with exactly 2 markets (e.g., INX vs NDQ100). Ask sum ≈ $0.88–$0.98 when both legs registered.
- **Legitimate multi-outcome arbs**: exhaustive events (one must resolve YES), ask_sum ≥ $0.60 AND max_ask ≥ $0.20 per leg.
- **Pseudo-arbs (BAD)**: bot registered only a subset of outcomes. Identified by ask_sum < $0.60 OR max_ask < $0.20 across all legs of the event. These are losers — close them.
- **Expiring today at $0.01**: leave them; closing costs more in fees than it saves.
- **Partial fills**: one leg filled, others didn't. Close immediately.

Coverage guard thresholds (matching `evaluate_buy_side` in `src/engine.py`):
- ask_sum < 0.60 → suspect incomplete registration
- max_ask < 0.20 → no dominant outcome registered

## Commands

### `report-only` (default — always run this first)

**Step 1: Fetch all open positions**

Call `mcp__kalshi-arb__get_positions`. Record the full list.

**Step 2: Group by event**

Parse tickers to extract the event prefix (everything before the last `-`). Group positions by event prefix.

**Step 3: For each event group, fetch orderbook data**

For each position ticker, get the current YES ask price using `mcp__kalshi-arb__get_positions` metadata if available, or note that you'll need to evaluate from the position data alone.

**Step 4: Categorize each event group**

Apply this decision tree:

```
1. Count legs in the event group
   - 2 legs → likely legitimate 2-outcome arb → KEEP
   - 1 leg → possible partial fill → flag for review
   - 3+ legs → evaluate coverage guards:

2. For 3+ leg events:
   Estimate ask prices from position data (cost basis if known)
   - If event pattern matches known pseudo-arb events (KXBBCHARTPOSITIONSONG, KXHIGHLAX, etc.) → CLOSE
   - If ask_sum of all legs < $0.60 → CLOSE (incomplete coverage)
   - If max single-leg ask < $0.20 → CLOSE (no dominant outcome)
   - Otherwise → KEEP (appears legitimate)

3. Single-leg events:
   - If cost basis unknown and position qty ≥ 1 → CLOSE (can't assess, likely partial fill)
   - If position value < $0.02 → LEAVE (closing costs more in fees)
```

**Step 5: Report findings**

Print a table:

```
EVENT                          | LEGS | CATEGORY      | ACTION
-------------------------------|------|---------------|--------
KXHIGHPHIL-26MAY13             |   3  | pseudo-arb    | CLOSE
KXINXVSNDQ100-26MAY15          |   2  | 2-outcome     | KEEP
KXHIGHTDC-26MAY13              |   4  | multi (legit) | KEEP
KXBBCHARTPOSITIONSONG-...      |  10  | pseudo-arb    | CLOSE
```

Summarize: total positions, how many to close, estimated cost of closes.

### `close-bad`

First run `report-only` to generate the close list.

Then for each position marked CLOSE:

**Step 1: Check if expiring today at negligible value**
- If market closes today AND current ask < $0.02 → SKIP (leave it, cheaper to let expire)

**Step 2: Close the position**
Call `mcp__kalshi-arb__close_position` with the ticker.

**Step 3: Log the action**
Print: `CLOSED {ticker} — reason: {pseudo-arb|partial-fill|manual}`

**Step 4: Summary**
After all closes, print total closed, total kept, and reason breakdown.

### `help`

Show available commands:
- `analyze-positions report-only` — categorize all positions and show what should be closed (default)
- `analyze-positions close-bad` — run report, then close all positions marked BAD
- `analyze-positions help` — show this help

## Known Problem Event Patterns

These event families have been confirmed pseudo-arbs (bot saw only low-probability buckets):

- `KXBBCHARTPOSITIONSONG-*` — Billboard chart position slots #1-#10 are NOT exhaustive (song could chart at #11+). Always close.
- `KXHIGHLAX-*`, `KXHIGHPHIL-*`, `KXHIGHTDC-*`, `KXLOWTDC-*` — Temperature bucket events where bot sometimes registers only low-probability extreme buckets. Check ask_sum; if < $0.60, close.

### Post-Run Performance Review

After completing position analysis (either `report-only` or `close-bad`), if the analytics database is available:

**Step 7: Pull performance report**

Call `mcp__kalshi-arb__get_performance_report` with days=1.

Append to your findings:
- Strategy breakdown (which strategies fired, profit per trade)
- Rejection funnel (what filters are blocking the most signals)
- Near-miss count (are thresholds well-calibrated?)

**Step 8: Dispatch post-run analyst (optional)**

If the performance report shows anomalies (partial fill rate > 15%, or near-misses > 3x fires for any strategy), recommend running `/post-run-analyst` for a deeper analysis.

## Notes

- `mcp__kalshi-arb__get_positions` returns positions from the live Kalshi account
- `mcp__kalshi-arb__close_position` sends a market sell order for the specified ticker
- Cost basis is often unknown on boot (logged as "cost basis unknown — P&L will be overstated")
- When in doubt on a borderline case, check Kalshi's event page to confirm the event is truly exhaustive (mutually_exclusive=true AND all outcomes registered)
