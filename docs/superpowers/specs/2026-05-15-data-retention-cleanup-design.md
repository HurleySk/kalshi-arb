# Data Retention & Automatic Cleanup

## Problem

The bot generates ~1 GB/hour of SQLite data (orderbook snapshots + signal evaluations) and ~100 MB/hour of logs with no rotation or retention policy. After a few hours of runtime the DB hit 4.5 GB and logs reached 580 MB. Without cleanup, a full day would produce ~20 GB of DB data alone.

## Decisions

- **Retention model:** Session-based with a storage cap (5 GB default). Oldest sessions are purged first until DB size is under the cap, but at least 1 session is always preserved.
- **What gets purged:** Only `orderbook_snapshots` and `signal_evaluations`. Executions, fills, balances, and session metadata are never purged (tiny rows representing real trading history).
- **Cleanup triggers:** On bot startup (catches growth from previous runs) + periodic during runtime (every 30 min default).
- **Log rotation:** `RotatingFileHandler` with 5 MB max per file, 5 backup files (25 MB total).
- **Approach:** Integrated into `DataRecorder` and `_setup_logging()`. No new modules.

## Config Changes

### `recording:` section (new fields)

```yaml
recording:
  enabled: true
  db_path: data/arb_history.db
  snapshot_interval_secs: 5
  balance_poll_interval_secs: 300
  retention_max_db_size_mb: 5000      # Purge oldest sessions when DB exceeds this
  retention_min_sessions: 1           # Always keep at least N sessions (even if over cap)
  cleanup_interval_secs: 1800         # Periodic cleanup interval (seconds)
```

### `logging:` section (new fields)

```yaml
logging:
  level: INFO
  file: logs/arb_bot.log
  max_file_size_mb: 5                 # Rotate at this size
  max_backup_count: 5                 # Keep this many rotated files
```

All new fields have defaults so existing configs work without changes.

## Implementation

### 1. DataRecorder.purge_old_sessions()

New method on `DataRecorder`:

```
purge_old_sessions(max_db_size_mb: int, min_sessions: int) -> dict
```

Logic:
1. Check DB file size via `os.path.getsize(db_path)`.
2. If under `max_db_size_mb`, return early.
3. Query `SELECT id, start_time FROM sessions ORDER BY start_time ASC`.
4. While file size > cap AND remaining sessions > `min_sessions`:
   a. Delete `orderbook_snapshots WHERE session_id = <oldest>`.
   b. Delete `signal_evaluations WHERE session_id = <oldest>`.
   c. Do NOT delete from executions, fills, balances, or sessions.
   d. Track rows deleted for logging.
5. After all deletions, run `VACUUM` once to reclaim disk space.
6. Return summary dict: `{sessions_purged: [...], obs_deleted: N, sig_deleted: N, before_mb: X, after_mb: Y}`.
7. Log the summary at INFO level.

Note: `VACUUM` requires rebuilding the entire DB and temporarily doubles disk usage. For a 5 GB DB this is acceptable. If the cap were much larger, we'd use `PRAGMA incremental_vacuum` instead.

### 2. Cleanup triggers

**On startup:** `start_session()` calls `purge_old_sessions()` before creating the new session row. This ensures the DB is trimmed before recording begins.

**Periodic:** New `async cleanup_loop(interval_secs, max_db_size_mb, min_sessions)` coroutine added to `DataRecorder`. The bot's `run()` method spawns it as a task alongside existing loops. It sleeps for `interval_secs`, then calls `purge_old_sessions()`.

### 3. Log rotation

In `ArbBot._setup_logging()`, replace:
```python
handler = logging.FileHandler(self.cfg.log_file)
```
with:
```python
handler = logging.handlers.RotatingFileHandler(
    self.cfg.log_file,
    maxBytes=self.cfg.log_max_file_size_mb * 1024 * 1024,
    backupCount=self.cfg.log_max_backup_count,
)
```

Produces files: `arb_bot.log`, `arb_bot.log.1`, ..., `arb_bot.log.5`.

### 4. Config model changes

Add to the config dataclass (wherever bot config is parsed):
- `retention_max_db_size_mb: int = 5000`
- `retention_min_sessions: int = 1`
- `cleanup_interval_secs: int = 1800`
- `log_max_file_size_mb: int = 5`
- `log_max_backup_count: int = 5`

### 5. One-time historical cleanup

Before deploying the new code:
1. Run `python3 -m src.analytics` on the existing DB to extract summary stats.
2. Delete `data/arb_history.db` (all data is from today, 5 sessions).
3. Delete `logs/arb_bot.log` (will be recreated on next run with rotation).

## Files Changed

| File | Change |
|------|--------|
| `src/recorder.py` | Add `purge_old_sessions()` method, add `cleanup_loop()` async method |
| `src/main.py` | Switch to `RotatingFileHandler`, parse new config fields, spawn cleanup loop |
| `src/config.py` | Add new fields to `Config` dataclass, parse from `recording:` and `logging:` sections |
| `config.example.yaml` | Document new retention and log rotation fields |
| `config.yaml` | Add retention config (or rely on defaults) |

## What Does NOT Change

- Recording cadence (snapshot_interval_secs, balance_poll_interval_secs)
- All `record_*` methods in DataRecorder
- Replay engine (works on whatever sessions remain)
- Analytics module
- Executions, fills, balances tables (never purged)
- Session table rows (kept for referential integrity with preserved trade data)

## Testing

- Unit test: `purge_old_sessions` with a small in-memory DB, verify only snapshots/signals are deleted and executions/fills/balances survive.
- Unit test: verify cleanup skips when DB is under cap.
- Unit test: verify `min_sessions` floor is respected even when over cap.
- Integration: run bot, verify log rotation produces expected files.
