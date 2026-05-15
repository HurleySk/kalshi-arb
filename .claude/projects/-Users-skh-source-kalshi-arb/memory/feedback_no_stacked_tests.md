---
name: no-stacked-tests
description: Never launch multiple pytest runs simultaneously — wait for previous run or use run_in_background, not both
metadata:
  type: feedback
---

Never stack test runs. Do not launch a new `python3 -m pytest` while one is already running.

**Why:** User explicitly corrected this behavior during a live test session. Stacking test runs wastes resources and creates confusing output.

**How to apply:** Before running pytest, check if a previous run is still in progress (backgrounded or foreground). If so, wait for it to finish. Use `run_in_background` for a single run if you need to continue other work, but never launch a second pytest on top of an existing one.
