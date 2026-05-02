---
phase: 09-imap-multi-account
plan: "01"
subsystem: testing
tags: [imap, multi-account, xfail, test-stubs, nyquist]
dependency_graph:
  requires: []
  provides:
    - mock_imap_config_entry fixture
    - ImapClient unit test stubs (xfail)
    - multi-account integration test stubs (xfail)
    - IMAP config flow test stubs (xfail)
  affects:
    - tests/conftest.py
    - tests/api/test_imap_client.py
    - tests/test_multi_account.py
    - tests/test_config_flow.py
tech_stack:
  added: []
  patterns:
    - xfail stubs with strict=False for not-yet-implemented modules
    - inline executor helper pattern (mirrors test_gmail_client.py)
key_files:
  created:
    - tests/api/test_imap_client.py
    - tests/test_multi_account.py
  modified:
    - tests/conftest.py
    - tests/test_config_flow.py
decisions:
  - xfail(strict=False) used throughout — allows xpass without failure when coordinator partially satisfies stubs ahead of full implementation
metrics:
  duration: "~5 minutes"
  completed: "2026-05-02T23:43:46Z"
  tasks_completed: 4
  files_modified: 4
---

# Phase 09 Plan 01: IMAP Multi-Account Wave 0 Test Stubs Summary

Wave 0 Nyquist compliance: four test files created/extended with xfail stubs covering ImapClient (D-05, D-06, D-08, D-09), multi-account isolation (MULT-01, MULT-02, D-10, D-11), and IMAP config flow steps (D-01 through D-04) — all in xfail state, full suite green (147 passed, 14 xfailed, 3 xpassed).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add mock_imap_config_entry fixture | 52f8abe | tests/conftest.py |
| 2 | Create test_imap_client.py xfail stubs | 0ddd39b | tests/api/test_imap_client.py |
| 3 | Create test_multi_account.py xfail stubs | b32ba0c | tests/test_multi_account.py |
| 4 | Add IMAP config flow stubs to test_config_flow.py | af435e2 | tests/test_config_flow.py |

## Verification Results

- `pytest tests/api/test_imap_client.py` — 7 xfailed (all pass as expected)
- `pytest tests/test_multi_account.py` — 1 xfailed, 3 xpassed (exit 0)
- `pytest tests/test_config_flow.py` — 16 passed, 6 xfailed (no regressions)
- `pytest` (full suite) — 147 passed, 14 xfailed, 3 xpassed — exit 0
- `pytest tests/test_config_flow.py -k "imap or user"` — 6 IMAP stubs + 1 pre-existing user test collected

## Deviations from Plan

None — plan executed exactly as written.

Note: 3 tests in test_multi_account.py resulted in xpass rather than xfail. This is expected behavior with `strict=False`: `test_two_entries_can_be_added_to_hass`, `test_two_imap_entries_have_separate_store_keys`, and `test_two_entries_produce_non_colliding_entity_unique_ids` pass because the existing coordinator already satisfies the basic isolation invariants being tested. The one test requiring IMAP coordinator dispatch (`test_imap_coordinator_instantiates_imap_client`) correctly xfails. All tests exit 0.

## Known Stubs

None — this plan is entirely stubs by design (Wave 0). All stubs are intentional xfail markers for Plans 09-02 through 09-04 to promote.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. All changes are test-only files with fake credential literals as documented in threat model T-09-01-01 through T-09-01-03.

## Self-Check: PASSED

Files verified to exist:
- tests/conftest.py — FOUND
- tests/api/test_imap_client.py — FOUND
- tests/test_multi_account.py — FOUND
- tests/test_config_flow.py — FOUND

Commits verified:
- 52f8abe — FOUND
- 0ddd39b — FOUND
- b32ba0c — FOUND
- af435e2 — FOUND
