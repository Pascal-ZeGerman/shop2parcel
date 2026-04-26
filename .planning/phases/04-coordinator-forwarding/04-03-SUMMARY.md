---
phase: 04-coordinator-forwarding
plan: "03"
subsystem: coordinator-wiring-options-flow-registration
tags: [coordinator, init, config-flow, options-flow, tdd, wiring, lifecycle]
dependency_graph:
  requires:
    - custom_components/shop2parcel/coordinator.py::Shop2ParcelCoordinator (Plan 02)
    - custom_components/shop2parcel/options_flow.py::OptionsFlowHandler (Plan 02)
    - custom_components/shop2parcel/const.py::DOMAIN
  provides:
    - custom_components/shop2parcel/__init__.py — coordinator-wired async_setup_entry + async_unload_entry with PLATFORMS=[]
    - custom_components/shop2parcel/config_flow.py — OAuth2FlowHandler.async_get_options_flow classmethod
    - tests/test_init.py — 5 Phase 4 coordinator wiring tests
  affects:
    - Phase 5: only needs to add "sensor"/"binary_sensor" to PLATFORMS — no other setup changes
    - HA UI: gear icon now visible on integration, opens options flow
tech_stack:
  added: []
  patterns:
    - Lazy import of Shop2ParcelCoordinator inside async_setup_entry (avoids module-level import chain through coordinator->gmail_client->google packages)
    - "@staticmethod @callback async_get_options_flow for HA gear icon registration"
    - TDD RED (tests fail on Phase 3 __init__.py) then GREEN (tests pass with Phase 4 __init__.py)
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/__init__.py
    - custom_components/shop2parcel/config_flow.py
    - tests/test_init.py
    - tests/conftest.py
decisions:
  - "Lazy import of Shop2ParcelCoordinator inside async_setup_entry — phase 4 __init__.py import triggers coordinator->gmail_client->google import chain at package init time; lazy import ensures conftest mocks are in place before the import runs"
  - "sys.modules mock setup moved before DOMAIN import in conftest — Phase 4 __init__.py now imports coordinator chain at package init time; mocks must precede any shop2parcel package access"
metrics:
  duration: "~9 minutes"
  completed: "2026-04-26"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 4
---

# Phase 4 Plan 03: Coordinator Wiring and Options Flow Registration Summary

Phase 4 closed: Shop2ParcelCoordinator wired into async_setup_entry with correct _async_load_store-before-first_refresh ordering (RESEARCH.md Pitfall 1); async_get_options_flow registered on OAuth2FlowHandler; full 102-test suite green.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Replace __init__.py with coordinator-wired async_setup_entry/async_unload_entry | 3c305cb | custom_components/shop2parcel/__init__.py, tests/test_init.py, tests/conftest.py |
| 2 | Register OptionsFlowHandler on OAuth2FlowHandler in config_flow.py | b0a0b91 | custom_components/shop2parcel/config_flow.py |

## What Was Built

**`__init__.py`**: Replaced Phase 3's parcelapp smoke-test stub with coordinator-wired setup. Coordinator instantiation order: `Shop2ParcelCoordinator(hass, entry)` → `_async_load_store()` → `async_config_entry_first_refresh()`. This ordering prevents Pitfall 1 (re-POSTing all shipments on HA restart because forwarded_ids was empty). `PLATFORMS=[]` declared for Phase 5 to populate without touching setup logic. `async_unload_entry` uses `async_unload_platforms(entry, PLATFORMS)` symmetric pattern per CONTEXT.md D-10.

**`config_flow.py`**: Added `@staticmethod @callback async_get_options_flow` classmethod to `OAuth2FlowHandler`. Added `ConfigEntry` to config_entries import and `from homeassistant.core import callback`. Lazy import of `OptionsFlowHandler` inside the method body (HA-idiomatic pattern to avoid circular dependency risk).

**`tests/test_init.py`**: Replaced 4 Phase 3 tests (which used `aioresponses` to mock the parcelapp smoke-test HTTP call) with 5 Phase 4 tests:
1. `test_setup_entry_wires_coordinator` — asserts hass.data[DOMAIN][entry_id] is a `Shop2ParcelCoordinator` instance
2. `test_setup_entry_calls_load_store_before_first_refresh` — Pitfall 1 runtime ordering verification via `parent.attach_mock` call tracking
3. `test_setup_entry_gmail_auth_failure_sets_setup_error` — `GmailAuthError -> ConfigEntryAuthFailed -> SETUP_ERROR`
4. `test_unload_entry_removes_coordinator` — unload removes coordinator from hass.data
5. `test_setup_entry_forwards_to_empty_platforms` — asserts `PLATFORMS == []`

## Test Results

```
102 passed in 5.76s
```

- 5 new test_init.py tests: all PASSED
- 15 test_config_flow.py tests: all PASSED (no regression)
- 14 test_coordinator.py tests: all PASSED (no regression)
- 4 test_options_flow.py tests: all PASSED (no regression)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Lazy import of Shop2ParcelCoordinator to prevent module-level import chain**
- **Found during:** Task 1 — first test run after writing __init__.py
- **Issue:** Phase 4 `__init__.py` imports `coordinator.py` at module level. `coordinator.py` imports `gmail_client.py` which imports `from google.oauth2.credentials import Credentials` and `from googleapiclient.errors import HttpError`. When conftest.py runs `from custom_components.shop2parcel.const import DOMAIN`, Python loads the `custom_components.shop2parcel` package `__init__.py` which now triggers this import chain — BEFORE conftest has registered the `google`/`googleapiclient` sys.modules mocks. Result: `ModuleNotFoundError: No module named 'google'`.
- **Fix:** Changed top-level `from .coordinator import Shop2ParcelCoordinator` to a lazy import inside `async_setup_entry`. The grep acceptance criterion `grep -c 'from .coordinator import Shop2ParcelCoordinator'` still passes because the string exists in the function body. At production runtime there is zero behavioral difference.
- **Files modified:** `custom_components/shop2parcel/__init__.py`
- **Commit:** 3c305cb

**2. [Rule 1 - Bug] conftest.py sys.modules mock order — move before DOMAIN import**
- **Found during:** Task 1 — after lazy import fix revealed secondary ordering issue
- **Issue:** Even with the lazy coordinator import in `__init__.py`, the `_GOOGLE_MOCK` setup lines in conftest.py appeared AFTER the `from custom_components.shop2parcel.const import DOMAIN` import. Python executes `__init__.py` for the package when `const.py` is first accessed, and the mocks must be registered first.
- **Fix:** Moved all `sys.modules.setdefault(...)` mock registrations to before the `import pytest` and `from custom_components...` lines in conftest.py. Added comment explaining the ordering requirement and why it changed in Phase 4.
- **Files modified:** `tests/conftest.py`
- **Commit:** 3c305cb

## Known Stubs

None. All Phase 4 production code is fully wired. `PLATFORMS = []` is an intentional Phase 4 placeholder, not a stub — Phase 5 populates it with `["sensor", "binary_sensor"]` per CONTEXT.md D-09.

## Threat Surface Scan

No new network endpoints or auth paths. Threat mitigations from plan's threat_model:

| Threat ID | Verification |
|-----------|-------------|
| T-04-03-02 | Verified: Phase 3 `try/except` blocks that could expose `api_key` in exception messages are removed from `__init__.py`. Coordinator's own error translation is the sole exception surface. |
| T-04-03-03 | Verified: `grep 'await coordinator._async_load_store()' __init__.py` returns 1 match. `test_setup_entry_calls_load_store_before_first_refresh` runtime-verifies ordering via parent.attach_mock call tracking. |
| T-04-03-05 | Verified: `hass.data[DOMAIN].pop(entry.entry_id, None)` with `, None` default prevents KeyError on partial-setup entries. |

## Self-Check: PASSED

- `custom_components/shop2parcel/__init__.py`: FOUND
- `custom_components/shop2parcel/config_flow.py`: FOUND (async_get_options_flow added)
- `tests/test_init.py`: FOUND (5 Phase 4 tests)
- `tests/conftest.py`: FOUND (mock ordering fixed)
- Commit 3c305cb: FOUND
- Commit b0a0b91: FOUND
- Full pytest suite: 102 passed, 0 failed
