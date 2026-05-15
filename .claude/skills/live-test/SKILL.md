---
name: live-test
description: Use when you want to run a timed live test of the arb bot, monitor it in real time, and analyze the results. Covers startup, monitoring, shutdown, and log analysis.
argument-hint: "[duration=90] [help]"
---

You are an expert at running and analyzing live tests of the Kalshi arb bot. Follow the workflow below exactly.

**CRITICAL: After EVERY live test, you MUST run the `analyze-positions` skill. This is not optional. Live tests can create positions. Do not skip this step.**

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

Key log patterns to watch for:

| Pattern | Meaning |
|---------|---------|
| `STATUS \|` | Periodic health summary — note events, arbs_detected, maker_horizon counts |
| `arb_detected` | Taker arb fired — note event ticker and profit |
| `buy_side` | Buy-side arb fired |
| `near_expiry` | Near-expiry taker arb fired |
| `monotone_arb_detected` | Monotone pair arb fired |
| `coverage-filtered` | Buy-side rejected by two-guard filter (expected behavior) |
| `near-miss` | Profitable but filtered — useful for tuning |
| `maker horizon-filtered` | Maker arb rejected by horizon limit |
| `ERROR` / `Exception` | Bug or API error — note and investigate |
| `circuit_breaker` | Check if `circuit_breaker=ok` or triggered |
| `WebSocket` | Disconnects or reconnects |

### Step 4: Stop the bot

After the test duration:
```bash
if [ -f /tmp/kalshi-arb.pid ]; then
    kill $(cat /tmp/kalshi-arb.pid)
else
    pkill -f "python3 -m src.main" || true
fi
sleep 2
```

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

**5c. Check near-miss signals**
```bash
grep "near-miss" /tmp/live_test.log | tail -20
```
Near-misses close to $1.00 (bid_sum ≥ 0.97 for taker, ≥ 0.95 for maker) indicate markets approaching profitability.

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

**Do not skip this step, even if you found bugs or errors in Step 5.** Bug investigation comes AFTER position cleanup. Pseudo-arb positions left open become realized losses.

### Step 7: Write up findings

**Confirm Step 6 is complete before writing findings.** If you have not yet run `/analyze-positions close-bad`, do it now before proceeding.

Summarize:
1. Test duration and events monitored
2. Arbs detected vs executed vs failed
3. Any errors or unexpected behavior
4. Coverage filter performance (how many events it blocked)
5. Positions found and what was closed
6. Recommended next steps (tuning thresholds, fixing bugs, etc.)

## Commands

### `[duration=90]` (default)

Run the full workflow above for the specified duration in seconds. Examples:
- `/live-test` — 90-second test
- `/live-test 120` — 2-minute test
- `/live-test 300` — 5-minute test

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
