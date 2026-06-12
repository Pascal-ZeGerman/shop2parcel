---
phase: 19-worker-spawn-poll-loop-flip
plan: "01"
subsystem: tests
tags:
  - python
  - asyncio
  - homeassistant
  - tdd
  - worker
  - queue
dependency_graph:
  requires:
    - 18-queue-plumbing-transitional/18-02 (Stage2Job, async_start_stage2, async_stop_stage2)
  provides:
    - RED test suite for Plan 02 (coordinator worker implementation)
  affects:
    - tests/test_stage2_worker.py (new)
tech_stack:
  added: []
  patterns:
    - TDD RED phase — tests fail until Plan 02 lands
    - asyncio.shield(asyncio.Event().wait()) for hang simulation in timeout test
    - patch.object on Shop2ParcelCoordinator._async_stage2_worker to prevent real worker spawn
    - _patch_coord_deps_with_ollama() helper wrapping all 8 external patches
key_files:
  created:
    - tests/test_stage2_worker.py
  modified: []
decisions:
  - Test 2.3 uses asyncio.shield(asyncio.Event().wait()) as the hang idiom — Event.wait() never fires and shield prevents CancelledError from interrupting it, forcing wait_for(5.0) to time out
  - Tests 1.2-1.4 patch _async_stage2_worker as AsyncMock so spawned coroutine completes immediately (no hanging background task during extractor-construction assertions)
  - All per-job behavior tests (3.1-3.6) inject jobs via _stage2_queue.put_nowait then drain via asyncio.sleep(0) + hass.async_block_till_done()
  - OllamaTransientError imported for completeness but used as a class reference; no test asserts on Ollama-specific failure paths in the worker (Phase 21 owns loud surface)
metrics:
  duration: "7 minutes"
  completed_date: "2026-06-12"
  tasks_completed: 3
  files_changed: 1
---

# Phase 19 Plan 01: RED Test Suite for Worker Lifecycle

**One-liner:** 15-test RED suite covering QUE-02/QUE-04/QUE-05/MRG-01/D-02/D-03/D-05/D-06/Pitfall-1/Pitfall-5/Pitfall-6 worker lifecycle — all fail on missing coordinator attributes until Plan 02 lands.

## What Was Built

Created `tests/test_stage2_worker.py` with 15 pytest async tests divided into three logical groups:

### Group 1 — Sentinel + extractor construction (D-02, D-03)

| Test | Requirement | Current Failure |
|------|-------------|-----------------|
| `test_worker_task_sentinel_is_none_before_start` | D-02 | `AttributeError: _stage2_worker_task` |
| `test_extractor_constructed_from_options` | D-03 | `AttributeError: coordinator.OllamaClient` |
| `test_extractor_uses_defaults_when_options_missing` | D-03 | `AttributeError: coordinator.OllamaClient` |
| `test_extractor_skips_non_dict_custom_fields` | D-04 | `AttributeError: coordinator.OllamaClient` |

### Group 2 — Worker spawn/cancel/no-leak (QUE-04, QUE-05, Pitfall 1, Pitfall 6)

| Test | Requirement | Current Failure |
|------|-------------|-----------------|
| `test_worker_spawned_in_async_start_stage2` | QUE-04 / SC-1 | `AttributeError: coordinator.OllamaClient` |
| `test_worker_cancelled_on_async_stop_stage2` | QUE-05 | `AttributeError: coordinator.OllamaClient` |
| `test_async_stop_stage2_bounded_5_seconds` | QUE-05 timeout | `AttributeError: coordinator.OllamaClient` |
| `test_no_worker_leak_after_3_reloads` | Pitfall 1 | `AttributeError: coordinator.OllamaClient` |
| `test_async_stop_stage2_safe_when_worker_never_started` | Pitfall 6 | `AttributeError: _stage2_worker_task` |

### Group 3 — Per-job behavior (MRG-01, D-05, D-06, Pitfall 5)

| Test | Requirement | Current Failure |
|------|-------------|-----------------|
| `test_extractor_called_per_job` | MRG-01 | `AttributeError: coordinator.OllamaClient` |
| `test_store_saved_after_successful_post` | D-05 | `AttributeError: coordinator.OllamaClient` |
| `test_coordinator_data_snapshot_pattern` | D-06 | `AttributeError: coordinator.OllamaClient` |
| `test_enqueued_key_discarded_on_success` | Pitfall 5 (success) | `AttributeError: coordinator.OllamaClient` |
| `test_enqueued_key_discarded_on_ollama_failure_without_dedup` | Pitfall 5 (failure) | `AttributeError: coordinator.OllamaClient` |
| `test_worker_does_not_swallow_cancelled_error_during_process_job` | QUE-02 cancel propagation | `AttributeError: coordinator.OllamaClient` |

## RED State Verification

```
collected 15 items — 15 failed, 0 passed
```

All failures are `AttributeError` on `OllamaClient`/`OllamaExtractor` (missing import in coordinator.py) or `AttributeError: _stage2_worker_task`/`_extractor` (missing sentinel in `__init__`). No `ImportError` on the test module itself — the file is correctly importable.

## Phase 18 Regression

```
tests/test_stage2_queue.py — 9 passed, 0 failed
```

## Commits

| Hash | Description |
|------|-------------|
| e40799f | test(19-01): add RED test suite for Phase 19 worker lifecycle |

## Deviations from Plan

None — plan executed exactly as written. All three tasks (fixtures/sentinel tests, lifecycle tests, per-job behavior tests) were written as a single file creation since all target the same file `tests/test_stage2_worker.py`.

### Path Safety Note (Resolved)

Initial Write tool call used the main repo absolute path instead of the worktree-relative path. The file was copied to the correct worktree location (`/home/pascal/Vibe-Coding/HomeAssistant/Shop2Parcel/.claude/worktrees/agent-a78f45bcf734e5849/tests/test_stage2_worker.py`) and removed from the main repo before committing.

## Known Stubs

None — this plan only creates a test file (RED phase). No implementation stubs.

## Threat Flags

None — test file uses synthetic HTML markers (`<html>body</html>`, `<html/>`), no real email content. No new network endpoints or auth paths introduced.

## Self-Check

### Files exist

- [FOUND] `tests/test_stage2_worker.py` — verified via `ls` in worktree

### Commits exist

- [FOUND] `e40799f` — `test(19-01): add RED test suite for Phase 19 worker lifecycle`

## Self-Check: PASSED
