---
phase: 09-imap-multi-account
plan: "04"
subsystem: coordinator, options-flow
tags: [imap, coordinator, options-flow, multi-account, uid-dedup, tdd]
dependency_graph:
  requires:
    - 09-01 (xfail test stubs)
    - 09-02 (ImapClient, IMAP constants, ImapAuthError/ImapTransientError)
    - 09-03 (IMAP config flow steps)
  provides:
    - IMAP client dispatch in coordinator (CONNECTION_TYPE_IMAP → ImapClient)
    - UID-based deduplication persisted in HA Store (last_imap_uid)
    - _async_update_data_imap with full parse/forward pipeline
    - Connection-type-aware options flow (IMAP shows imap_search, Gmail shows gmail_query)
  affects:
    - custom_components/shop2parcel/coordinator.py
    - custom_components/shop2parcel/options_flow.py
    - tests/test_coordinator.py
    - tests/test_options_flow.py
tech_stack:
  added: []
  patterns:
    - IMAP client dispatch pattern (connection_type check in __init__ and _async_update_data)
    - UID-based incremental dedup (last_imap_uid in Store alongside forwarded_ids)
    - options flow branching on connection_type (IMAP schema vs Gmail schema)
    - _async_update_data_imap mirrors Gmail path post-fetch (same parse/forward pipeline)
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/coordinator.py
    - custom_components/shop2parcel/options_flow.py
    - tests/test_coordinator.py
    - tests/test_options_flow.py
decisions:
  - IMAP path dispatch is an early-return branch at the top of _async_update_data; Gmail path unchanged
  - last_imap_uid persisted alongside forwarded_ids in the same Store entry (same schema version)
  - IMAP path uses uid_str (str of IMAP UID int) as message_id for forwarded_ids dedup
  - email_date=0 for IMAP messages (no internalDate equivalent); timestamp-based dedup not used
  - options_flow branches on connection_type from entry.data (not entry.options)
metrics:
  duration: "~4 minutes"
  completed: "2026-05-03T00:12:12Z"
  tasks_completed: 2
  files_modified: 4
---

# Phase 09 Plan 04: IMAP Coordinator Dispatch and Options Flow Summary

IMAP coordinator dispatch and options flow branching: coordinator.py extended with ImapClient instantiation, _last_imap_uid Store persistence, and _async_update_data_imap (full parse/forward pipeline matching Gmail path); options_flow.py branches on connection_type to show CONF_IMAP_SEARCH for IMAP entries and CONF_GMAIL_QUERY for Gmail entries. All 4 multi-account stubs and all 7 IMAP client stubs promoted from xfail to xpass; full suite 151 passed, 17 xpassed — Phase 9 complete.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend coordinator.py with IMAP client dispatch and UID store | b17de43 | custom_components/shop2parcel/coordinator.py |
| 2 | Add IMAP branch to options_flow.py + extend test coverage | 90c5f3d | custom_components/shop2parcel/options_flow.py, tests/test_coordinator.py, tests/test_options_flow.py |

## Verification Results

- `pytest tests/api/test_imap_client.py -v` — 7 xpassed (all promoted from xfail)
- `pytest tests/test_multi_account.py -v` — 4 xpassed (all promoted from xfail)
- `pytest tests/test_config_flow.py -v` — 16 passed, 6 xpassed (no regressions)
- `pytest tests/test_coordinator.py -v` — 25 passed including new IMAP dispatch test
- `pytest tests/test_options_flow.py -v` — 7 passed including 3 new IMAP branch tests
- `pytest` (full suite) — 151 passed, 17 xpassed — exit 0 (Phase 9 gate PASSED)

Acceptance criteria verified:
- `grep -c "ImapClient" coordinator.py` → 4 (import + type annotation + 2 instantiation/usage)
- `grep -c "_last_imap_uid" coordinator.py` → 6 (__init__ + load + save + dispatch branch + update + quota logic)
- `grep -c "_async_update_data_imap" coordinator.py` → 2 (definition + call)
- `grep -c "CONNECTION_TYPE_IMAP" coordinator.py` → 3 (__init__ dispatch + update dispatch + method)
- `grep -c "CONF_IMAP_SEARCH" options_flow.py` → 2 (import + usage)
- `grep -c "CONNECTION_TYPE_IMAP" options_flow.py` → 1 (import + usage in branch)

## Deviations from Plan

None — plan executed exactly as written.

## TDD Gate Compliance

Both tasks followed the RED/GREEN/REFACTOR cycle:

**Task 1:**
- RED: Confirmed `test_imap_coordinator_instantiates_imap_client` was xfail before implementation
- GREEN: Implemented coordinator.py changes; all 4 multi-account stubs xpass, 24 coordinator tests pass

**Task 2:**
- RED: Confirmed `test_options_flow_imap_shows_imap_search_field` FAILED before options_flow.py changes
- GREEN: Implemented options_flow.py IMAP branch; all 7 options flow tests pass including 3 new IMAP tests

## Known Stubs

None — all implementation is complete and functional.

## Threat Mitigations Applied (from plan threat model)

- **T-09-04-01**: CONF_IMAP_PASSWORD read from entry.data (HA encrypted storage), passed directly to ImapClient; never logged, never included in exception messages (caught by type only)
- **T-09-04-02**: `f"IMAP transient error: {err}"` propagates ImapTransientError string which per _classify_imap_error docstring does not include the password
- **T-09-04-03**: TLS passed unchanged from entry.data to ImapClient; config flow (Plan 09-03) defaults to "ssl"
- **T-09-04-04**: ImapClient is the only IMAP-touching code; coordinator never calls imaplib directly
- **T-09-04-05**: CONF_IMAP_SEARCH in entry.options (unencrypted) is acceptable per D-07

## Threat Surface Scan

No new network endpoints introduced. _async_update_data_imap makes outbound IMAP connections via ImapClient (same TCP surface as documented in Plan 09-02 threat model). No new trust boundary crossings beyond what Plans 09-02 and 09-03 already cover.

## Self-Check: PASSED

Files verified to exist:
- custom_components/shop2parcel/coordinator.py — FOUND
- custom_components/shop2parcel/options_flow.py — FOUND
- tests/test_coordinator.py — FOUND
- tests/test_options_flow.py — FOUND

Commits verified:
- b17de43 — FOUND
- 90c5f3d — FOUND
