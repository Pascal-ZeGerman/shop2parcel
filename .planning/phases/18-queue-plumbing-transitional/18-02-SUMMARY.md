---
phase: 18-queue-plumbing-transitional
plan: "02"
subsystem: coordinator
tags: [stage2-branch, poll-loop, async-on-unload, coordinator-integration, QUE-07]
dependency_graph:
  requires: [18-01]
  provides: [stage2-branch-gmail, stage2-branch-imap, async_on_unload-teardown]
  affects: [gmail_coordinator.py, imap_coordinator.py, __init__.py, tests/test_stage2_queue.py]
tech_stack:
  added: []
  patterns: [stage2-branch-before-debug-mode, async_on_unload-lambda-wrapper, put_nowait-discipline]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/gmail_coordinator.py
    - custom_components/shop2parcel/imap_coordinator.py
    - custom_components/shop2parcel/__init__.py
    - tests/test_stage2_queue.py
decisions:
  - "Stage2Job imported with # noqa: F401 in both subclasses (type-clarity, not directly used — ruff F401 suppressed per plan intent)"
  - "async_on_unload lambda wraps async_stop_stage2 in hass.async_create_task (Pitfall 3 — robust across HA versions)"
  - "Stage-2 branch uses local variable `html` (not `html_body`) — confirmed by source inspection"
metrics:
  duration: "~45 minutes"
  completed: "2026-06-11"
  tasks: 4
  files: 4
requirements: [QUE-07, QUE-01]
---

# Phase 18 Plan 02: Stage-2 Branch Insertion + __init__.py Wiring

**One-liner:** Stage-2 branch inserted BEFORE debug_mode guard in both Gmail and IMAP poll loops, conditional async_start_stage2 + async_on_unload teardown in __init__.py, and 3 integration tests verifying branch routing + QUE-07 + stage2_enabled=False boundary.

## What Was Built

Wired the Stage-2 queue infrastructure from Plan 01 into the actual poll loops:

### gmail_coordinator.py
- Added `Stage2Job` import (`# noqa: F401` — type clarity, not directly used)
- Inserted `if self._diagnostics.stage2_enabled:` branch immediately AFTER `d.last_poll_found.append(...)` and BEFORE `if debug_mode:` (D-03 — Stage-2 bypasses dry-run suppression and quota blocking)
- Branch calls `self._enqueue_stage2(normalized, storage_key, shipment, html, message_id=f"gmail:{msg_id}", meta=email_meta)` then `continue`

### imap_coordinator.py
- Added `Stage2Job` import (`# noqa: F401`)
- Same structural insertion with `message_id=f"imap:{uid_str}"` and `meta=imap_meta`
- Local variable confirmed as `html` (same as gmail path)

### __init__.py
- After `coordinator._diagnostics.stage2_enabled = bool(entry.options.get(CONF_OLLAMA_URL, ""))`:
  - `if coordinator._diagnostics.stage2_enabled:` guard
  - `await coordinator.async_start_stage2()` — constructs queue before first poll
  - `entry.async_on_unload(lambda: hass.async_create_task(coordinator.async_stop_stage2()))` — robust teardown on all paths (RESEARCH.md Pitfall 3)
- For `stage2_enabled=False` entries: no queue constructed, no teardown registered

### tests/test_stage2_queue.py (3 new tests)
- `test_stage2_branch_bypasses_post` (Test 7): R4 acceptance — stage2_enabled=True, one matching email → zero parcel POSTs, queue has 1 Stage2Job, TN in enqueued_keys
- `test_poll_loop_ollama_free_with_full_queue` (Test 8): QUE-07 — pre-filled queue → `stage2_dropped_backpressure` event, proves put_nowait not await put, no Ollama imports in subclass files
- `test_stage2_disabled_does_not_construct_queue` (Test 9): stage2_enabled=False → `async_start_stage2` never called, `_stage2_queue` absent, inline POST path still works

## Threat Mitigations Applied

- **T-18-05 (DoS — event-loop block)**: Stage-2 branch uses `put_nowait` + `continue`; Test 8 verifies no hang with full queue
- **T-18-06 (Tampering — stale keys on reload)**: `async_on_unload` lambda wrapper fires on all teardown paths
- **T-18-07 (EoP — queue on stage2=False entry)**: `if coordinator._diagnostics.stage2_enabled:` guard in __init__.py; Test 9 verifies `_stage2_queue` absent

## Commits

| Hash | Type | Description |
|------|------|-------------|
| e8eaa5a | feat | Stage-2 branch insertion in gmail_coordinator and imap_coordinator |
| ba11a01 | feat | Conditional async_start_stage2 + async_on_unload teardown in __init__.py |
| 45d8a17 | test | 3 integration tests for branch routing, QUE-07, stage2_enabled=False |
| afd5964 | refactor | Lint pass — ruff format + noqa annotations on Stage2Job imports |

## Test Results

- `tests/test_stage2_queue.py`: 9/9 PASSED (6 from Plan 01 + 3 new)
- `tests/test_coordinator.py`: 86/86 PASSED
- Full suite: 509 passed, 1 skipped (no regressions)

## Verification

- `grep -c "self._enqueue_stage2(" gmail_coordinator.py`: 1
- `grep -c "self._enqueue_stage2(" imap_coordinator.py`: 1
- Both `_enqueue_stage2(` calls appear BEFORE `if debug_mode:` in their respective loops
- `grep -q "await coordinator.async_start_stage2()" __init__.py`: PASSED
- `grep -q "coordinator.async_stop_stage2" __init__.py`: PASSED
- `grep -c "cancel_cleanup" __init__.py`: 3 (pre-existing 2 + no new count added; teardown uses async_on_unload lambda)
- `ruff check custom_components/shop2parcel/ tests/test_stage2_queue.py`: PASSED
- `ruff format --check` (touched files): PASSED
- `mypy custom_components/shop2parcel/__init__.py gmail_coordinator.py imap_coordinator.py`: Success (no issues)
- Full pytest suite: 509 passed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Lint] Removed direct Stage2Job usage / added noqa: F401**
- **Found during:** Task 4 (lint pass)
- **Issue:** `Stage2Job` was imported in both subclass files for type clarity as the plan specified, but ruff F401 treats it as an unused import and flags it as an error
- **Fix:** Added `# noqa: F401` comment to suppress the lint error while preserving the import for type documentation purposes (ruff I001 would have triggered on `Stage2Job as Stage2Job` alias form)
- **Files modified:** `gmail_coordinator.py`, `imap_coordinator.py`
- **Commit:** afd5964

**2. [Rule 1 - Bug] Fixed test_poll_loop_ollama_free_with_full_queue add_to_hass order**
- **Found during:** Task 3 first run
- **Issue:** `async_update_entry` was called before `add_to_hass`, raising `UnknownEntry`
- **Fix:** Moved `add_to_hass` before `async_update_entry` (same fix as other tests in the file)
- **Files modified:** `tests/test_stage2_queue.py`
- **Commit:** 45d8a17

## Known Stubs

None — all methods are fully wired to production data.

## Threat Flags

No new threat surface beyond what is documented in the plan's `<threat_model>`.

## Self-Check: PASSED
