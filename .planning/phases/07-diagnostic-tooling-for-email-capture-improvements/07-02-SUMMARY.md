---
phase: 07-diagnostic-tooling-for-email-capture-improvements
plan: 02
subsystem: coordinator
tags: [home-assistant, diagnostics, coordinator, instrumentation, dataclass, pollstats, parseresult, tdd]

# Dependency graph
requires:
  - phase: 07-01
    provides: "ParseResult dataclass with keyword_hits, strategy_used, skip_reason fields returned by EmailParser.parse()"
provides:
  - "PollStats dataclass (slots=True, not frozen) with all D-05 fields in coordinator.py"
  - "self._diagnostics: PollStats instance attribute on Shop2ParcelCoordinator (initialized in __init__)"
  - "_async_update_data instrumented to accumulate emails_scanned/matched/found/keyword_hits and reset last_poll_* per cycle"
  - "no_html_body skip reason recorded by coordinator (D-02) before parser.parse() is called"
  - "24-test coordinator test suite (20 existing passing with ParseResult-wrapped mocks + 4 new DIAG-05/06/07/Pitfall1)"
affects: [07-03, diagnostic_sensor, coordinator]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "PollStats as mutable dataclass(slots=True) without frozen=True — accumulator pattern for coordinator diagnostics"
    - "TDD RED/GREEN flow: failing tests committed before implementation"
    - "ParseResult consumed by coordinator: result = parser.parse(); shipment = result.shipment for existing forwarding path"
    - "per-poll reset at top of _async_update_data (D-06), cumulative totals only increment"

key-files:
  created: []
  modified:
    - custom_components/shop2parcel/coordinator.py
    - tests/test_coordinator.py
    - tests/conftest.py
    - tests/api/test_gmail_client.py

key-decisions:
  - "PollStats is NOT frozen (D-04 Pitfall 3) — coordinator mutates it in place across poll cycles"
  - "already-forwarded messages do NOT increment emails_scanned_total (Pitfall 1) — _forwarded_ids guard fires before instrumentation"
  - "no_html_body skip reason set by coordinator before calling parser.parse(), not by parser (D-02)"
  - "conftest.py registers googleapiclient.errors stub with real Exception subclass so test_coordinator.py is standalone-collectable"
  - "test_gmail_client.py uses direct sys.modules assignment + module namespace rebind to override conftest stub"

patterns-established:
  - "Diagnostic accumulator: d = self._diagnostics; d.field += 1 pattern in _async_update_data"
  - "_make_parse_result() helper in tests wraps _make_shipment() for parser mock returns"

requirements-completed: [DIAG-05, DIAG-06, DIAG-07, DIAG-12]

# Metrics
duration: 25min
completed: 2026-05-01
---

# Phase 07 Plan 02: PollStats Coordinator Instrumentation Summary

**PollStats dataclass wired to Shop2ParcelCoordinator._diagnostics, instrumenting _async_update_data to accumulate emails scanned/matched/found and keyword hits per poll cycle with per-cycle reset semantics**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-01T14:00:00Z
- **Completed:** 2026-05-01T14:17:48Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 4

## Accomplishments

- PollStats dataclass (13 fields, slots=True, not frozen) added to coordinator.py at module scope
- self._diagnostics: PollStats = PollStats() initialized in Shop2ParcelCoordinator.__init__ (always non-None)
- _async_update_data fully instrumented: poll-start reset, no_html_body skip, ParseResult accumulation, keyword_hits iteration, poll-end timing
- All 20 existing coordinator tests updated to wrap parse() mock returns in ParseResult via _make_parse_result() helper
- 4 new passing tests covering DIAG-05 (scanned increments), DIAG-06 (per-cycle reset), DIAG-07 (no_html_body), Pitfall 1 (already-forwarded not counted)

## Task Commits

Each task was committed atomically:

1. **Task 1+2 RED: Failing tests for PollStats diagnostics** - `44f340a` (test)
2. **Task 1+2 GREEN: PollStats implementation + test suite update** - `18ba72d` (feat)

_TDD tasks followed RED gate (failing tests committed) then GREEN gate (implementation passing all tests)._

## Files Created/Modified

- `custom_components/shop2parcel/coordinator.py` - Added PollStats dataclass, _diagnostics attribute, full _async_update_data instrumentation; added ParseResult + dataclass/field imports
- `tests/test_coordinator.py` - Added ParseResult import, _make_parse_result() helper, wrapped all existing parse.return_value calls, added 4 new DIAG tests
- `tests/conftest.py` - Added googleapiclient.errors stub registration so test_coordinator.py is standalone-collectable (Rule 1 auto-fix)
- `tests/api/test_gmail_client.py` - Changed setdefault to direct assignment + module-level HttpError rebind to fix isolation (Rule 1 auto-fix)

## Decisions Made

- PollStats NOT frozen (per Pitfall 3 in RESEARCH.md) — coordinator mutates in place, frozen=True would raise FrozenInstanceError
- already-forwarded messages NOT counted in emails_scanned_total — _forwarded_ids guard fires at line 217, first increment at line 231
- no_html_body skip reason set by coordinator (not parser) per D-02 — parser never sees empty HTML body
- Timing captures poll-start before the for loop and poll-end after — excludes Gmail list call timing per D-04 specifics

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_coordinator.py standalone collection failure**
- **Found during:** Pre-execution baseline test run
- **Issue:** `from googleapiclient.errors import HttpError` in gmail_client.py fails when test_coordinator.py is run standalone — conftest registers `googleapiclient` as MagicMock but not `googleapiclient.errors`, causing `ModuleNotFoundError`
- **Fix:** Added `googleapiclient.errors` stub mock with `_StubHttpError` exception class in conftest.py so Python can resolve the submodule import
- **Files modified:** tests/conftest.py
- **Verification:** `pytest tests/test_coordinator.py -x` passes standalone (24 tests)
- **Committed in:** 18ba72d (GREEN phase commit)

**2. [Rule 1 - Bug] Fixed test_gmail_client.py isinstance() breakage caused by conftest fix**
- **Found during:** Full test suite run after conftest fix
- **Issue:** Conftest's setdefault for googleapiclient.errors meant test_gmail_client.py's own setdefault was a no-op; gmail_client.HttpError was bound to _StubHttpError not _MockHttpError; isinstance() checks failed with TypeError
- **Fix:** Changed test_gmail_client.py line 44 from setdefault to direct sys.modules assignment; added module-namespace rebind (`_gmail_client_module.HttpError = _MockHttpError`) so _classify_gmail_error uses the correct class
- **Files modified:** tests/api/test_gmail_client.py
- **Verification:** All 4 previously failing gmail_client tests now pass; full suite 128 passed
- **Committed in:** 18ba72d (GREEN phase commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - existing bugs that blocked the plan)
**Impact on plan:** Both fixes necessary for test correctness and isolation. No scope creep.

## Issues Encountered

- Test ordering dependency in the existing test suite: `test_gmail_client.py` ran before `test_coordinator.py` in full runs (masking the conftest bug), but running `test_coordinator.py` standalone exposed the missing `googleapiclient.errors` mock. Fixed as Rule 1 deviation.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 03 (diagnostic_sensor.py) can now read coordinator._diagnostics: PollStats with all fields populated
- PollStats.emails_scanned_total, emails_matched_total, tracking_numbers_found_total, keyword_hits_total are cumulative counters
- PollStats.last_poll_* fields are per-cycle (reset at top of each _async_update_data call)
- No blockers — all 128 tests pass

## Self-Check: PASSED

- SUMMARY.md exists: FOUND at .planning/phases/07-diagnostic-tooling-for-email-capture-improvements/07-02-SUMMARY.md
- coordinator.py modified: FOUND at custom_components/shop2parcel/coordinator.py
- test_coordinator.py updated: FOUND at tests/test_coordinator.py
- Commit 44f340a (RED): FOUND
- Commit 18ba72d (GREEN): FOUND
- Commit 08a0907 (SUMMARY): FOUND
- Final test run: 128 passed, 2 xfailed, 1 xpassed

---
*Phase: 07-diagnostic-tooling-for-email-capture-improvements*
*Completed: 2026-05-01*
