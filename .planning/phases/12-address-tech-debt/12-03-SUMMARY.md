---
phase: 12-address-tech-debt
plan: "03"
subsystem: coordinator
tags: [refactor, coordinator-split, gmail, imap, test-infrastructure]
dependency_graph:
  requires: []
  provides: [gmail_coordinator.GmailCoordinator, imap_coordinator.ImapCoordinator]
  affects: [coordinator.py, __init__.py, tests/test_coordinator.py, tests/conftest.py]
tech_stack:
  added: []
  patterns: [DataUpdateCoordinator subclass pattern, per-module patch targets]
key_files:
  created:
    - custom_components/shop2parcel/gmail_coordinator.py
    - custom_components/shop2parcel/imap_coordinator.py
  modified:
    - custom_components/shop2parcel/coordinator.py
    - custom_components/shop2parcel/__init__.py
    - tests/test_coordinator.py
    - tests/conftest.py
    - tests/test_diagnostic_sensor.py
    - tests/test_diagnostics.py
    - tests/test_init.py
    - tests/test_multi_account.py
decisions:
  - "GmailCoordinator and ImapCoordinator each override _async_update_data; base class has no poll implementation"
  - "Shop2ParcelCoordinator.__init__ no longer sets _email_client; each subclass __init__ sets it after super()"
  - "coordinator.ParcelAppClient patches in cleanup tests remain correct — async_cleanup_delivered still lives in base"
  - "5 remaining coordinator.ParcelAppClient patches in test_coordinator.py are for async_cleanup_delivered path (base coordinator) — not old-style"
metrics:
  duration_seconds: 1057
  completed_date: "2026-05-14"
  tasks_completed: 2
  files_modified: 10
---

# Phase 12 Plan 03: Coordinator Split Summary

Split the 926-line monolithic `coordinator.py` into three files and updated all test patch targets to match the new module layout.

## What Was Built

**GmailCoordinator** (`gmail_coordinator.py`): Subclass of `Shop2ParcelCoordinator` overriding `_async_update_data` with the full Gmail OAuth2 + message-fetch + parse + forward + dedup cycle (verbatim from coordinator.py lines 258-576, minus the IMAP dispatch branch).

**ImapCoordinator** (`imap_coordinator.py`): Subclass of `Shop2ParcelCoordinator` overriding `_async_update_data` with the IMAP SINCE-date fetch + tracking-number dedup cycle (verbatim from coordinator.py `_async_update_data_imap`, renamed; WR-02 and WR-03 fixes already applied in source).

**Slimmed base class** (`coordinator.py`): Retains `PollStats`, `Shop2ParcelStore`, `Shop2ParcelCoordinator` (base with `__init__`, `diagnostics`, `_async_load_store`, `_async_save_store`, `async_cleanup_delivered`), module-level helpers (`_extract_email_meta`, `_extract_imap_email_meta`, `_next_midnight_utc`). No `_async_update_data` method.

**`__init__.py`**: Lazy-import block now imports `GmailCoordinator` and `ImapCoordinator`, dispatches on `CONF_CONNECTION_TYPE`.

**Test infrastructure**: All ~122+ patch targets updated across 7 test files. Gmail-path tests → `gmail_coordinator.*`; IMAP-path tests → `imap_coordinator.*`; `Shop2ParcelStore` and `async_cleanup_delivered`'s `ParcelAppClient` → `coordinator.*` (unchanged, still in base).

## Deviations from Plan

**[Rule 1 - Bug] test_multi_account.py used Shop2ParcelCoordinator directly for IMAP client check**
- **Found during:** Task 2 test run (1 failure after initial patch target updates)
- **Issue:** `test_imap_coordinator_instantiates_imap_client` instantiated `Shop2ParcelCoordinator(hass, mock_imap_config_entry)` and expected `_email_client` to be set — but after refactor, base class no longer sets `_email_client`
- **Fix:** Updated test to import and use `ImapCoordinator` directly
- **Files modified:** `tests/test_multi_account.py`
- **Commit:** 3951828

**[Rule 2 - Missing] test_diagnostic_sensor.py, test_diagnostics.py, test_init.py needed patch target updates too**
- **Found during:** Task 2 test run (AttributeError on coordinator.GmailClient in test_diagnostic_sensor.py)
- **Issue:** Plan only mentioned `test_coordinator.py` and `conftest.py` but 5 additional test files also patched the moved symbols
- **Fix:** Updated all stale patch targets in test_diagnostic_sensor.py, test_diagnostics.py, test_init.py, test_multi_account.py
- **Commit:** 3951828

## Known Stubs

None — all coordinator behavior is fully implemented; this is a structural refactor only.

## Threat Flags

None — this is a pure code-movement refactor. No new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

- custom_components/shop2parcel/gmail_coordinator.py — FOUND
- custom_components/shop2parcel/imap_coordinator.py — FOUND
- Commit e3828ae (feat 12-03) — FOUND
- Commit 3951828 (refactor 12-03) — FOUND
- All 265 tests pass
