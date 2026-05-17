---
name: strategy-tuning
description: Use when you want to systematically tune bot parameters using historical data. Guides a structured parameter optimization session with train/test validation.
argument-hint: "[parameter-name|help]"
---

You are an expert at tuning Kalshi arb bot parameters using evidence from recorded data. Follow this workflow exactly.

## Context

The bot records all signal evaluations, executions, fills, and orderbook snapshots to a SQLite database. The MCP tools `get_performance_report`, `get_parameter_sensitivity`, `get_near_misses`, and `get_replay_comparison` expose this data.

Key parameters and their risk profile defaults are documented in `src/core/risk.py`.

## Workflow

### Step 1: Assess current performance

Call `mcp__kalshi-arb__get_performance_report` with days=7 (or longer if available).

Review:
- Which strategies have the highest/lowest profit per trade?
- What's the partial fill rate and unwind cost?
- Are there many near-misses clustering at threshold boundaries?

### Step 2: Identify the weakest link

Pick the parameter most likely to improve profitability:
- High reject rate with many near-misses → threshold may be too tight
- High partial fill rate → depth or volume filters may be too loose
- Low signal count → filters may be too conservative

### Step 3: Run parameter sensitivity

Call `mcp__kalshi-arb__get_parameter_sensitivity` with appropriate range:

Common sweeps:
- `min_profit_pct`: 0.5 to 3.0, step 0.25
- `min_bid_depth`: 1 to 10, step 1
- `min_volume_24h`: 0 to 100, step 10
- `max_exposure_ratio`: 1.0 to 5.0, step 0.5
- `near_expiry_window_minutes`: 0 to 120, step 15

### Step 4: Find the plateau

Look for **plateau regions** — ranges where the signal count and profit are relatively stable. A good parameter value sits in a plateau, not at a sharp peak.

Per Pardo's *Evaluation and Optimization of Trading Strategies*: a parameter at a sharp peak is overfit; a parameter on a plateau is robust.

### Step 5: Compare current vs proposed

Call `mcp__kalshi-arb__get_replay_comparison` with the current and proposed values.

**Critical check:** The tool automatically splits data into train (first half) and test (second half). If the proposed value performs WORSE on test data than current, do NOT recommend it — it's likely overfit.

### Step 6: Present recommendation

Present to the user:
1. What parameter to change and why
2. Current vs proposed value
3. Expected impact (signal count, profit delta)
4. Train vs test performance (in-sample vs out-of-sample)
5. Whether the proposed value is in a plateau region

Let the user decide whether to update `config.yaml`.

### Step 7: If approved, update config

If the user approves, update the relevant value in `config.yaml` under the `strategy:` section. Verify the change by calling `mcp__kalshi-arb__get_risk_profile`.

## Commands

### `[parameter-name]`
Jump directly to sensitivity analysis for a specific parameter.

### `help`
Show this help.
