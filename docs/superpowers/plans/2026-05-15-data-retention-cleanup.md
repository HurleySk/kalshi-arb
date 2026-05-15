# Data Retention & Automatic Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic DB pruning (session-based with storage cap) and log rotation to prevent unbounded data growth.

**Architecture:** `DataRecorder` gains a `purge_old_sessions()` method that deletes orderbook snapshots and signal evaluations from oldest sessions when the DB exceeds a configurable size cap. A periodic async loop calls it every 30 minutes. Log rotation uses `RotatingFileHandler` instead of `FileHandler`. All config fields have defaults so existing setups work unchanged.

**Tech Stack:** Python stdlib (`logging.handlers`, `os.path.getsize`), SQLite (`VACUUM`), asyncio

---

### Task 1: Add retention config fields

**Files:**
- Modify: `src/config.py:17-41` (Config dataclass) and `src/config.py:79-102` (load_config)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for new config fields**

Add to `tests/test_config.py`:

```python
def test_retention_config_defaults(tmp_path):
    """Retention and log rotation config should have defaults when omitted."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: test
    private_key_path: /tmp/fake.pem
strategy:
  risk_mode: conservative
""")
    cfg = load_config(str(cfg_file))
    assert cfg.retention_max_db_size_mb == 5000
    assert cfg.retention_min_sessions == 1
    assert cfg.cleanup_interval_secs == 1800
    assert cfg.log_max_file_size_mb == 5
    assert cfg.log_max_backup_count == 5


def test_retention_config_custom(tmp_path):
    """Retention config should read custom values from yaml."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
mode: demo
credentials:
  demo:
    api_key_id: test
    private_key_path: /tmp/fake.pem
strategy:
  risk_mode: conservative
recording:
  retention_max_db_size_mb: 2000
  retention_min_sessions: 3
  cleanup_interval_secs: 900
logging:
  max_file_size_mb: 10
  max_backup_count: 3
""")
    cfg = load_config(str(cfg_file))
    assert cfg.retention_max_db_size_mb == 2000
    assert cfg.retention_min_sessions == 3
    assert cfg.cleanup_interval_secs == 900
    assert cfg.log_max_file_size_mb == 10
    assert cfg.log_max_backup_count == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py::test_retention_config_defaults tests/test_config.py::test_retention_config_custom -v`
Expected: FAIL — `Config` has no attribute `retention_max_db_size_mb`

- [ ] **Step 3: Add fields to Config dataclass and load_config**

In `src/config.py`, add five fields to the `Config` dataclass after `recording_balance_poll_interval_secs`:

```python
    retention_max_db_size_mb: int
    retention_min_sessions: int
    cleanup_interval_secs: int
    log_max_file_size_mb: int
    log_max_backup_count: int
```

In `load_config()`, add to the `return Config(...)` block after the existing `recording_balance_poll_interval_secs` line:

```python
        retention_max_db_size_mb=int(recording_cfg.get("retention_max_db_size_mb", 5000)),
        retention_min_sessions=int(recording_cfg.get("retention_min_sessions", 1)),
        cleanup_interval_secs=int(recording_cfg.get("cleanup_interval_secs", 1800)),
        log_max_file_size_mb=int(logging_cfg.get("max_file_size_mb", 5)),
        log_max_backup_count=int(logging_cfg.get("max_backup_count", 5)),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add retention and log rotation config fields"
```

---

### Task 2: Implement purge_old_sessions in DataRecorder

**Files:**
- Modify: `src/recorder.py:116-123` (store db_path), add `purge_old_sessions()` method
- Test: `tests/test_recorder.py`

- [ ] **Step 1: Write failing tests for purge_old_sessions**

Add to `tests/test_recorder.py`:

```python
import os


def _populate_sessions(recorder, num_sessions, obs_per_session=10, sig_per_session=5):
    """Helper: create multiple sessions with snapshots, signals, executions, and fills."""
    for i in range(num_sessions):
        sid = recorder.start_session({"session": i})
        for j in range(obs_per_session):
            recorder.record_orderbook_snapshot(
                event_ticker=f"EVT-{i}",
                market_ticker=f"MKT-{i}-{j}",
                yes_bids={55: 10.0},
                no_bids={45: 8.0},
            )
        for j in range(sig_per_session):
            recorder.record_signal(
                event_ticker=f"EVT-{i}", strategy="taker", outcome="skip",
                reject_reason="test", bid_sum=1.0, ask_sum=None,
                profit_pct=0.0, exposure_ratio=0.0, legs=None, metadata=None,
            )
        recorder.record_execution(
            event_ticker=f"EVT-{i}", strategy="taker",
            legs=[{"ticker": f"MKT-{i}", "price": 0.55}],
            result="full_fill", fill_details=None, unwind_cost=0.0,
        )
        recorder.record_fill(
            ticker=f"MKT-{i}", side="yes", action="sell",
            price=0.55, quantity=1, realized_pnl=0.01,
        )
        recorder.record_balance(cash_cents=10000, portfolio_cents=10500)
        recorder.end_session()


def test_purge_skips_when_under_cap(recorder):
    """purge_old_sessions should no-op when DB is under the size cap."""
    recorder.start_session({})
    recorder.record_orderbook_snapshot(
        event_ticker="EVT-A", market_ticker="MKT-1",
        yes_bids={55: 10.0}, no_bids={45: 8.0},
    )
    recorder.end_session()
    result = recorder.purge_old_sessions(max_db_size_mb=1000, min_sessions=1)
    assert result is None
    rows = recorder._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    assert rows == 1


def test_purge_deletes_oldest_session_snapshots_and_signals(tmp_path):
    """purge should delete snapshots and signals from oldest session first."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=10, sig_per_session=5)

    # Force purge by setting cap to 0 MB (always under)
    result = rec.purge_old_sessions(max_db_size_mb=0, min_sessions=1)
    assert result is not None
    assert len(result["sessions_purged"]) == 2  # purged 2, kept 1 (min_sessions)

    # Session 3's data survives
    obs = rec._conn.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    sig = rec._conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()[0]
    assert obs == 10
    assert sig == 5

    # Executions, fills, balances from ALL sessions survive
    execs = rec._conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    fills = rec._conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    bals = rec._conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]
    assert execs == 3
    assert fills == 3
    assert bals == 3

    rec.close()


def test_purge_respects_min_sessions(tmp_path):
    """purge should never delete below min_sessions even if over cap."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=10, sig_per_session=5)

    result = rec.purge_old_sessions(max_db_size_mb=0, min_sessions=2)
    assert result is not None
    assert len(result["sessions_purged"]) == 1  # only purged 1, kept 2

    sessions_with_obs = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(sessions_with_obs) == 2

    rec.close()


def test_purge_sessions_rows_preserved(tmp_path):
    """Session table rows should never be deleted (referential integrity with trade data)."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path)
    _populate_sessions(rec, num_sessions=3, obs_per_session=5, sig_per_session=3)

    rec.purge_old_sessions(max_db_size_mb=0, min_sessions=1)

    sessions = rec._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert sessions == 3  # all 3 session rows preserved

    rec.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_recorder.py::test_purge_skips_when_under_cap tests/test_recorder.py::test_purge_deletes_oldest_session_snapshots_and_signals tests/test_recorder.py::test_purge_respects_min_sessions tests/test_recorder.py::test_purge_sessions_rows_preserved -v`
Expected: FAIL — `DataRecorder` has no method `purge_old_sessions`

- [ ] **Step 3: Store db_path and implement purge_old_sessions**

In `src/recorder.py`, make two changes:

**a)** In `__init__`, store the db_path. Change:
```python
        if self._enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
```
to:
```python
        self._db_path: str | None = db_path if self._enabled else None

        if self._enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
```

**b)** Add `purge_old_sessions` method after `end_session()` (before the `record_*` methods):

```python
    def purge_old_sessions(self, max_db_size_mb: int, min_sessions: int) -> dict | None:
        """Delete snapshots and signals from oldest sessions until DB is under size cap.

        Returns summary dict if anything was purged, None otherwise.
        Executions, fills, balances, and session rows are never deleted.
        """
        if not self._enabled or self._conn is None or self._db_path is None:
            return None

        db_size_bytes = os.path.getsize(self._db_path)
        cap_bytes = max_db_size_mb * 1024 * 1024
        if db_size_bytes <= cap_bytes:
            return None

        before_mb = db_size_bytes / (1024 * 1024)

        sessions = self._conn.execute(
            "SELECT id FROM sessions ORDER BY start_time ASC"
        ).fetchall()
        total_sessions = len(sessions)

        purged_ids: list[int] = []
        total_obs_deleted = 0
        total_sig_deleted = 0

        for (sid,) in sessions:
            if total_sessions - len(purged_ids) <= min_sessions:
                break
            if os.path.getsize(self._db_path) <= cap_bytes:
                break

            obs_deleted = self._conn.execute(
                "DELETE FROM orderbook_snapshots WHERE session_id = ?", (sid,)
            ).rowcount
            sig_deleted = self._conn.execute(
                "DELETE FROM signal_evaluations WHERE session_id = ?", (sid,)
            ).rowcount
            self._conn.commit()

            purged_ids.append(sid)
            total_obs_deleted += obs_deleted
            total_sig_deleted += sig_deleted

        if not purged_ids:
            return None

        self._conn.execute("VACUUM")

        after_mb = os.path.getsize(self._db_path) / (1024 * 1024)

        summary = {
            "sessions_purged": purged_ids,
            "obs_deleted": total_obs_deleted,
            "sig_deleted": total_sig_deleted,
            "before_mb": round(before_mb, 1),
            "after_mb": round(after_mb, 1),
        }
        logger.info("DB cleanup: purged %d session(s) — %d snapshots, %d signals removed. "
                     "Size: %.1f MB → %.1f MB",
                     len(purged_ids), total_obs_deleted, total_sig_deleted,
                     before_mb, after_mb)
        return summary
```

Also add the missing imports at the top of `src/recorder.py`:
```python
import logging
import os
```

And add the logger:
```python
logger = logging.getLogger("kalshi-arb")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recorder.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/recorder.py tests/test_recorder.py
git commit -m "feat: add purge_old_sessions to DataRecorder"
```

---

### Task 3: Call purge on startup in start_session

**Files:**
- Modify: `src/recorder.py` — `start_session()` method
- Modify: `src/recorder.py` — `__init__()` to accept retention params
- Modify: `src/main.py:40-41` — pass retention config to DataRecorder
- Test: `tests/test_recorder.py`

- [ ] **Step 1: Write failing test for purge-on-startup**

Add to `tests/test_recorder.py`:

```python
def test_start_session_triggers_purge(tmp_path):
    """start_session should purge old data when DB exceeds cap."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path, max_db_size_mb=0, min_sessions=1)
    _populate_sessions(rec, num_sessions=2, obs_per_session=10, sig_per_session=5)

    # This start_session should trigger purge (cap=0 means always purge)
    rec.start_session({"session": "new"})

    # Only the newest _populate session + the just-started session remain with obs/signals
    obs_sessions = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(obs_sessions) == 1  # only session 2's snapshots survive

    rec.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_recorder.py::test_start_session_triggers_purge -v`
Expected: FAIL — `DataRecorder.__init__()` doesn't accept `max_db_size_mb`

- [ ] **Step 3: Add retention params to DataRecorder.__init__ and call purge in start_session**

In `src/recorder.py`, update `__init__` signature:

```python
    def __init__(
        self,
        db_path: str | None,
        max_db_size_mb: int = 5000,
        min_sessions: int = 1,
    ) -> None:
        self._enabled = db_path is not None
        self._session_id: int | None = None
        self._conn: sqlite3.Connection | None = None
        self._db_path: str | None = db_path if self._enabled else None
        self._max_db_size_mb = max_db_size_mb
        self._min_sessions = min_sessions

        if self._enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
```

Update `start_session` to call purge before creating the new session:

```python
    def start_session(self, config: dict) -> int | None:
        """Open a new recording session.  Returns session id, or None if disabled."""
        if not self._enabled:
            return None
        assert self._conn is not None
        self.purge_old_sessions(self._max_db_size_mb, self._min_sessions)
        cur = self._conn.execute(
            "INSERT INTO sessions (start_time, end_time, config_json) VALUES (?, NULL, ?)",
            (time.time(), json.dumps(config)),
        )
        self._conn.commit()
        self._session_id = cur.lastrowid
        return self._session_id
```

In `src/main.py`, update the DataRecorder construction (line ~40-41):

```python
        db_path = self.cfg.recording_db_path if self.cfg.recording_enabled else None
        self.recorder = DataRecorder(
            db_path,
            max_db_size_mb=self.cfg.retention_max_db_size_mb,
            min_sessions=self.cfg.retention_min_sessions,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recorder.py tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/recorder.py src/main.py
git commit -m "feat: trigger DB purge on session startup"
```

---

### Task 4: Add periodic cleanup loop

**Files:**
- Modify: `src/recorder.py` — add `async cleanup_loop()` method
- Modify: `src/main.py:487-489` — spawn cleanup loop task
- Test: `tests/test_recorder.py`

- [ ] **Step 1: Write failing test for cleanup_loop**

Add to `tests/test_recorder.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_cleanup_loop_calls_purge(tmp_path):
    """cleanup_loop should call purge_old_sessions periodically."""
    db_path = str(tmp_path / "test.db")
    rec = DataRecorder(db_path, max_db_size_mb=0, min_sessions=1)

    # Populate 2 sessions
    _populate_sessions(rec, num_sessions=2, obs_per_session=5, sig_per_session=3)

    # Run cleanup_loop with a very short interval, cancel after one cycle
    loop_task = asyncio.create_task(rec.cleanup_loop(interval_secs=0.01))
    await asyncio.sleep(0.1)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    # Should have purged oldest session's snapshots/signals
    obs_sessions = rec._conn.execute(
        "SELECT DISTINCT session_id FROM orderbook_snapshots"
    ).fetchall()
    assert len(obs_sessions) == 1

    rec.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_recorder.py::test_cleanup_loop_calls_purge -v`
Expected: FAIL — `DataRecorder` has no method `cleanup_loop`

- [ ] **Step 3: Add cleanup_loop method to DataRecorder**

Add after `purge_old_sessions()` in `src/recorder.py`:

```python
    async def cleanup_loop(self, interval_secs: int = 1800) -> None:
        """Periodically purge old sessions to keep DB under size cap."""
        while True:
            await asyncio.sleep(interval_secs)
            self.purge_old_sessions(self._max_db_size_mb, self._min_sessions)
```

Also add `import asyncio` at the top of `src/recorder.py`.

- [ ] **Step 4: Spawn cleanup loop in ArbBot.run()**

In `src/main.py`, in the `run()` method, add the cleanup loop task alongside the existing recording tasks. After line 489 (`tasks.append(asyncio.create_task(self._balance_loop()))`), add:

```python
            tasks.append(asyncio.create_task(
                self.recorder.cleanup_loop(self.cfg.cleanup_interval_secs)
            ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recorder.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/recorder.py src/main.py
git commit -m "feat: add periodic cleanup loop for DB retention"
```

---

### Task 5: Switch to RotatingFileHandler

**Files:**
- Modify: `src/main.py:129` — replace FileHandler with RotatingFileHandler

- [ ] **Step 1: Update _setup_logging in src/main.py**

Add `import logging.handlers` at the top of `src/main.py` (after `import logging`).

Replace the handler line in `_setup_logging`:

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

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `python3 -m pytest tests/test_main.py tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: switch to RotatingFileHandler for log rotation"
```

---

### Task 6: Update config.example.yaml

**Files:**
- Modify: `config.example.yaml`

- [ ] **Step 1: Add retention and log rotation fields to config.example.yaml**

In the `recording:` section, add after `balance_poll_interval_secs`:

```yaml
  retention_max_db_size_mb: 5000       # Purge oldest sessions when DB exceeds this (MB)
  retention_min_sessions: 1            # Always keep at least this many sessions
  cleanup_interval_secs: 1800          # How often to run cleanup during runtime (seconds)
```

In the `logging:` section, add after `file`:

```yaml
  max_file_size_mb: 5                  # Rotate log file at this size (MB)
  max_backup_count: 5                  # Keep this many rotated log files (total max: 25 MB)
```

- [ ] **Step 2: Commit**

```bash
git add config.example.yaml
git commit -m "docs: add retention and log rotation fields to config.example.yaml"
```

---

### Task 7: One-time historical cleanup

**Prerequisite:** Live test must be complete before running this.

- [ ] **Step 1: Run analytics on existing DB**

```bash
python3 -m src.analytics
```

Save the output for reference — this is the summary of all historical trading data.

- [ ] **Step 2: Delete old DB and log files**

```bash
rm data/arb_history.db
rm logs/arb_bot.log
```

Both will be recreated automatically on the next bot run — the DB by `DataRecorder.__init__` and the log by `RotatingFileHandler`.

- [ ] **Step 3: Verify clean state**

```bash
ls -la data/ logs/
```

Expected: directories exist but no `.db` or `.log` files.

---

### Task 8: Run full test suite

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: ALL PASS — no regressions from the changes.
