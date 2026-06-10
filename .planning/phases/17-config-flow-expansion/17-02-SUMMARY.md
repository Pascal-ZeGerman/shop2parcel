---
phase: 17-config-flow-expansion
plan: "02"
subsystem: coordinator, config_flow, const
tags: [cfg-04, backward-compat, stage2_enabled, pollstats, tdd]
depends_on:
  requires: []
  provides: [PollStats.stage2_enabled, async_setup_entry stage2 derivation, config_flow options seed]
  affects: [coordinator.py, __init__.py, config_flow.py, const.py]
tech_stack:
  added: []
  patterns: [TDD RED-GREEN, PollStats dataclass append, lazy-import pattern, HA config entry options seed]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/const.py
    - custom_components/shop2parcel/coordinator.py
    - custom_components/shop2parcel/__init__.py
    - custom_components/shop2parcel/config_flow.py
    - tests/test_init.py
    - tests/test_config_flow.py
decisions:
  - "CONF_OLLAMA_URL and CONF_STAGE2_ENABLED added to const.py (Plan 01 not yet landed in parallel wave)"
  - "bool(entry.options.get(CONF_OLLAMA_URL, '')) used as D-05 specifies — empty-string fallback prevents AttributeError on v1.2 entries"
  - "_FakeAbstractOAuth2FlowHandler.async_create_entry updated to accept optional options kwarg for test correctness"
metrics:
  duration: "566s (9 minutes)"
  completed: "2026-06-10"
  tasks: 2
  files: 6
---

# Phase 17 Plan 02: PollStats stage2_enabled + config_flow options seed Summary

**One-liner:** CFG-04 backward compat via PollStats.stage2_enabled derived at setup time and seeded False on new entries, wired with full TDD RED-GREEN cycle.

## Tasks Completed

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 (RED) | PollStats.stage2_enabled tests | bfe000a | committed |
| 1 (GREEN) | PollStats field + __init__.py derivation | 147d462 | committed |
| 2 (RED) | async_step_finish options seed tests | 2cc5736 | committed |
| 2 (GREEN) | config_flow.py options kwarg | 1154d53 | committed |

## Changes Made

### const.py — Phase 17 constants added
- `CONF_OLLAMA_URL = "ollama_url"` (and related Ollama constants)
- `CONF_STAGE2_ENABLED = "stage2_enabled"` — the key constant for this plan
- Added all Phase 17 constants needed by both Plan 02 and downstream plans (01 parallel wave)

### coordinator.py — PollStats field added (1 field)
```python
stage2_enabled: bool = False
# Phase 17 D-05: derived at async_setup_entry time; False until Ollama URL is set.
```
- Appended as the last field in `@dataclass(slots=True) class PollStats`
- `PollStats().stage2_enabled` defaults to `False` (Test 5 verified)

### __init__.py — Derivation line added (1 line)
```python
coordinator._diagnostics.stage2_enabled = bool(entry.options.get(CONF_OLLAMA_URL, ""))
```
- Inserted AFTER `await coordinator._async_load_store()` and BEFORE `await coordinator.async_config_entry_first_refresh()`
- Uses the lazy-import style matching existing `CONF_CONNECTION_TYPE` import pattern
- `CONF_OLLAMA_URL` added to the existing lazy import block

### config_flow.py — options kwarg added (1 kwarg)
```python
return self.async_create_entry(
    title=user_input[CONF_NAME],
    data={**self._data, CONF_API_KEY: user_input[CONF_API_KEY]},
    options={CONF_STAGE2_ENABLED: False},
)
```
- `CONF_STAGE2_ENABLED` imported from `.const` (alphabetical position)
- Error path (ParcelAppAuthError, ParcelAppTransientError) unchanged

## Test Deltas

### tests/test_init.py — 5 tests added
1. `test_stage2_enabled_pollstats_default` — PollStats() dataclass default is False
2. `test_stage2_enabled_false_when_options_empty` — empty entry.options → False
3. `test_stage2_enabled_true_when_ollama_url_set` — ollama_url set → True
4. `test_stage2_enabled_false_when_ollama_url_empty_string` — explicit empty → False
5. `test_stage2_v12_entry_no_ollama_url_loads_without_exception` — v1.2 entry doesn't crash

### tests/test_config_flow.py — 2 tests added + stub fix
1. `test_finish_seeds_stage2_enabled_false` — success path options == {"stage2_enabled": False}
2. `test_finish_seeds_stage2_enabled_auth_error_unchanged` — error path unchanged, no options key
- `_FakeAbstractOAuth2FlowHandler.async_create_entry` updated to accept `options=None` kwarg

## CFG-04 Acceptance Criteria

- v1.2-shaped entry (no `ollama_url` in options) loads without exception: CONFIRMED by Tests 4 + 5
- `coordinator.diagnostics.stage2_enabled is False` for v1.2 entry: CONFIRMED by Tests 1 + 4
- `coordinator.diagnostics.stage2_enabled is True` when ollama_url is non-empty: CONFIRMED by Test 2
- New entries created via `async_step_finish` have `options == {"stage2_enabled": False}`: CONFIRMED by config_flow Test 1
- Error path of `async_step_finish` unchanged: CONFIRMED by config_flow Test 2

## Deviations from Plan

### Auto-added: Phase 17 constants in const.py (Rule 3 — blocking dependency)

**Found during:** Task 1 setup
**Issue:** `CONF_OLLAMA_URL` and `CONF_STAGE2_ENABLED` were not yet present in `const.py` because Plan 01 (which adds them) is running in the same parallel wave. Per the plan's own note: "If Plan 01 has not landed at the moment this plan is executed (parallel wave 1 ordering), executors MUST add the same constants at the top of const.py."
**Fix:** Added all Phase 17 constants to `const.py` using the same names and values as specified in CONTEXT.md and PLAN.md.
**Files modified:** `custom_components/shop2parcel/const.py`
**Commit:** 147d462 (included with GREEN implementation)

### Auto-fixed: _FakeAbstractOAuth2FlowHandler.async_create_entry stub (Rule 1 — Bug)

**Found during:** Task 2 RED tests
**Issue:** The test stub's `async_create_entry` only accepted `title` and `data` kwargs. The real HA `async_create_entry` accepts an optional `options` kwarg. The new test asserting `result["options"]` required the stub to pass through the `options` argument.
**Fix:** Updated stub to accept `options=None` and include it in the returned dict when not None.
**Files modified:** `tests/test_config_flow.py`
**Commit:** 2cc5736 (part of RED commit)

## Verification Results

```
pytest tests/ -v --tb=short
447 passed, 1 skipped (live_ollama smoke test)

ruff check — All checks passed
ruff format --check — 5 files already formatted
mypy — not installed in this environment (ruff type checks substitute)
```

## Known Stubs

None. All data flows are wired; `stage2_enabled` is derived from real `entry.options` data.

## Threat Flags

No new threat surface introduced. Changes are:
- A bool field addition to an in-memory dataclass (no network exposure)
- A bool derivation from existing persisted options (no new trust boundary)
- A const dict `{"stage2_enabled": False}` seeded at entry creation (non-secret, as per T-17-02-02 accept disposition)

All three STRIDE mitigations in the plan's threat register are implemented:
- T-17-02-01: `bool(entry.options.get(CONF_OLLAMA_URL, ""))` — empty string fallback prevents AttributeError
- T-17-02-02: stage2_enabled is non-secret boolean; surfaced via existing diagnostics (accept)
- T-17-02-03: stage2_enabled set only in two code paths; no user form field maps to it

## Self-Check: PASSED

Files created/modified:
- [FOUND] custom_components/shop2parcel/const.py
- [FOUND] custom_components/shop2parcel/coordinator.py
- [FOUND] custom_components/shop2parcel/__init__.py
- [FOUND] custom_components/shop2parcel/config_flow.py
- [FOUND] tests/test_init.py
- [FOUND] tests/test_config_flow.py

Commits verified:
- [FOUND] bfe000a — test(17-02): add RED tests for PollStats.stage2_enabled
- [FOUND] 147d462 — feat(17-02): add PollStats.stage2_enabled and derive it in async_setup_entry
- [FOUND] 2cc5736 — test(17-02): add RED tests for async_step_finish stage2_enabled options seed
- [FOUND] 1154d53 — feat(17-02): seed options={CONF_STAGE2_ENABLED: False} in async_step_finish
