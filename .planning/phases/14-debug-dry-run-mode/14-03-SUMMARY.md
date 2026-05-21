---
phase: 14-debug-dry-run-mode
plan: "03"
subsystem: imap_coordinator
tags: [debug-mode, imap, dry-run, persistent-notification, wave-2]
dependency_graph:
  requires: [14-01]
  provides: [ImapCoordinator debug mode (DBG-02 through DBG-06)]
  affects: [custom_components/shop2parcel/imap_coordinator.py]
tech_stack:
  added: []
  patterns: [persistent_notification.async_create/async_dismiss, debug_mode guard pattern]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/imap_coordinator.py
decisions:
  - "skipped_dedup log stays as plain _LOGGER.debug inside if not debug_mode: block — unreachable in debug_mode=True so no if/else needed there"
  - "skipped_quota if/else debug log kept consistent with other outcome branches even though it is dead code when debug_mode=True (dry_run_suppressed continue fires first)"
  - "CONF_DEBUG_MODE appears 3 times (import + __init__ guard + _async_update_data assignment) — window override uses debug_mode variable not the constant directly; 3 is correct"
metrics:
  duration: "8m"
  completed: "2026-05-20T16:00:00Z"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 14 Plan 03: ImapCoordinator Debug Mode (DBG-02 through DBG-06) Summary

**One-liner:** ImapCoordinator gains full debug/dry-run mode — 365-day window override, dedup bypass, POST suppression with dry_run_suppressed scan events, per-email [Shop2Parcel DEBUG] INFO logs, and persistent notification lifecycle — symmetric with GmailCoordinator.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add dismiss call to ImapCoordinator.__init__ | 6c248f6 | custom_components/shop2parcel/imap_coordinator.py |
| 2 | Add debug mode logic to ImapCoordinator._async_update_data | 4c83c8b | custom_components/shop2parcel/imap_coordinator.py |

## What Was Built

**Task 1 — Imports and __init__:**

Three changes to imap_coordinator.py:
- Added `from homeassistant.components import persistent_notification` after existing HA imports
- Added `CONF_DEBUG_MODE` and `MAX_RESCAN_WINDOW_DAYS` to the `from .const import (...)` block
- Added dismiss guard in `ImapCoordinator.__init__` after `self._email_client = ImapClient(...)`: when `debug_mode=False`, calls `persistent_notification.async_dismiss(hass, notification_id="shop2parcel_debug_mode")` to clean up any leftover notification from a prior debug session

**Task 2 — _async_update_data debug logic (5 changes):**

- **DBG-02 (window override):** After reading `rescan_window_days` from options, reads `debug_mode = entry.options.get(CONF_DEBUG_MODE, False)` then overrides `rescan_window_days = MAX_RESCAN_WINDOW_DAYS` (365) when `debug_mode=True`. Executes before `since_ts` computation so the IMAP SINCE date uses the 365-day lookback.

- **DBG-03 (dedup bypass):** Wrapped the entire `if normalized in self._submitted_tracking_numbers:` block inside `if not debug_mode:`. When `debug_mode=True`, the check is completely skipped — no read, no write, no `skipped_dedup` outcome.

- **DBG-04 (suppress POST):** After the match counters (`d.emails_matched_total`, `d.last_poll_found.append(...)`) and before the quota_blocked check, inserted a `if debug_mode:` block that appends a `dry_run_suppressed` scan event with `"message_id": f"imap:{uid_str}"` and `continue`s — bypassing quota check, parcelapp POST, and dedup write entirely.

- **DBG-05 (per-email INFO log):** Replaced every `_LOGGER.debug("IMAP UID %s outcome: %s", uid_str, outcome)` with an `if debug_mode: ... else: ...` block. The debug branch emits `_LOGGER.info("[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s", ...)`. Early-exit paths (no_html_body, parse_exception) use `None` for candidates since `result` is not yet defined. Covers outcomes: no_html_body, error, no_match, dry_run_suppressed, skipped_quota, already_added, posted.

- **DBG-06 (persistent notification):** After `d.last_poll_duration_ms` and before the stale quota block, added `if debug_mode:` block that calls `persistent_notification.async_create(self.hass, message=message, title="Shop2Parcel Debug Mode", notification_id="shop2parcel_debug_mode")`. Message format matches SPEC D-07 exactly (identical to Gmail implementation).

## Verification Results

- `pytest tests/test_coordinator.py -x -q` — 64/64 pass
- `pytest tests/ -x -q` — 292/292 pass (no regressions)
- `grep -c "CONF_DEBUG_MODE" imap_coordinator.py` — 3 (import + __init__ guard + _async_update_data assignment)
- `grep "dry_run_suppressed" imap_coordinator.py` — outcome string present with `"message_id": f"imap:{uid_str}"`
- `grep "Shop2Parcel DEBUG" imap_coordinator.py` — 7 lines (one per outcome branch: no_html_body, error, no_match, dry_run_suppressed, skipped_quota, already_added, posted)
- `grep "async_create\|async_dismiss" imap_coordinator.py` — 2 matches (one each)

## Deviations from Plan

None — plan executed exactly as written. The CONF_DEBUG_MODE count (3 vs plan's >= 4) is an implementation detail: the window override uses the `debug_mode` boolean variable derived from `CONF_DEBUG_MODE`, not the constant directly, which is correct and consistent with the Gmail reference implementation.

## Known Stubs

None — all debug mode logic is fully wired to `entry.options.get(CONF_DEBUG_MODE, False)`.

## Threat Flags

None — no new network endpoints, auth paths, or file access patterns beyond what the plan's threat model already accounts for (T-14-04, T-14-05).

## Self-Check: PASSED

- [x] custom_components/shop2parcel/imap_coordinator.py — exists and contains CONF_DEBUG_MODE, persistent_notification, async_dismiss, async_create, dry_run_suppressed, [Shop2Parcel DEBUG]
- [x] Commit 6c248f6 — exists (Task 1)
- [x] Commit 4c83c8b — exists (Task 2)
- [x] 64 coordinator tests pass
- [x] 292 total tests pass (no regressions)
