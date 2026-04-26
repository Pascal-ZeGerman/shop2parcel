---
phase: 04-coordinator-forwarding
plan: "01"
subsystem: coordinator-constants-test-scaffolding
tags: [constants, test-stubs, tdd, wave-0, xfail]
dependency_graph:
  requires: []
  provides:
    - custom_components/shop2parcel/const.py::CONF_POLL_INTERVAL
    - custom_components/shop2parcel/const.py::CONF_GMAIL_QUERY
    - custom_components/shop2parcel/const.py::DEFAULT_POLL_INTERVAL
    - custom_components/shop2parcel/const.py::DEFAULT_GMAIL_QUERY
    - tests/test_coordinator.py (14 xfail stubs)
    - tests/test_options_flow.py (4 xfail stubs)
  affects:
    - Plan 02 coordinator.py (imports CONF_POLL_INTERVAL, CONF_GMAIL_QUERY)
    - Plan 02 options_flow.py (imports all four constants)
tech_stack:
  added: []
  patterns:
    - pytest.mark.xfail(strict=True) for Wave 0 test scaffolding
    - Deferred module imports inside xfail stubs (ImportError -> xfail)
key_files:
  created:
    - tests/test_coordinator.py
    - tests/test_options_flow.py
  modified:
    - custom_components/shop2parcel/const.py
decisions:
  - "Use xfail(strict=True) with deferred imports: import failure in stub body produces XFAIL; once Plan 02 creates module the AssertionError keeps it XFAIL until decorator is removed"
  - "DOMAIN constant preserved unchanged — append-only pattern per PATTERNS.md"
  - "DEFAULT_POLL_INTERVAL = 30 (int) not float — directly feeds timedelta(minutes=...) in Plan 02 coordinator"
metrics:
  duration: "~10 minutes"
  completed: "2026-04-26"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 3
---

# Phase 4 Plan 01: Constants and Test Scaffolding Summary

Wave 0 contract satisfied: const.py extended with four coordinator constants and 18 xfail-strict test stubs written before any implementation code, covering all FWRD-01..FWRD-05 + EMAIL-05 requirements.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Append four configuration constants to const.py | 23b1b87 | custom_components/shop2parcel/const.py |
| 2 | Create test_coordinator.py and test_options_flow.py with xfail-strict stubs | c443b8a | tests/test_coordinator.py, tests/test_options_flow.py |

## What Was Built

- **const.py**: Appended `CONF_POLL_INTERVAL = "poll_interval"`, `CONF_GMAIL_QUERY = "gmail_query"`, `DEFAULT_POLL_INTERVAL = 30` (int), `DEFAULT_GMAIL_QUERY = "from:no-reply@shopify.com subject:shipped"` to the existing file. DOMAIN preserved.
- **tests/test_coordinator.py**: 14 test stubs, one per VALIDATION.md test-map entry for FWRD-01..FWRD-05 + EMAIL-05 coordinator side. Each decorated `@pytest.mark.xfail(strict=True)` with a deferred import of `Shop2ParcelCoordinator` (module does not yet exist).
- **tests/test_options_flow.py**: 4 test stubs for EMAIL-05 options flow side. Deferred import of `OptionsFlowHandler`.

## Test Results

```
83 passed, 18 xfailed in 2.74s
```

Phase 3 tests (test_init.py, test_config_flow.py) continue to pass without modification.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

All 18 tests in test_coordinator.py and test_options_flow.py are intentional stubs. They are decorated `xfail(strict=True)` and will remain XFAIL until Plan 02 implements `coordinator.py` and `options_flow.py` and removes the decorators. This is the design intent of Wave 0 scaffolding — not a defect.

No stubs exist in const.py or in production code.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. Constants are non-secret configuration keys. No threat flags.

## Self-Check: PASSED

- `custom_components/shop2parcel/const.py`: FOUND (modified, 4 constants added)
- `tests/test_coordinator.py`: FOUND (created, 14 xfail stubs)
- `tests/test_options_flow.py`: FOUND (created, 4 xfail stubs)
- Commit 23b1b87: FOUND (feat(04-01): append CONF_POLL_INTERVAL...)
- Commit c443b8a: FOUND (test(04-01): add xfail-strict stubs...)
- Full pytest suite: 83 passed, 18 xfailed, 0 failures
