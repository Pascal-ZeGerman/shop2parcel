---
phase: 14-debug-dry-run-mode
plan: "01"
subsystem: options_flow
tags: [debug-mode, const, options-flow, wave-1]
dependency_graph:
  requires: []
  provides: [CONF_DEBUG_MODE constant, debug_mode options field (Gmail + IMAP)]
  affects: [custom_components/shop2parcel/const.py, custom_components/shop2parcel/options_flow.py]
tech_stack:
  added: []
  patterns: [vol.Optional bool field in OptionsFlowWithReload schema]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/const.py
    - custom_components/shop2parcel/options_flow.py
decisions:
  - "CONF_DEBUG_MODE placed after MAX_SUBMITTED_TRACKING_NUMBERS in const.py to keep Phase 14 constants co-located"
  - "vol.Optional (not vol.Required) used for debug_mode so existing users upgrading from v1.1 don't see a form validation error"
  - "Default reads from entry.options with fallback to False, preserving existing behavior"
metrics:
  duration: "3m"
  completed: "2026-05-20T15:44:28Z"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 14 Plan 01: Add CONF_DEBUG_MODE Constant and Options Flow Toggle Summary

**One-liner:** CONF_DEBUG_MODE = "debug_mode" constant added to const.py and vol.Optional bool field wired into both Gmail and IMAP options flow branches.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add CONF_DEBUG_MODE to const.py | 2d0b539 | custom_components/shop2parcel/const.py |
| 2 | Add debug_mode field to both options flow branches | a815164 | custom_components/shop2parcel/options_flow.py |

## What Was Built

**Task 1 — const.py:**
Added `CONF_DEBUG_MODE = "debug_mode"` with Phase 14 (DBG-01) comment, after `MAX_SUBMITTED_TRACKING_NUMBERS` and before `normalize_tracking_number`.

**Task 2 — options_flow.py:**
- Added `CONF_DEBUG_MODE` to the import from `.const` (alphabetically ordered)
- IMAP branch schema: `vol.Optional(CONF_DEBUG_MODE, default=self.config_entry.options.get(CONF_DEBUG_MODE, False)): bool`
- Gmail branch schema: same pattern, appended after `CONF_RESCAN_WINDOW_DAYS` entry

Both entries use `vol.Optional` (not `vol.Required`) so existing v1.1 users are not forced to submit the new field during upgrade. `bool` as the validator enables HA's native checkbox rendering.

## Verification Results

- `python -c "from custom_components.shop2parcel.const import CONF_DEBUG_MODE; assert CONF_DEBUG_MODE == 'debug_mode'"` — exits 0
- `grep -c "CONF_DEBUG_MODE" options_flow.py` — returns 5 (import + 2 schema keys + 2 default lookups)
- `pytest tests/test_options_flow.py -x -q` — 10/10 pass, no regressions

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan adds the constant and UI toggle only. Downstream coordinator logic (debug bypass of ParcelApp, dedup, scan window override) is wired in Plans 02-04.

## Threat Flags

None — debug_mode lives in entry.options which requires HA admin credentials to modify; no external write surface (T-14-01 accepted as specified in plan threat model).

## Self-Check: PASSED

- [x] custom_components/shop2parcel/const.py — exists and contains CONF_DEBUG_MODE
- [x] custom_components/shop2parcel/options_flow.py — exists and contains CONF_DEBUG_MODE (5 occurrences)
- [x] Commit 2d0b539 — exists (Task 1)
- [x] Commit a815164 — exists (Task 2)
- [x] 10 options flow tests pass
