---
phase: 14-debug-dry-run-mode
plan: "02"
subsystem: coordinator
tags: [debug-mode, gmail-coordinator, dry-run, persistent-notification, wave-2]
dependency_graph:
  requires:
    - phase: 14-01
      provides: CONF_DEBUG_MODE constant in const.py
  provides:
    - GmailCoordinator debug mode: 365-day window, dedup bypass, dry-run POST suppression
    - Per-email [Shop2Parcel DEBUG] INFO logging in Gmail path
    - Persistent notification lifecycle (create on debug poll, dismiss on normal init)
  affects:
    - custom_components/shop2parcel/gmail_coordinator.py
    - 14-03 (IMAP plan mirrors this implementation)
tech_stack:
  added: []
  patterns:
    - debug_mode local variable read once at poll start; used throughout _async_update_data
    - if not debug_mode: guard wraps dedup block so bypass is explicit
    - dry_run_suppressed continue before quota/POST guard ensures async_add_delivery is unreachable in debug mode
    - if debug_mode: / else: pattern on every per-email outcome log call
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/gmail_coordinator.py
key_decisions:
  - "debug_mode variable read once at top of _async_update_data (after rescan_window_days), not re-read per-email — single config entry read matches HA coordinator pattern"
  - "DBG-04 continue placed BEFORE quota guard so dry_run_suppressed is the only outcome when debug_mode=True for matched emails — no double-path ambiguity"
  - "skipped_quota if/else debug log kept even though debug_mode=True can never reach that branch in practice (the dry_run_suppressed continue fires first) — defensive correctness"
  - "already_added if/else debug log kept for same reason — ParcelAppAlreadyAddedError path is only reachable when debug_mode=False"
patterns_established:
  - "Debug mode guard pattern: read flag once, local variable used throughout — not repeated config lookups"
  - "IMAP coordinator (14-03) must mirror this exact pattern structure"
requirements_completed:
  - DBG-02
  - DBG-03
  - DBG-04
  - DBG-05
  - DBG-06
duration: 18min
completed: "2026-05-20"
---

# Phase 14 Plan 02: GmailCoordinator Debug Mode Logic Summary

**GmailCoordinator becomes the reference debug implementation: 365-day window override, dedup bypass, dry-run POST suppression with scan events, per-email INFO logging, and persistent notification lifecycle.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-05-20T15:48:00Z
- **Completed:** 2026-05-20T16:06:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- `GmailCoordinator.__init__` dismisses `shop2parcel_debug_mode` persistent notification when debug_mode=False (synchronous, no await)
- `GmailCoordinator._async_update_data` implements all five DBG requirements: window override (DBG-02), dedup bypass (DBG-03), POST suppression with `dry_run_suppressed` scan event (DBG-04), per-email INFO logging with `[Shop2Parcel DEBUG]` prefix (DBG-05), and `async_create` notification at end of poll (DBG-06)
- All 64 existing coordinator tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add dismiss call to GmailCoordinator.__init__** - `bf3bbd2` (feat)
2. **Task 2: Add debug mode logic to GmailCoordinator._async_update_data** - `c8f458f` (feat)

## Files Created/Modified

- `custom_components/shop2parcel/gmail_coordinator.py` — added `persistent_notification` import, `CONF_DEBUG_MODE` + `MAX_RESCAN_WINDOW_DAYS` to const imports, dismiss call in `__init__`, and six targeted changes to `_async_update_data` implementing DBG-02 through DBG-06

## Decisions Made

- debug_mode read once at top of `_async_update_data` as a local variable (after `rescan_window_days`) — not repeated per-email. Matches HA coordinator pattern.
- `dry_run_suppressed` continue placed before the quota guard (`if quota_blocked:`) so that in debug mode the only possible outcome for a matched email is `dry_run_suppressed`. The `async_add_delivery` call is structurally unreachable when debug_mode=True.
- The `skipped_quota` and `already_added` branches retain if/else debug log patterns even though they are unreachable in debug mode — defensive correctness and mirrors the plan's specification exactly.
- CONF_DEBUG_MODE appears 3 times in the file (import, dismiss guard, debug_mode assignment) rather than 4 as the plan's grep check expected — the window override uses the `debug_mode` local variable, not a second `CONF_DEBUG_MODE` lookup. This is correct and all tests confirm correct behavior.

## Deviations from Plan

### Minor Implementation Difference

**1. [Rule 1 - Correctness] CONF_DEBUG_MODE count is 3 not 4**
- **Found during:** Post-task verification
- **Issue:** Plan verification check expected `grep -c "CONF_DEBUG_MODE" gmail_coordinator.py` to return >= 4. Actual count is 3.
- **Explanation:** The plan's comment listed "import + debug_mode assignment + dismiss guard + window override" as 4 CONF_DEBUG_MODE uses. But the window override naturally uses the already-read `debug_mode` local variable (not a second `self.config_entry.options.get(CONF_DEBUG_MODE, ...)` call). Using a second config lookup would be redundant and incorrect.
- **Impact:** None — behavior is correct. All 64 coordinator tests pass.

---

**Total deviations:** 1 minor (grep count expectation vs correct implementation)
**Impact on plan:** Zero functional impact. All acceptance criteria met.

## Issues Encountered

None — implementation proceeded cleanly through all six changes in `_async_update_data`.

## Known Stubs

None — all debug mode logic is fully wired. The persistent notification message uses the live `d.last_poll_emails_scanned` counter accumulated during each poll cycle.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes. T-14-02 (INFO log disclosure) and T-14-03 (notification accumulation) were pre-accepted in plan threat model.

## Next Phase Readiness

- GmailCoordinator is the reference implementation for debug mode
- Plan 14-03 (IMAP coordinator) mirrors this exactly using identical pattern structure
- The six change points (window override, dedup bypass, dry_run_suppressed event, INFO log per outcome, persistent notification) serve as the template for imap_coordinator.py

## Self-Check: PASSED

- [x] `custom_components/shop2parcel/gmail_coordinator.py` — exists and contains all debug mode logic
- [x] `persistent_notification` import present
- [x] `CONF_DEBUG_MODE` in const imports block
- [x] `MAX_RESCAN_WINDOW_DAYS` in const imports block
- [x] `async_dismiss` call in `__init__` guarded by `if not entry.options.get(CONF_DEBUG_MODE, False):`
- [x] `debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)` in `_async_update_data`
- [x] `rescan_window_days = MAX_RESCAN_WINDOW_DAYS` inside `if debug_mode:` block
- [x] `if not debug_mode:` wraps the entire dedup check block
- [x] `"outcome": "dry_run_suppressed"` in debug_mode short-circuit block before quota guard
- [x] 7x `[Shop2Parcel DEBUG]` INFO log occurrences (one per outcome branch)
- [x] `persistent_notification.async_create` with `notification_id="shop2parcel_debug_mode"` at end of poll
- [x] Commit bf3bbd2 — exists (Task 1)
- [x] Commit c8f458f — exists (Task 2)
- [x] 64 coordinator tests pass

---
*Phase: 14-debug-dry-run-mode*
*Completed: 2026-05-20*
