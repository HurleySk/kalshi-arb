---
name: live-test
description: Use when you want to run a timed live test of the arb bot, monitor it in real time, and analyze the results. Covers startup, monitoring, shutdown, and log analysis.
argument-hint: "[duration=90] [max_loss=0.10] [max_errors=3] [help]"
---

You are an expert at running and analyzing live tests of the Kalshi arb bot. Follow the workflow below exactly.

**CRITICAL: After EVERY live test, you MUST run the `analyze-positions` skill. This is not optional. Live tests can create positions. Do not skip this step.**

## Intervention Parameters

These parameters control when you MUST stop the bot early, kill it, unwind positions, and begin analysis. They can be overridden by the user on invocation.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_loss` | `$0.10` | Cumulative realized + unrealized loss. Stop immediately if exceeded. |
| `max_errors` | `3` | Consecutive ERROR-level log lines (not warnings). Stop and investigate. |
| `max_partial_fills` | `2` | Total partial fill events during the test. Stop — likely liquidity problem. |
| `max_unwind_time` | `60` | Seconds any single unwind is running. If exceeded, kill bot and manually close. |
| `circuit_breaker` | `stop` | Action when circuit breaker trips: `stop` (kill bot) or `continue` (let bot handle it). |

**Override syntax:** `/live-test 300 max_loss=0.50 max_errors=5`

### Intervention Decision Tree

During monitoring (Step 3), evaluate EVERY log line against these rules in priority order:

```
1. circuit_breaker=triggered → action per circuit_breaker param (default: stop)
2. UNWIND TIMEOUT or unwind running > max_unwind_time → STOP IMMEDIATELY
3. Cumulative loss > max_loss → STOP IMMEDIATELY
4. Consecutive errors >= max_errors → STOP AND INVESTIGATE
5. Partial fill count >= max_partial_fills → STOP AND INVESTIGATE
6. Python traceback / unhandled exception → STOP AND INVESTIGATE
7. Bot process exited unexpectedly → BEGIN ANALYSIS (skip Step 4)
```

**STOP IMMEDIATELY** means:
1. Kill the bot process (`kill -TERM` first, `kill -9` after 5s if still alive)
2. Wait 2 seconds for graceful shutdown
3. Run `analyze-positions close-bad` to unwind any open positions
4. THEN begin log analysis and investigation
5. Report what triggered the intervention

**STOP AND INVESTIGATE** means:
1. Kill the bot process
2. Run `analyze-positions close-bad` to unwind any open positions  
3. Grep the log for the specific error pattern
4. Diagnose root cause before reporting findings
5. Include the diagnosis and proposed fix in your report

### Loss Tracking During Monitoring

Track cumulative loss in real time by watching for these log patterns:

| Pattern | Loss Contribution |
|---------|-------------------|
| `arb_detected` / `buy_side_arb` with execution | Check `net_profit` field — negative = loss |
| `PARTIAL_FILL` | Assume worst-case: sum of filled leg prices as loss until unwind completes |
| `UNWIND` result | Actual unwind loss (replaces worst-case estimate) |
| `circuit_breaker=triggered` | Check balance change from last STATUS line |

If you cannot determine exact loss from logs, use the MCP tool `mcp__kalshi-arb__get_positions` to check current exposure and estimate unrealized P&L.

## Context

- Bot entry point: `python3 -m src.main`
- Config: `config.yaml` (must be set to `mode: live` for live testing)
- Pidfile: `/tmp/kalshi-arb.pid` (prevents duplicate instances)
- Log file: `/tmp/live_test.log`
- Default test duration: 90 seconds

## Workflow

### Step 1: Pre-flight checks

**1a. Kill any running instance**
```bash
if [ -f /tmp/kalshi-arb.pid ]; then
    PID=$(cat /tmp/kalshi-arb.pid)
    kill $PID 2>/dev/null || true
    sleep 2
    rm -f /tmp/kalshi-arb.pid
fi
# Belt-and-suspenders
pkill -f "python3 -m src.main" 2>/dev/null || true
sleep 1
```

**1b. Verify no stale process**
```bash
pgrep -f "src.main" && echo "WARNING: process still running" || echo "Clear"
```

**1c. Confirm config is set to live mode**
```bash
grep "^mode:" config.yaml
```
If not `mode: live`, do not proceed. Alert the user.

**1d. Clear old log**
```bash
> /tmp/live_test.log
```

### Step 2: Start the bot

```bash
python3 -m src.main > /tmp/live_test.log 2>&1 &
echo "Bot PID: $!"
```

Wait 5 seconds, then verify it started:
```bash
tail -5 /tmp/live_test.log
```
Look for `Starting Kalshi Arb Bot in LIVE mode`. If you see `Another instance is already running` or a Python traceback, abort and diagnose.

### Step 3: Monitor in real time

Use the Monitor tool on `/tmp/live_test.log` for the test duration (default 90 seconds).

**While monitoring, actively track intervention state:**
- `cumulative_loss = 0.0` — updated on every execution/unwind result
- `consecutive_errors = 0` — reset on any non-error log line
- `partial_fill_count = 0` — incremented on each PARTIAL_FILL event
- `unwind_start_time = None` — set when unwind begins, cleared when it ends

**On EVERY log line, run the Intervention Decision Tree (see above).** If any threshold is breached, abort monitoring immediately and follow the STOP procedure. Do not wait for the next STATUS line or the test duration to expire.

Key log patterns to watch for:

| Pattern | Meaning | Intervention? |
|---------|---------|---------------|
| `STATUS \|` | Periodic health summary — note events, arbs_detected, maker_horizon counts | Check balance for loss tracking |
| `arb_detected` | Taker arb fired — note event ticker and profit | Track profit/loss |
| `buy_side` | Buy-side arb fired | Track profit/loss |
| `near_expiry` | Near-expiry taker arb fired | Track profit/loss |
| `monotone_arb_detected` | Monotone pair arb fired | Track profit/loss |
| `PARTIAL_FILL` | Partial fill occurred | Increment partial_fill_count |
| `UNWIND` | Unwind started/completed | Track unwind duration |
| `UNWIND TIMEOUT` | Unwind process exceeded max time | **STOP IMMEDIATELY** |
| `circuit_breaker=triggered` | Circuit breaker tripped | **STOP per param** |
| `ERROR` / `Exception` | Bug or API error | Increment consecutive_errors |
| `Traceback` | Unhandled Python exception | **STOP AND INVESTIGATE** |
| `coverage-filtered` | Buy-side rejected by two-guard filter (expected behavior) | No |
| `near-miss` | Profitable but filtered — useful for tuning | No |
| `maker horizon-filtered` | Maker arb rejected by horizon limit | No |
| `WebSocket` | Disconnects or reconnects | No (unless repeated >3x in 60s) |
| `stale orderbook` | Orderbook data >5s old for a market — signal evaluation skipped | No |
| `timed out` | An API call exceeded its timeout — check connectivity | Increment consecutive_errors |

### Step 4: Stop the bot

Whether the test duration expired normally or an intervention threshold was breached:

**Normal stop (duration expired):**
```bash
if [ -f /tmp/kalshi-arb.pid ]; then
    kill $(cat /tmp/kalshi-arb.pid)
else
    pkill -f "python3 -m src.main" || true
fi
sleep 2
```

**Intervention stop (threshold breached):**
```bash
# SIGTERM first for graceful shutdown
if [ -f /tmp/kalshi-arb.pid ]; then
    kill $(cat /tmp/kalshi-arb.pid)
else
    pkill -f "python3 -m src.main" || true
fi
sleep 5
# If still alive, force kill
pgrep -f "src.main" && kill -9 $(pgrep -f "src.main") || true
sleep 1
```

After an intervention stop, note the trigger reason (which threshold was breached, the exact log line) before proceeding to Step 5.

### Step 5: Analyze the log

**5a. Extract STATUS lines**
```bash
grep "STATUS |" /tmp/live_test.log | tail -5
```
From the final STATUS line, note:
- `events=N` — how many events were being monitored
- `arbs_detected=N` — how many arb signals fired
- `arbs_executed=N` — how many actually traded
- `arbs_failed=N` — how many failed (API errors, race conditions)
- `maker_horizon=N` — events closing within maker horizon right now

**5b. Check for coverage-filtered events**
```bash
grep "coverage-filtered" /tmp/live_test.log | sort | uniq -c | sort -rn | head -20
```
High coverage-filtered counts are healthy — means the guards are working. Review any event that fired an arb to ensure it wasn't a false positive.

**5c. Check near-miss signals (from analytics DB)**

Call `mcp__kalshi-arb__get_near_misses` with days=1. This is more reliable than grep since the recorder captures all near-misses regardless of log level.

Also call `mcp__kalshi-arb__get_signal_history` with outcome="near_miss" and limit=10 to see the highest-value misses.

Near-misses close to $1.00 (bid_sum >= 0.97 for taker, >= 0.95 for maker) indicate markets approaching profitability.

**5d. Check for errors**
```bash
grep -E "ERROR|Exception|Traceback|circuit_breaker=triggered" /tmp/live_test.log
```

**5e. Check arb signals that fired**
```bash
grep -E "arb_detected|buy_side_arb|near_expiry|monotone_arb" /tmp/live_test.log
```
For each signal that fired, verify:
- Was it a genuine arb (bid_sum/ask_sum makes sense)?
- Did it execute successfully (`arbs_executed` incremented)?

### Step 6: MANDATORY — Analyze open positions

**Run the `analyze-positions` skill now.** Always. Every single live test creates or inherits positions that must be reviewed.

```
/analyze-positions close-bad
```

If the bot ran for more than 60 seconds and analytics recording is enabled, also run `/post-run-analyst` for a detailed performance assessment with tuning recommendations.

**Do not skip this step, even if you found bugs or errors in Step 5.** Bug investigation comes AFTER position cleanup. Pseudo-arb positions left open become realized losses.

### Step 7: Write up findings

**Confirm Step 6 is complete before writing findings.** If you have not yet run `/analyze-positions close-bad`, do it now before proceeding.

Summarize:
1. **Test outcome**: completed normally OR intervention triggered (which threshold, exact log line)
2. Test duration (actual, not requested if stopped early) and events monitored
3. Arbs detected vs executed vs failed
4. **Loss tracking**: cumulative realized loss, any unrealized exposure, positions unwound
5. **Partial fills**: count, which events, unwind outcomes
6. Any errors or unexpected behavior
7. Coverage filter performance (how many events it blocked)
8. Positions found and what was closed
9. Recommended next steps (tuning thresholds, fixing bugs, etc.)

If the test was stopped by intervention, the findings report MUST lead with:
```
⚠️ INTERVENTION: Test stopped early after {duration}s
   Trigger: {parameter} breached ({actual_value} > {threshold})
   Log line: {exact line that triggered the stop}
```

## Commands

### `[duration=90] [param=value ...]` (default)

Run the full workflow above for the specified duration in seconds, with optional intervention parameter overrides. Examples:
- `/live-test` — 90-second test, default thresholds
- `/live-test 120` — 2-minute test
- `/live-test 300 max_loss=0.50` — 5-minute test, tolerate up to $0.50 loss
- `/live-test 3600 max_loss=1.00 max_errors=5 max_partial_fills=3` — 1-hour soak test with relaxed thresholds
- `/live-test 120 circuit_breaker=continue` — let the bot handle circuit breaker events internally

### `help`

Show this help.

## Common Issues

**Bot won't start: "Another instance is already running"**
→ Stale pidfile or genuine duplicate. Run Step 1a again and verify `pgrep`.

**Log is empty after 5 seconds**
→ Check `python3 -m src.main` exits immediately. Likely config error or import failure. Run manually: `python3 -m src.main 2>&1 | head -20`.

**arbs_detected > 0 but arbs_executed = 0**
→ Orders getting rejected. Check for `arbs_failed` count and grep errors for API rejection messages.

**WebSocket disconnects frequently**
→ Network instability. Bot auto-reconnects; check if events count drops or recovers in STATUS lines.

**coverage-filtered count is 0**
→ Debug logging may be off. Check `config.yaml` for `logging.level: DEBUG`. The two-guard filter only logs at DEBUG level.

**Boot reconciliation failed: "too_many_orders_in_batch"**
→ `api.py::batch_cancel_orders` received more than 20 order IDs at once. Fixed in the current codebase (chunks in batches of 20). If this reappears, check that the chunking logic in `api.py` is still in place.
