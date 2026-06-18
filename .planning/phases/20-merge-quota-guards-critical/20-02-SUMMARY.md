---
phase: 20-merge-quota-guards-critical
plan: "02"
subsystem: coordinator
tags:
  - coordinator
  - stage2-worker
  - merge-wiring
  - fail-handling
  - mrg-02
  - mrg-03
  - fail-03
dependency_graph:
  requires:
    - custom_components/shop2parcel/merge.py (merge_llm_authoritative — Plan 20-01)
    - custom_components/shop2parcel/api/exceptions.py (OllamaTransientError, OllamaSchemaError)
    - custom_components/shop2parcel/coordinator.py (Stage2Job, _enqueue_stage2, _async_process_stage2_job)
  provides:
    - coordinator._async_process_stage2_job (MRG-02 merge wiring, MRG-03 conflict event, FAIL-03 early return)
    - coordinator.Stage2Job (extended with message_id + meta fields — D-06, D-07)
  affects:
    - All Stage2Job constructor sites in coordinator.py and test files (Pitfall 1 closed)
tech_stack:
  added: []
  patterns:
    - merge_llm_authoritative called per-job; merged ShipmentData passed to POST
    - stage2_conflict activity event emitted via _emit_scan_event when conflicts non-empty
    - FAIL-03 early return on OllamaTransientError/OllamaSchemaError — no POST, no dedup write
    - Stage2Job extended with required message_id/meta fields (frozen=True preserved)
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/coordinator.py
    - tests/test_stage2_queue.py
    - tests/test_stage2_worker.py
decisions:
  - "merge_llm_authoritative import at coordinator top-level (from .merge import merge_llm_authoritative) — alphabetically after OllamaExtractor import"
  - "Stage2Job message_id + meta are REQUIRED fields (no defaults per D-06) — loud TypeError on misconfigured callers"
  - "FAIL-03 uses fmt:skip on except tuple — ruff formatter incorrectly removes parentheses from except (OllamaTransientError, OllamaSchemaError) creating invalid Python 3 syntax"
  - "test_coordinator_data_snapshot_pattern updated from object-identity (is) to content-equality check — Phase 20 merge always calls dataclasses.replace producing a new ShipmentData instance"
metrics:
  duration_seconds: 1800
  completed_date: "2026-06-15"
  tasks_completed: 3
  files_changed: 3
---

# Phase 20 Plan 02: Coordinator Merge Wiring + FAIL-03 Summary

**One-liner:** `_async_process_stage2_job` wired to call `merge_llm_authoritative` per job — merged ShipmentData POSTed (MRG-02), single `stage2_conflict` activity event emitted on LLM divergence (MRG-03), and `OllamaTransientError`/`OllamaSchemaError` short-circuit with no POST and no dedup write (FAIL-03); `Stage2Job` extended with required `message_id`/`meta` fields (D-06/D-07) and all constructor sites updated.

## What Was Built

Three atomic changes to `custom_components/shop2parcel/coordinator.py` and the test suite:

**Task 1 — Stage2Job extension (D-06, D-07):**
- Added `message_id: str` and `meta: dict` as required fields to the `Stage2Job` frozen dataclass
- Updated `_enqueue_stage2` to pass `message_id=message_id, meta=meta` to the constructor (D-07 — the params existed but were dropped)
- Updated all 5 `Stage2Job(...)` constructor sites in `tests/test_stage2_queue.py`
- Updated all 6 `Stage2Job(...)` constructor sites in `tests/test_stage2_worker.py`
- Added 2 new tests: `test_stage2job_has_message_id_and_meta_fields` and `test_stage2job_frozen_with_new_fields`

**Task 2 — Merge wiring + conflict event (MRG-02, MRG-03):**
- Added `from .merge import merge_llm_authoritative` import
- Refactored `_async_process_stage2_job`: captures `stage2_result` from `async_extract`, calls `merge_llm_authoritative(job.shipment, stage2_result)` → `(merged_shipment, conflicts)`
- Emits `stage2_conflict` event via `_emit_scan_event` when `conflicts` is non-empty (MRG-03)
- POST, dedup write, store save, and `async_set_updated_data` all use `merged_shipment` (MRG-02)
- Removed obsolete Phase 19 stub comment `"Phase 20 will replace job.shipment pass-through"`
- Added 3 new integration tests: `test_merge_promotes_stage2_value_when_stage1_none`, `test_merge_conflict_keeps_stage1_and_emits_event`, `test_two_field_conflict_emits_single_event`

**Task 3 — FAIL-03 early return (OllamaTransientError/OllamaSchemaError):**
- `OllamaTransientError` and `OllamaSchemaError` caught before merge; debug log emitted, `_stage2_enqueued_keys.discard` called, then `return` — control never reaches POST or dedup write
- Added 2 new tests: `test_ollama_transient_no_post_no_dedup`, `test_ollama_schema_no_post_no_dedup`
- Phase 21 owns the loud `ERROR`-level log and HA persistent notification (FAIL-01); Phase 20 keeps `DEBUG`

## TDD Commits

| Gate | Commit | Files | Notes |
|------|--------|-------|-------|
| T1 RED | `eb758c6` | tests/test_stage2_queue.py | 2 tests failing: Stage2Job missing message_id/meta fields |
| T1 GREEN | `0efd351` | coordinator.py, test_stage2_queue.py, test_stage2_worker.py | All constructor sites updated; 26 tests pass |
| T2 RED | `1dd7941` | tests/test_stage2_worker.py | 3 tests failing: merge not wired; POST uses job.shipment |
| T2 GREEN | `a747a37` | coordinator.py, tests/test_stage2_worker.py | Merge wired; 33 tests pass |
| T3 + lint | `23de4fd` | tests/test_stage2_worker.py | 2 FAIL-03 tests added; all 46 pass |
| Lint fix | `0ebdcf6` | coordinator.py, tests/test_stage2_worker.py | ruff format + fmt:skip fix |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] ruff formatter removes parentheses from except tuple**
- **Found during:** Lint pass after Task 3
- **Issue:** `ruff format` converts `except (OllamaTransientError, OllamaSchemaError):` to
  `except OllamaTransientError, OllamaSchemaError:` — valid Python 2 syntax but invalid Python 3
- **Fix:** Added `# fmt: skip` to the except line to prevent reformatting
- **Files modified:** `custom_components/shop2parcel/coordinator.py`
- **Commit:** `0ebdcf6`

**2. [Rule 1 - Bug] test_coordinator_data_snapshot_pattern: object-identity check broke with merge**
- **Found during:** Task 2 GREEN
- **Issue:** Existing test asserted `post_arg["msg1::1Z999"] is shipment` (object identity). Phase 20's
  `merge_llm_authoritative` calls `dataclasses.replace()` which always creates a new `ShipmentData`
  instance, breaking the identity assertion even when no fields change
- **Fix:** Changed to content-equality check: `stored.tracking_number == shipment.tracking_number`,
  `stored.carrier_name == shipment.carrier_name`, `stored.order_name == shipment.order_name`. The
  core D-06 snapshot intent (dict is new: `post_arg is not pre`) still passes
- **Files modified:** `tests/test_stage2_worker.py`
- **Commit:** `a747a37`

## Known Stubs

None — all three tasks fully implemented; no placeholder values.

## Threat Flags

No new threat surface introduced beyond the plan's threat model. Mitigations applied:

| T-ID | Mitigation Applied |
|------|--------------------|
| T-20-02-01 | merged_shipment used as POST body; LLM cannot overwrite Stage-1 non-None locked fields (MRG-03 guard in merge.py) |
| T-20-02-02 | `extra["conflicts"]` contains only field name + the two values; no raw LLM response text |
| T-20-02-03 | FAIL-03: dedup unwritten on Ollama error → next poll retries naturally |

## Self-Check: PASSED

- `coordinator.py` modified: FOUND
- `tests/test_stage2_worker.py` modified: FOUND
- `tests/test_stage2_queue.py` modified: FOUND
- T1 RED commit `eb758c6` exists: FOUND
- T1 GREEN commit `0efd351` exists: FOUND
- T2 RED commit `1dd7941` exists: FOUND
- T2 GREEN commit `a747a37` exists: FOUND
- T3 + lint commit `23de4fd` exists: FOUND
- `grep -n "from .merge import merge_llm_authoritative" coordinator.py` returns 1 line: CONFIRMED
- `grep -n "outcome=\"stage2_conflict\"" coordinator.py` returns 1 line: CONFIRMED
- `grep -n "merge_llm_authoritative(job.shipment" coordinator.py` returns 1 line: CONFIRMED
- `grep -n "Phase 20 will replace" coordinator.py` returns nothing: CONFIRMED
- `except (OllamaTransientError, OllamaSchemaError)` with `return` inside: CONFIRMED
- 546 tests pass, 1 skipped: CONFIRMED
- `ruff check` exits 0: CONFIRMED
- `ruff format --check` exits 0: CONFIRMED
