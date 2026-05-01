---
phase: 07-diagnostic-tooling-for-email-capture-improvements
plan: "01"
subsystem: email-parser
tags: [home-assistant, diagnostics, dataclass, parser, instrumentation, tdd]
dependency_graph:
  requires: []
  provides:
    - custom_components/shop2parcel/api/email_parser.py::ParseResult
    - tests/test_diagnostic_sensor.py (Wave 0 xfail stubs)
  affects:
    - custom_components/shop2parcel/coordinator.py (Plan 02 consumer)
    - tests/test_coordinator.py (Plan 02 will update mocks)
tech_stack:
  added: []
  patterns:
    - "ParseResult frozen dataclass as value type from parse()"
    - "keyword_hits always-populated dict (3 keys, bool values)"
    - "Wave 0 xfail stub tests for Plan 03 to fill in"
key_files:
  created:
    - tests/test_diagnostic_sensor.py
  modified:
    - custom_components/shop2parcel/api/email_parser.py
    - tests/api/test_email_parser.py
decisions:
  - "ParseResult frozen=True (value type, not accumulator) — coordinator accumulates into PollStats separately"
  - "keyword_hits all-False for HTML strategy parses (D-07) — HTML strategy never runs fallback regexes"
  - "skip_reason 'no_template_match' from _parse_html_template is overridden by regex fallback result — final skip_reason is always 'no_regex_match' on total failure (D-02)"
  - "Wave 0 stubs use xfail markers so pytest suite passes before diagnostic_sensor.py exists"
metrics:
  duration: "5m 26s"
  completed_date: "2026-05-01"
  tasks_completed: 3
  files_modified: 3
---

# Phase 07 Plan 01: ParseResult Dataclass and Parser Instrumentation Summary

**One-liner:** Added frozen `ParseResult` dataclass to `EmailParser.parse()`, enabling structured instrumentation of parse strategy, skip reasons, and per-email keyword-hit booleans for Phase 7 diagnostics.

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Add ParseResult dataclass; update parser return types | 5440c0c | custom_components/shop2parcel/api/email_parser.py |
| 2 | Migrate test_email_parser.py to ParseResult + 4 new tests | db510a5 | tests/api/test_email_parser.py |
| 3 | Create Wave 0 stub tests/test_diagnostic_sensor.py | c96b34f | tests/test_diagnostic_sensor.py |

## What Was Built

### Task 1: ParseResult Dataclass (email_parser.py)

Added `ParseResult(shipment, skip_reason, strategy_used, keyword_hits)` as a `@dataclass(slots=True, frozen=True)` immediately after `ShipmentData`. Changed all three methods to return `ParseResult`:

- `parse()` — always returns `ParseResult`, never `None`
- `_parse_html_template()` — success: `strategy_used="html_template"`, `keyword_hits` all-False; failure: `skip_reason="no_template_match"`
- `_parse_regex_fallback()` — success: `strategy_used="regex_fallback"`, `keyword_hits` reflects actual regex matches; failure: `skip_reason="no_regex_match"`

The `keyword_hits` dict always has exactly 3 keys (`tracking_regex`, `order_regex`, `carrier_regex`) with bool values, even for HTML strategy parses (all False in that case — per D-07/D-08). This guarantees the coordinator can iterate the dict without key guards.

### Task 2: Test Migration (test_email_parser.py)

Migrated 9 existing tests from `result is not None` / `isinstance(result, ShipmentData)` / `result.<field>` to the `result.shipment` accessor pattern. Added 4 new tests:

- `test_parse_always_returns_parseresult` (DIAG-01)
- `test_html_strategy_success_strategy_used` (DIAG-02)
- `test_regex_fallback_success_strategy_used` (DIAG-03)
- `test_keyword_hits_always_has_all_three_keys` (DIAG-04)

Total: 15 tests pass.

### Task 3: Wave 0 Stub (test_diagnostic_sensor.py)

Created `tests/test_diagnostic_sensor.py` with 3 xfail-marked stubs covering DIAG-08, DIAG-09, DIAG-10. The xfail markers ensure pytest passes while `diagnostic_sensor.py` does not yet exist. Plan 03 removes the markers.

## Verification Results

```
Full suite: 124 passed, 2 xfailed, 1 xpassed in ~5s
test_email_parser.py: 15 passed
test_diagnostic_sensor.py: 3 xfailed (expected)
test_no_ha_imports: PASSED (D-03 invariant preserved)
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

`tests/test_diagnostic_sensor.py` contains 3 xfail-marked stubs. These are intentional Wave 0 placeholders; Plan 03 will replace the xfail markers with live assertions against the `diagnostic_sensor.py` implementation.

## Threat Surface Scan

No new threat surface introduced. `ParseResult` is a frozen pure-Python value type with no network I/O, no new HA imports, and no user-supplied input shaping the `keyword_hits` dict (3 hardcoded keys only). T-07-02 (Tampering via mutability) is mitigated by `frozen=True` as designed.

## Self-Check: PASSED

- `custom_components/shop2parcel/api/email_parser.py` — EXISTS and modified (ParseResult added)
- `tests/api/test_email_parser.py` — EXISTS and updated (15 tests)
- `tests/test_diagnostic_sensor.py` — EXISTS and created (3 xfail stubs)
- Commits verified: 5440c0c, db510a5, c96b34f — all present in git log
