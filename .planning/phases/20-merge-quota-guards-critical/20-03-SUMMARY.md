---
phase: 20-merge-quota-guards-critical
plan: "03"
subsystem: coordinator
tags:
  - quota-cap
  - persistent-notification
  - coordinator
  - mrg-05
dependency_graph:
  requires:
    - custom_components/shop2parcel/const.py (debug_mode_notification_id pattern — mirrored)
    - custom_components/shop2parcel/coordinator.py (Shop2ParcelCoordinator base class — Plan 20-02)
    - custom_components/shop2parcel/gmail_coordinator.py (_async_update_data entry point)
    - custom_components/shop2parcel/imap_coordinator.py (_async_update_data entry point)
    - custom_components/shop2parcel/__init__.py (async_remove_entry — existing shape)
  provides:
    - MAX_STAGE2_POSTS_PER_POLL=5 constant + stage2_cap_notification_id() helper in const.py
    - _stage2_posts_this_poll + _stage2_cap_notified_this_poll counter attrs in coordinator __init__
    - _reset_stage2_poll_counters() base helper on Shop2ParcelCoordinator
    - Cap gate in _async_process_stage2_job (before extractor call)
    - Once-per-poll persistent notification on cap hit
    - Post-success increment (D-12)
    - Cap notification dismissal in async_remove_entry
  affects:
    - Phase 21 (failure surface + diagnostics — builds on coordinator state)
tech_stack:
  added: []
  patterns:
    - Per-poll counter reset via base-class helper (DRY — subclasses call self._reset_stage2_poll_counters())
    - Cap gate placed before extractor to avoid wasted Ollama inference on cap-hit polls
    - Once-per-poll notification flag (_stage2_cap_notified_this_poll) guards persistent_notification spam
    - Post-success-only increment (D-12) preserves retry-ability of failed POSTs within cap budget

key-files:
  created: []
  modified:
    - custom_components/shop2parcel/const.py
    - custom_components/shop2parcel/coordinator.py
    - custom_components/shop2parcel/gmail_coordinator.py
    - custom_components/shop2parcel/imap_coordinator.py
    - custom_components/shop2parcel/__init__.py
    - tests/test_stage2_worker.py
    - tests/test_debug_mode.py

key-decisions:
  - "Cap gate placed BEFORE extractor call in _async_process_stage2_job — cap-skipped jobs never reach Ollama (avoids wasted inference cost during cap-hit polls)"
  - "_reset_stage2_poll_counters() defined on base class (DRY) — single implementation shared by GmailCoordinator and ImapCoordinator without duplication"
  - "Static source test for temperature:0 — no live Ollama call required; pathlib.Path.read_text() check covers the SPEC §Boundaries verification requirement"
  - "test_async_remove_entry_dismisses_debug_notification updated from assert_called_once_with to assert call_count==2 — intentional behavior change (2 dismissals now)"

requirements-completed:
  - MRG-05
  - MRG-02

duration: 18min
completed: "2026-06-15"
---

# Phase 20 Plan 03: MRG-05 Per-Poll Stage-2 POST Cap Summary

**Per-poll Stage-2 POST cap (MAX_STAGE2_POSTS_PER_POLL=5) with gate before Ollama extractor, single-shot HA notification per poll, post-success-only counter increment (D-12), and cap-notification dismissal on entry remove**

## Performance

- **Duration:** 18 min
- **Started:** 2026-06-15T20:36:04Z
- **Completed:** 2026-06-15T20:54:18Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- MRG-05 fully implemented: `MAX_STAGE2_POSTS_PER_POLL = 5` constant + `stage2_cap_notification_id()` helper in `const.py`, mirroring the `debug_mode_notification_id` pattern
- Cap gate sits BEFORE the `if self._extractor is not None:` check — cap-skipped jobs never call Ollama, eliminating wasted inference cost on high-volume poll cycles
- Once-per-poll notification via `_stage2_cap_notified_this_poll` flag — first cap-hit fires `persistent_notification.async_create("Shop2Parcel Stage-2 Cap Hit", ...)`; subsequent cap-hits in the same poll are silent
- Counter increments only on the success path (D-12) — `already-added`, `invalid-tracking`, `quota`, and `transient-error` paths do NOT increment the cap budget
- Cap-skipped items have their `_stage2_enqueued_keys` entry discarded (allowing re-enqueue) and are NOT written to `_submitted_tracking_numbers` (retryable next poll)
- `async_remove_entry` dismisses both the debug-mode and Stage-2 cap notifications on integration removal

## TDD Commits

| Gate | Commit | Files | Notes |
|------|--------|-------|-------|
| T1 RED | `b92f8eb` | tests/test_stage2_worker.py | 5 failing tests: ImportError on new const + AttributeError on new attrs/method |
| T1 GREEN | `9747e06` | const.py, coordinator.py, gmail_coordinator.py, imap_coordinator.py | All 5 Task 1 tests pass; 551 total |
| T2 RED | `368f59e` | tests/test_stage2_worker.py | 3 failing tests: AttributeError MAX_STAGE2_POSTS_PER_POLL not in coordinator module |
| T2 GREEN | `65d19ca` | coordinator.py | Cap gate + notification + increment; 554 total |
| T3 RED | `ec2a6bf` | tests/test_stage2_worker.py | temperature_zero passes; remove_entry_dismisses_cap fails |
| T3 GREEN | `dcc5109` | __init__.py, test_stage2_worker.py, test_debug_mode.py | Both T3 tests pass; 556 total |

## Files Created/Modified

- `custom_components/shop2parcel/const.py` — Added `MAX_STAGE2_POSTS_PER_POLL=5`, `STAGE2_CAP_NOTIFICATION_ID_PREFIX`, `stage2_cap_notification_id()` helper
- `custom_components/shop2parcel/coordinator.py` — Added `persistent_notification` import, `MAX_STAGE2_POSTS_PER_POLL`/`stage2_cap_notification_id` const imports, counter attrs in `__init__`, `_reset_stage2_poll_counters()` helper, cap gate + notification in `_async_process_stage2_job`, success-path increment
- `custom_components/shop2parcel/gmail_coordinator.py` — Added `self._reset_stage2_poll_counters()` call at top of `_async_update_data`
- `custom_components/shop2parcel/imap_coordinator.py` — Added `self._reset_stage2_poll_counters()` call at top of `_async_update_data`
- `custom_components/shop2parcel/__init__.py` — Updated `async_remove_entry` to dismiss both debug-mode and Stage-2 cap notifications; updated import to include `stage2_cap_notification_id`
- `tests/test_stage2_worker.py` — 8 new tests (5 Task-1 scaffolding, 3 Task-2 cap/increment/reset, 2 Task-3 dismissal/temperature)
- `tests/test_debug_mode.py` — Updated `test_async_remove_entry_dismisses_debug_notification` for 2-dismiss behavior

## Decisions Made

- Cap gate placed BEFORE extractor call — avoids wasted Ollama inference on cap-hit polls (D-09)
- `_reset_stage2_poll_counters()` on base class — DRY; both GmailCoordinator and ImapCoordinator share single implementation
- Static source test for `temperature:0` — `pathlib.Path(...).read_text()` with substring check; no live Ollama call

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_async_remove_entry_dismisses_debug_notification used assert_called_once_with**
- **Found during:** Task 3 (Task 3 GREEN — full suite run)
- **Issue:** Existing test in `test_debug_mode.py` used `mock_dismiss.assert_called_once_with(...)` — this assertion fails after Task 3 changed `async_remove_entry` to call `async_dismiss` twice (once for debug-mode, once for stage2_cap). The test intent (debug_mode notification is dismissed) is still valid but the assertion style was incorrect for the new 2-dismiss behavior.
- **Fix:** Changed to `assert mock_dismiss.call_count == 2` + verify debug_mode notification_id in the set of dismissed IDs
- **Files modified:** `tests/test_debug_mode.py`
- **Verification:** Full suite passes (556 tests)
- **Committed in:** `dcc5109` (Task 3 GREEN commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Auto-fix necessary for correctness (test expectation update after intentional behavior change). No scope creep.

## Issues Encountered

- **mypy not in venv:** `mypy` binary absent from `.venv/bin/` — skipped mypy run (consistent with prior Phase 20 plans; pre-existing environment limitation)
- **cwd drift on first commit:** Initial git commit accidentally targeted `main` branch of main repo instead of worktree. Reverted with `git revert` on main. All plan work is correctly on `worktree-agent-a8843ed11ed25205d`.
- **Test patch target:** Initial `test_async_remove_entry_dismisses_cap_notification` used wrong patch target (`custom_components.shop2parcel.persistent_notification`). Corrected to `homeassistant.components.persistent_notification.async_dismiss` to match how `async_remove_entry` uses a lazy import pattern.

## Known Stubs

None — all MRG-05 behavior is fully implemented; no placeholder values.

## Threat Flags

No new threat surface introduced beyond the plan's threat model. Mitigations applied:

| T-ID | Mitigation Applied |
|------|--------------------|
| T-20-03-01 | `MAX_STAGE2_POSTS_PER_POLL=5` caps Stage-2 POSTs at 5/poll; gate placed before Ollama call |
| T-20-03-02 | `_stage2_cap_notified_this_poll` flag ensures at most one notification per poll |
| T-20-03-03 | Cap notification body contains only the cap value + "retry next poll" — no email content, no tracking numbers |

## Self-Check: PASSED

- `custom_components/shop2parcel/const.py` modified (MAX_STAGE2_POSTS_PER_POLL + stage2_cap_notification_id): CONFIRMED
- `custom_components/shop2parcel/coordinator.py` modified (cap gate + increment + persistent_notification import): CONFIRMED
- `custom_components/shop2parcel/gmail_coordinator.py` modified (_reset_stage2_poll_counters call): CONFIRMED
- `custom_components/shop2parcel/imap_coordinator.py` modified (_reset_stage2_poll_counters call): CONFIRMED
- `custom_components/shop2parcel/__init__.py` modified (stage2_cap dismiss): CONFIRMED
- T1 RED commit `b92f8eb`: CONFIRMED
- T1 GREEN commit `9747e06`: CONFIRMED
- T2 RED commit `368f59e`: CONFIRMED
- T2 GREEN commit `65d19ca`: CONFIRMED
- T3 RED commit `ec2a6bf`: CONFIRMED
- T3 GREEN commit `dcc5109`: CONFIRMED
- 556 tests pass, 1 skipped: CONFIRMED
- `ruff check custom_components/shop2parcel/` exits 0: CONFIRMED
- `grep -nE "^MAX_STAGE2_POSTS_PER_POLL" const.py` returns value 5: CONFIRMED (line 89)
- Cap gate appears BEFORE extractor check in coordinator.py: CONFIRMED (line 593 vs line 619)
- `self._stage2_posts_this_poll += 1` AFTER async_add_delivery BEFORE dedup write: CONFIRMED (line 697 vs 698)
- `stage2_cap_notification_id` in __init__.py (import + dismiss call): CONFIRMED
- `"temperature": 0` in ollama_client.py: CONFIRMED (line 171)
