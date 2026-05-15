You are an expert at running dry-run simulations of the Kalshi arb bot against recorded orderbook history.

## Context

The dry run replays recorded orderbook snapshots through the **real** ExecutionManager with a SimulatedAPI. It exercises fill dedup, partial fill handling, unwind logic, and position tracking — the same code paths as production — with configurable fault injection to surface race conditions.

## Commands

### `[preset]` (default)

Run the dry-run simulation. Presets:

| Preset | partial_fill_rate | ws_race_rate | Description |
|--------|-------------------|--------------|-------------|
| `clean` | 0.0 | 0.0 | No faults — baseline signal/profit check |
| `moderate` (default) | 0.1 | 0.3 | Moderate fault injection |
| `aggressive` | 0.3 | 1.0 | Stress test: high partial fills + all WS races |
| `race-only` | 0.0 | 1.0 | Only test WS fill dedup (the phantom short bug) |

### Custom overrides

Pass individual rates: `/dry-run --partial-fill-rate 0.5 --ws-race-rate 0.8`

## Workflow

### Step 1: Run the dry run

```bash
python3 -m src.dry_run --db data/arb_history.db \
    --partial-fill-rate {rate} --ws-race-rate {rate} --seed 42
```

For presets, map to the rates in the table above. Default risk mode is `conservative`.

### Step 2: Interpret results

The output looks like:

```
=== DRY RUN REPORT ===
Signals fired:      12
Executions:         12
Partial fills:      3
WS fills injected:  9
WS fills deduped:   9
Open positions:     18
Realized P&L:       $-0.1200
Session loss:       $0.1200

INVARIANTS: ALL PASSED
```

Key metrics to check:
- **WS fills deduped** should equal **WS fills injected** — if not, fills are double-counted (the phantom short bug)
- **Invariant violations** — any violation means a bug exists in the fill dedup or position tracking
- **Open positions** — sell-side arbs leave positions open (normal). Check that none have negative quantity.

### Step 3: Report findings

If invariants pass:
> Dry run completed: {signals} signals, {executions} executions, {ws_deduped}/{ws_injected} WS fills deduped. All invariants passed.

If invariants fail:
> **DRY RUN FAILED**: {count} invariant violations detected:
> - {violation details}
>
> These indicate bugs in the fill dedup or position tracking logic that must be fixed before live testing.

### Step 4: Recommend next steps

Based on results:
- All clean → safe for live test
- Invariant failures → investigate and fix before any live test
- High partial fill losses → consider tightening risk parameters

## Notes

- The dry run uses `TimeoutConfig` with near-zero values so it runs in <1 second
- The SimulatedAPI generates deterministic `sim-XXXXXX` order IDs (seeded RNG)
- No real API calls are made — safe to run anytime
- Results are printed to stdout, not written to the analytics DB
- Source: `src/dry_run.py`, `src/simulator.py`
