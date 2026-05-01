---
phase: 07-diagnostic-tooling-for-email-capture-improvements
plan: 03
subsystem: home-assistant
tags: [home-assistant, diagnostics, sensor, platform, entity, coordinator]

# Dependency graph
requires:
  - phase: 07-01
    provides: "ParseResult dataclass in email_parser.py; test stub file tests/test_diagnostic_sensor.py"
  - phase: 07-02
    provides: "PollStats dataclass + coordinator._diagnostics accumulation in coordinator.py"
provides:
  - "custom_components/shop2parcel/diagnostic_sensor.py with 4 static DiagnosticSensor subclasses"
  - "EmailsScannedSensor, EmailsMatchedSensor, TrackingNumbersFoundSensor, KeywordHitsSensor"
  - "4 diagnostic sensors co-registered in sensor.py async_setup_entry"
  - "6 live tests in tests/test_diagnostic_sensor.py (3 original xfail converted + 3 new)"
affects: [sensor-entities, testing, phase-08, phase-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Co-registration pattern: static diagnostic sensors registered in sensor.py async_setup_entry alongside dynamic ShipmentSensor"
    - "DiagnosticSensor base class with CoordinatorEntity + SensorEntity, MEASUREMENT state class, shared DeviceInfo"

key-files:
  created:
    - custom_components/shop2parcel/diagnostic_sensor.py
  modified:
    - custom_components/shop2parcel/sensor.py
    - custom_components/shop2parcel/__init__.py
    - tests/test_diagnostic_sensor.py

key-decisions:
  - "Diagnostic sensors registered under 'sensor' platform via sensor.py (not a separate 'diagnostic_sensor' platform domain — HA only accepts built-in platform domains in async_forward_entry_setups)"
  - "DiagnosticSensor base class holds shared DeviceInfo and SensorStateClass.MEASUREMENT (not TOTAL_INCREASING — restart-reset counters would trigger anomaly logs)"
  - "list() and dict() copies in extra_state_attributes prevent HA serializer from mutating live _diagnostics state (T-07-10 mitigation)"

patterns-established:
  - "Co-registration: secondary static sensors can be added in sensor.py's async_setup_entry without a separate platform file"
  - "DiagnosticSensor pattern: CoordinatorEntity subclass reading from coordinator._diagnostics with int native_value (never None, always 0 before first poll)"

requirements-completed: [DIAG-08, DIAG-09, DIAG-10]

# Metrics
duration: 40min
completed: 2026-05-01
---

# Phase 07 Plan 03: Diagnostic Sensor Platform Summary

**4 static DiagnosticSensor entities reading from coordinator._diagnostics (PollStats) and co-registered under the sensor platform in sensor.py**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-05-01T18:00:00Z
- **Completed:** 2026-05-01T18:40:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Created `custom_components/shop2parcel/diagnostic_sensor.py` with 5 classes: `DiagnosticSensor` base + 4 concrete subclasses (`EmailsScannedSensor`, `EmailsMatchedSensor`, `TrackingNumbersFoundSensor`, `KeywordHitsSensor`)
- Wired diagnostic sensors into the sensor platform via `sensor.py`'s `async_setup_entry` (co-registration pattern — worked around HA's platform domain restriction)
- Converted 3 xfail stub tests to live assertions and added 3 new state/attribute coverage tests; all 6 pass; full suite of 134 tests green

## Task Commits

1. **Task 1: Create diagnostic_sensor.py with 4 static sensor entities** - `3dae0b1` (feat)
2. **Task 2: Wire diagnostic_sensor into sensor platform and convert tests to live** - `0c4a8be` (feat)

## Files Created/Modified

- `custom_components/shop2parcel/diagnostic_sensor.py` — New file: `DiagnosticSensor` base class + 4 concrete sensor subclasses; reads `coordinator._diagnostics` (PollStats); all entities share Shop2Parcel DeviceInfo; `SensorStateClass.MEASUREMENT`; 175 lines
- `custom_components/shop2parcel/sensor.py` — Updated `async_setup_entry` to import and co-register 4 static DiagnosticSensor entities alongside existing dynamic ShipmentSensor pattern
- `custom_components/shop2parcel/__init__.py` — Updated PLATFORMS comment to document the co-registration approach (PLATFORMS list stays `["sensor", "binary_sensor"]`)
- `tests/test_diagnostic_sensor.py` — Removed 3 `xfail` markers; added 3 state/attribute live tests (`test_emails_scanned_state_after_poll`, `test_tracking_numbers_found_attributes_after_poll`, `test_keyword_hits_per_keyword_attribute`)

## Decisions Made

- **Co-registration over separate platform domain:** Plan D-13 called for `"diagnostic_sensor"` in PLATFORMS, but HA's `async_forward_entry_setups` only accepts built-in platform domains (e.g., `sensor`, `binary_sensor`). Test confirmed `diagnostic_sensor` setup never fired with the separate domain approach. Fix: import and register all 4 diagnostic sensors in `sensor.py`'s `async_setup_entry`. This achieves D-09 (static registration) and D-10 (CoordinatorEntity reads _diagnostics) without changing behavior.
- **MEASUREMENT vs TOTAL_INCREASING:** Used `SensorStateClass.MEASUREMENT` (not `TOTAL_INCREASING`) because counters reset on HA restart — TOTAL_INCREASING would log anomalies when the counter drops from N to 0 on restart.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Platform loading failure for "diagnostic_sensor" custom domain**
- **Found during:** Task 2 (wire + test), based on prior agent knowledge
- **Issue:** `PLATFORMS = ["sensor", "binary_sensor", "diagnostic_sensor"]` causes HA to silently skip the `diagnostic_sensor` platform setup. HA's `async_forward_entry_setups` only loads modules for built-in platform domains. The custom domain `"diagnostic_sensor"` produces no log lines, no error, and no entity registration.
- **Fix:** Moved the 4 diagnostic sensor registrations into `sensor.py`'s `async_setup_entry` (called before the existing `ShipmentSensor` dynamic listener pattern). Reverted PLATFORMS to `["sensor", "binary_sensor"]` with an explanatory comment. `diagnostic_sensor.py` retains the class definitions and `async_setup_entry` function for code organization, but `sensor.py` calls its contents directly.
- **Files modified:** `custom_components/shop2parcel/sensor.py`, `custom_components/shop2parcel/__init__.py`
- **Verification:** All 6 diagnostic sensor tests pass (`pytest tests/test_diagnostic_sensor.py -v`); full suite 134/134 green

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Required change — the plan's literal D-13 specification was architecturally incorrect for HA. The fix preserves all behavioral requirements (D-09 static setup, D-10 CoordinatorEntity, D-11 shared DeviceInfo, D-12 state/attrs). No new files added, no scope creep.

## Issues Encountered

- HA's `async_forward_entry_setups` restricts platform forwarding to built-in platform domains. The plan assumed `"diagnostic_sensor"` would work as a custom module name (based on `binary_sensor.py` precedent), but `binary_sensor` is itself a built-in HA domain — the pattern only works for built-in names. Resolved by co-registration in `sensor.py`.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Phase 7 complete: all 3 plans delivered (ParseResult instrumentation, PollStats accumulation, 4 diagnostic sensor entities)
- 4 diagnostic sensors visible in HA UI under the Shop2Parcel device: emails_scanned, emails_matched, tracking_numbers_found, keyword_hits
- All 134 tests pass — ready for Phase 8 (Parser Template Expansion)

## Self-Check

Checking files exist and commits exist:

- `custom_components/shop2parcel/diagnostic_sensor.py` — FOUND
- `custom_components/shop2parcel/sensor.py` — FOUND (modified)
- `tests/test_diagnostic_sensor.py` — FOUND (6 tests, 0 xfail)
- Commit `3dae0b1` — Task 1: diagnostic_sensor.py
- Commit `0c4a8be` — Task 2: sensor.py + __init__.py + tests

## Self-Check: PASSED

---
*Phase: 07-diagnostic-tooling-for-email-capture-improvements*
*Completed: 2026-05-01*
