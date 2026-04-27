---
phase: 06-testing-hacs-packaging
plan: "01"
subsystem: testing
tags: [testing, ha-integration, gap-fill, reauth, quota, sensor-lifecycle]
dependency_graph:
  requires: []
  provides: [test-gap-fill-reauth, test-gap-fill-quota-recovery, test-gap-fill-sensor-lifecycle]
  affects: [tests/test_config_flow.py, tests/test_coordinator.py, tests/test_sensor.py]
tech_stack:
  added: []
  patterns: [pytest-asyncio, handler.context dict for source property, 6-patch coordinator scaffold]
key_files:
  created: []
  modified:
    - tests/test_config_flow.py
    - tests/test_coordinator.py
    - tests/test_sensor.py
decisions:
  - "Set handler.context={'source': 'reauth'} instead of handler.source = ... because source is a read-only property reading self.context.get('source') on FlowHandler"
metrics:
  duration: "~15 minutes"
  completed: "2026-04-27"
  tasks: 3
  files_modified: 3
---

# Phase 06 Plan 01: Test Gap Fill (D-01) Summary

**One-liner:** Three targeted tests fill D-01 gaps: reauth OAuth completion path, quota-recovery exit condition, and dynamic sensor add lifecycle — bringing total suite from 117 to 120 tests.

## Pre/Post Baseline

- **Pre-baseline:** 117 tests collected and passing
- **Post-baseline:** 120 tests collected and passing (+3)

## New Tests Added

| Test | File | Gap Filled |
|------|------|-----------|
| `test_reauth_oauth_create_entry_calls_update_reload_and_abort` | `tests/test_config_flow.py` | D-01 gap 1: reauth OAuth completion path |
| `test_quota_recovers_after_reset_at_past` | `tests/test_coordinator.py` | D-01 gap 2: quota-recovery exit condition |
| `test_sensor_appears_when_data_gains_entry` | `tests/test_sensor.py` | D-01 gap 3: dynamic sensor add lifecycle |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed handler.source assignment — source is a read-only property**

- **Found during:** Task 1 first test run
- **Issue:** Plan code used `handler.source = _mock_ha_config_entries.SOURCE_REAUTH` but `source` is a property on `FlowHandler` (from `homeassistant.data_entry_flow`) that reads `self.context.get("source", None)` — it has no setter, raising `AttributeError: property 'source' has no setter`
- **Fix:** Changed to `handler.context = {"source": _mock_ha_config_entries.SOURCE_REAUTH}` which is how HA itself sets the source on new flows
- **Files modified:** `tests/test_config_flow.py`
- **Commit:** 3617f44

## Credentials Check

No real credentials in any new test — all token values use `fake-access-token` / `fake-refresh-token` literal prefixes. `grep -E "ya29\.|gh[ps]_|xox[bp]" tests/` returns zero matches.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. Tests are additive-only to existing files with mock-only I/O.

## Known Stubs

None — tests are complete with concrete assertions.

## Self-Check

- [x] `tests/test_config_flow.py` exists and contains `test_reauth_oauth_create_entry_calls_update_reload_and_abort`
- [x] `tests/test_coordinator.py` exists and contains `test_quota_recovers_after_reset_at_past`
- [x] `tests/test_sensor.py` exists and contains `test_sensor_appears_when_data_gains_entry`
- [x] All 3 commits exist: 3617f44, c6bb743, 2889d4c
- [x] Full suite: 120 passed, 0 failed

## Self-Check: PASSED
