---
phase: 12-address-tech-debt
plan: "01"
subsystem: diagnostics
tags: [tech-debt, code-review, testing, release]
dependency_graph:
  requires: []
  provides: [diagnostics-private-access-fix, imap-activity-log-test, manifest-v1.1.1]
  affects: [diagnostics.py, manifest.json, tests/test_diagnostics.py, tests/test_diagnostic_sensor.py]
tech_stack:
  added: []
  patterns: [coordinator-public-property-access]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/diagnostics.py
    - custom_components/shop2parcel/manifest.json
    - tests/test_diagnostics.py
    - tests/test_diagnostic_sensor.py
decisions:
  - "Used coordinator.diagnostics (public property) consistently — eliminates private access inconsistency between lines 91 and 111"
  - "New IMAP test seeds via coordinator._diagnostics.scan_events (test-internal seeding pattern used elsewhere in test suite)"
  - "Manifest bumped to 1.1.1 atomically with test/code fixes — single PR for v1.1.1 patch release"
metrics:
  duration_minutes: 8
  completed_date: "2026-05-14"
  tasks_completed: 2
  files_modified: 4
---

# Phase 12 Plan 01: Fix IN-01/IN-02/IN-03 Code Review Findings and Bump Manifest to 1.1.1

Fix three Info-level code review findings from the Phase 11 review and stamp manifest v1.1.1 for the patch release.

## What Was Built

Fixed private-access inconsistency in `diagnostics.py` (IN-01), added an observable API invariant test for IMAP `message_id` prefixing in `test_diagnostics.py` (IN-02), removed a dead `from collections import deque` import from `test_diagnostic_sensor.py` (IN-03), and bumped `manifest.json` version to `1.1.1` for the v1.1.1 patch release.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix IN-01 private access and IN-03 dead import | 392bba3 | diagnostics.py, manifest.json, tests/test_diagnostic_sensor.py |
| 2 | Add IMAP activity_log test (IN-02) | e9257ef | tests/test_diagnostics.py |

## Deviations from Plan

None — plan executed exactly as written.

## Verification Results

Full test suite: **265 passed** (1 new test added — `test_diagnostics_activity_log_contains_imap_events`).

Success criteria verification:
- `diagnostics.py` line 111 reads `d = coordinator.diagnostics` — confirmed (no `coordinator._diagnostics` matches)
- `test_diagnostics.py` contains `test_diagnostics_activity_log_contains_imap_events` — passes
- `test_diagnostic_sensor.py` has no `from collections import deque` in any function body — confirmed
- `manifest.json` version is `"1.1.1"` — confirmed

## Known Stubs

None.

## Threat Flags

None — changes are test and manifest-only with one single-character production fix (private to public property access). No new network endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- custom_components/shop2parcel/diagnostics.py — exists, coordinator.diagnostics at line 111
- custom_components/shop2parcel/manifest.json — exists, version 1.1.1
- tests/test_diagnostics.py — exists, contains imap:uid123
- tests/test_diagnostic_sensor.py — exists, no dead deque import
- Commits 392bba3 and e9257ef — present in git log
