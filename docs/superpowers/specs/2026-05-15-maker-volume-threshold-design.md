# Separate Volume Threshold for Maker Strategy

**Date:** 2026-05-15
**Status:** Approved design, pending implementation

## Problem

The maker strategy (`evaluate_maker`) shares the same `_validate_legs()` call as the taker strategy, which applies `min_volume_24h` uniformly. Analytics from a 90-second live run show **37 distinct events** with bid_sum between $1.00 and $1.05 (maker-profitable) that are blocked by the conservative volume filter (`min_volume_24h=50`) before `evaluate_maker()` ever runs.

These events — political futures (TX Senate runoff, NFL Coach of Year, Tony Awards, governor races), sports futures, and long-dated markets — have real orderbook depth (8-272 contracts at best bid) but low 24h trading volume because they don't trade frequently.

The volume filter is appropriate for **taker** arbs, which need existing liquidity to fill immediately. But **maker** orders are limit orders that create liquidity — they sit on the book and wait. Low 24h volume is not a risk factor for makers the way it is for takers.

## Change

Add a `maker_min_volume_24h` field to `RiskProfile` with a lower default than `min_volume_24h`. Pass it to `_validate_legs()` when called from `evaluate_maker()`.

## Design

### RiskProfile

Add one field:

```python
maker_min_volume_24h: float = 0.0  # 0 = no volume filter for maker
```

Preset values:
- **conservative:** `maker_min_volume_24h: 10.0` (lower than taker's 50, still filters truly dead markets)
- **moderate:** `maker_min_volume_24h: 0.0` (no volume filter)
- **aggressive:** `maker_min_volume_24h: 0.0` (no volume filter)

### ArbEngine

`evaluate_maker()` already calls `_validate_legs(orderbooks, market_metadata, event_ticker=event_ticker)` without overriding `min_volume_24h`. Change to:

```python
legs = self._validate_legs(
    orderbooks, market_metadata, event_ticker=event_ticker,
    min_volume_24h=self.maker_min_volume_24h,
)
```

Store the new field in `__init__`:
```python
self.maker_min_volume_24h = risk_profile.maker_min_volume_24h
```

No other code changes needed — `_validate_legs()` already accepts `min_volume_24h` as an optional override (it's used by `evaluate_near_expiry()` the same way).

### Config

Add to `config.example.yaml` under the maker section:
```yaml
# maker_min_volume_24h: 10.0   # Volume floor for maker (lower than taker since makers create liquidity)
```

Add `"maker_min_volume_24h"` to the `override_keys` set in `config.py`.

## Files Changed

| File | Change |
|------|--------|
| `src/risk.py` | Add `maker_min_volume_24h` field to `RiskProfile`, add to all 3 presets |
| `src/engine.py` | Store `maker_min_volume_24h` in `__init__`, pass to `_validate_legs()` in `evaluate_maker()` |
| `src/config.py` | Add `maker_min_volume_24h` to `override_keys` |
| `config.example.yaml` | Add commented-out `maker_min_volume_24h` line |
| `tests/test_engine.py` | Test that maker evaluates with lower volume threshold |
| `tests/test_risk.py` | Test preset values for `maker_min_volume_24h` |

## Validation

After implementation, run a live test and verify:
- `get_signal_history strategy=maker outcome=near_miss` shows events that were previously volume-blocked
- `get_performance_report` shows maker signals on previously-blocked events
- No regression in taker signal quality (taker still uses the original `min_volume_24h`)
