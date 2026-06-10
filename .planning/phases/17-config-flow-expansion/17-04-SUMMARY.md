---
phase: 17-config-flow-expansion
plan: "04"
subsystem: options-flow
tags: [options-flow, custom-fields, ollama, config, tdd, fld-01, fld-02]
dependency_graph:
  requires: [17-01, 17-03]
  provides: [custom-fields-crud, options-flow-complete]
  affects: [options_flow, strings_json, en_json]
tech_stack:
  added: []
  patterns:
    - HA MenuFlow sub-menu (async_show_menu step_id=custom_fields_menu)
    - _FIELD_NAME_RE imported from ollama_extractor (no inline duplication)
    - vol.In selector for remove step
    - In-memory options mutation (read fresh → mutate → async_create_entry)
    - TDD RED/GREEN with pytest-homeassistant-custom-component
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/options_flow.py
    - custom_components/shop2parcel/strings.json
    - custom_components/shop2parcel/translations/en.json
    - tests/test_options_flow.py
    - tests/test_translations.py
decisions:
  - "async_step_custom_fields replaced with real sub-menu; remove_custom_field hidden when empty"
  - "_FIELD_NAME_RE imported from ollama_extractor; no inline re.compile duplication"
  - "locked-name collision is field-scoped (errors[CONF_FIELD_NAME]), not errors['base']"
  - "empty/absent description normalized to None before storage (Pitfall 5)"
  - "options.abort block removed entirely; not_implemented key no longer reachable"
metrics:
  duration_minutes: 10
  completed: "2026-06-10"
  tasks_completed: 2
  files_modified: 5
  files_created: 0
---

# Phase 17 Plan 04: Custom Fields CRUD Summary

**One-liner:** Custom-fields CRUD menu with regex + locked-field validation; async_step_add_custom_field stores {name, description-or-None}; remove step uses vol.In selector; Plan 03 stub and not_implemented abort removed.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 (RED) | 16 failing tests for custom-fields CRUD menu + add/remove steps | 951a823 |
| 1 (GREEN) | Replace stub with real CRUD: custom_fields_menu + add + remove steps | f8f3acc |
| 2 (RED) | 7 failing tests for custom-fields translation keys + abort removal | 8c99ef2 |
| 2 (GREEN) | Update strings.json + en.json; remove not_implemented abort | 7514092 |

## What Was Built

### options_flow.py Changes

**Methods added/changed:**
- `async_step_custom_fields` — REWRITTEN: was `async_abort(reason="not_implemented")` stub; now returns `async_show_menu(step_id="custom_fields_menu")` with dynamic menu_options and current_fields placeholder
- `async_step_add_custom_field` — NEW: validates name via `_FIELD_NAME_RE.fullmatch`, rejects LOCKED_OLLAMA_FIELDS collisions with field-scoped errors, normalizes description to None, appends `{name, description}` to options and calls `async_create_entry`
- `async_step_remove_custom_field` — NEW: `vol.In(existing_names)` selector, removes matching entry from list, calls `async_create_entry`

**New constants imported (3 new from const.py):**
`CONF_CUSTOM_FIELDS`, `CONF_FIELD_NAME`, `CONF_FIELD_DESCRIPTION`

**New top-level imports:**
- `from .extractors.ollama_extractor import _FIELD_NAME_RE` (single source of truth for field name regex)

**Removed from options_flow.py:**
- `async_abort(reason="not_implemented")` — no longer reachable

### strings.json + translations/en.json Changes

**Structural changes:**
- `options.step.custom_fields_menu` — NEW step with `{current_fields}` placeholder in description + `menu_options` (add_custom_field, remove_custom_field)
- `options.step.add_custom_field` — NEW step with title, data (field_name, field_description), data_description (format hint, AI hint)
- `options.step.remove_custom_field` — NEW step with title + data.field_name
- `options.error` — EXTENDED: two new keys `invalid_field_name`, `locked_field_collision` (4 total alongside Plan 03's two keys)
- `options.abort` — REMOVED entirely (not_implemented stub gone; block was its only entry)
- Byte-identical to `translations/en.json` (confirmed via `cmp -s`)

### Test Deltas

**test_options_flow.py:** 18 changes (16 new Plan 04 tests + 1 stub test retargeted + ruff format):
- `test_custom_fields_returns_menu` — retargeted from stub abort assertion to menu assertion
- `test_custom_fields_menu_empty` — Test 1: empty options → menu_options=["add_custom_field"], placeholder="none"
- `test_custom_fields_menu_with_existing` — Test 2: non-empty → includes remove_custom_field
- `test_add_custom_field_shows_form` — Test 3: form shape
- `test_add_custom_field_happy_path` — Test 4: FLD-02 happy path
- `test_add_custom_field_appends_to_existing` — Test 5: append to existing list
- `test_add_custom_field_empty_description_normalized_to_none` — Test 6: "" → None
- `test_add_custom_field_absent_description_normalized_to_none` — Test 7: missing key → None
- `test_add_custom_field_locked_tracking_number/carrier_name/order_name` — Tests 8a-c: FLD-01
- `test_add_custom_field_invalid_uppercase/space/leading_digit/too_long` — Tests 9-12: FLD-02
- `test_remove_custom_field_shows_selector` — Test 13: vol.In shape
- `test_remove_custom_field_happy_path` — Test 14: FLD-02 remove
- `test_add_custom_field_stateless_across_calls` — Test 15: Pitfall 2 stateless
- `test_add_custom_field_locked_collision_is_field_scoped` — Test 16: field-scoped error

**tests/test_translations.py:** 8 new tests + 1 updated (abort test):
- `test_strings_abort_not_implemented_removed` — updated: now asserts key is absent
- `test_custom_fields_menu_keys` — Plan 04 Test 1 (was RED gate)
- `test_custom_fields_menu_description_placeholder` — {current_fields} in description
- `test_add_custom_field_data_keys` — field_name + field_description keys present
- `test_remove_custom_field_data_key` — field_name key present
- `test_new_error_keys_present` — 4 error keys total
- `test_not_implemented_abort_absent` — abort block gone
- `test_en_json_identical_to_strings` — byte-identical sync check
- `test_plan03_keys_preserved` — regression: Plan 03 keys preserved

**Total test count: 500 passing, 1 skipped** (was 474 at phase start; +26 net new tests)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Retargeted stale test_custom_fields_stub_returns_abort test**
- **Found during:** Task 1 GREEN run
- **Issue:** `test_custom_fields_stub_returns_abort` (added in Plan 03) asserted `result["type"] == "abort"`. After replacing the stub with a real sub-menu, this test fails correctly — Plan 03's stub behavior is intentionally removed.
- **Fix:** Renamed to `test_custom_fields_returns_menu` and updated assertion to `result["type"] == "menu"`.
- **Files modified:** `tests/test_options_flow.py`
- **Commit:** f8f3acc (included in GREEN commit)

## Phase Requirement Coverage Confirmation

All 9 Phase 17 requirement IDs implemented:

| Requirement | Plan | Implementation |
|-------------|------|----------------|
| OLLM-01 (URL) | 17-03 | CONF_OLLAMA_URL in settings form + async_get_tags validation |
| OLLM-02 (model) | 17-03 | CONF_OLLAMA_MODEL with DEFAULT_OLLAMA_MODEL |
| OLLM-03 (timeout) | 17-03 | CONF_OLLAMA_TIMEOUT vol.Range(10, 300) |
| FLD-01 (locked-field enforcement) | 17-04 | LOCKED_OLLAMA_FIELDS collision check in add step |
| FLD-02 (custom fields CRUD) | 17-04 | add + remove steps + in-memory options mutation |
| CFG-01 (URL reachability) | 17-03 | OllamaTransientError → errors["base"] = "ollama_cannot_connect" |
| CFG-02 (model existence) | 17-03 | model not in /api/tags → errors["base"] = "ollama_model_not_found" |
| CFG-03 (disclosure text) | 17-03 | locked_fields placeholder in settings description |
| CFG-04 (v1.2 backward compat) | 17-02 | stage2_enabled seeded False in config_flow |
| QUE-01 (queue maxlen) | 17-03 | CONF_QUEUE_MAXLEN vol.Range(1, 256) |

## Source Assertion Verification

| Assertion | Expected | Actual |
|-----------|----------|--------|
| `_FIELD_NAME_RE.fullmatch` usages in options_flow.py | 1 | 1 |
| `re.compile` usages in options_flow.py | 0 | 0 |
| Error assignments (invalid_field_name, locked_field_collision) | 2 | 2 |
| `_FIELD_NAME_RE` import present | 1 | 1 |
| `async_abort` usages | 0 | 0 |
| strings.json error keys | 4 | 4 |
| strings.json / en.json byte-identical | True | True |
| not_implemented absent | True | True |

## TDD Gate Compliance

- RED gate (task 1): `test_custom_fields_menu_empty` failed before implementation (confirmed — abort returned)
- GREEN gate (task 1): all 40 options_flow tests pass after implementation
- RED gate (task 2): `test_custom_fields_menu_keys` + 6 other translation tests failed before edits (confirmed)
- GREEN gate (task 2): all 15 translation tests pass after edits

## Known Stubs

None — all stubs from Plan 03 replaced. The `async_step_custom_fields` stub and `options.abort.not_implemented` are both removed.

## Self-Check: PASSED

Files exist:
- custom_components/shop2parcel/options_flow.py — FOUND
- custom_components/shop2parcel/strings.json — FOUND
- custom_components/shop2parcel/translations/en.json — FOUND
- tests/test_options_flow.py — FOUND
- tests/test_translations.py — FOUND

Commits exist:
- 951a823 — FOUND (test RED for CRUD menu)
- f8f3acc — FOUND (feat GREEN CRUD implementation)
- 8c99ef2 — FOUND (test RED for translation keys)
- 7514092 — FOUND (feat GREEN translation JSON)

Full suite: 500 passed, 1 skipped (verified)
