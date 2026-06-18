---
phase: 16-ollamaextractor-schema-composition
plan: 01
subsystem: extractors
tags: [scaffolding, value-type, frozen-dataclass, tdd, fixtures]
dependency_graph:
  requires:
    - custom_components/shop2parcel/api/ollama_client.py (Phase 15 — OllamaClient class spec-bound by mock_client fixture)
    - custom_components/shop2parcel/api/email_parser.py (Phase 2/8 — ShipmentData type used by sample_stage1 fixture)
  provides:
    - custom_components/shop2parcel/extractors/types.py::Stage2Result (consumed by Phases 18/20/21)
    - tests/extractors/conftest.py fixtures: mock_client, sample_stage1, shopify_mini_html (consumed by Plans 16-02 / 16-03)
  affects:
    - none (pure addition — no existing code modified)
tech_stack:
  added: []
  patterns:
    - "Frozen, slotted dataclass for value types (matches ShipmentData / ParseResult)"
    - "Empty package marker for subpackages (matches tests/api/__init__.py)"
    - "AsyncMock(spec=<class>) for cross-layer mocking (matches test_ollama_client.py fixture style, promoted to conftest)"
    - "Inline-string HTML fixtures, not file fixtures (matches tests/api/test_email_parser.py pattern for compact cases)"
    - "No HA imports in extractors/ — extends the api/ no-HA-imports discipline (D-01/D-03)"
key_files:
  created:
    - custom_components/shop2parcel/extractors/__init__.py
    - custom_components/shop2parcel/extractors/types.py
    - tests/extractors/__init__.py
    - tests/extractors/conftest.py
    - tests/extractors/test_types.py
  modified: []
decisions:
  - "D-04 confirmed verbatim: Stage2Result fields in CONTEXT.md order — locked: dict[str, str | None], custom: dict[str, str | None], passes_used: int, latency_ms: float. All required (no defaults)."
  - "extractors/__init__.py created as truly empty (0 bytes) — matches tests/api/__init__.py rather than the docstring-only custom_components/shop2parcel/api/__init__.py. Plan acceptance criterion required 'empty file' and the empty form keeps it accidental-import-free."
metrics:
  duration_minutes: 7
  completed_date: 2026-06-09
---

# Phase 16 Plan 01: extractors/ subpackage + Stage2Result Summary

Shipped the new `extractors/` subpackage scaffolding and the `Stage2Result` frozen, slotted dataclass that Phases 16/18/20/21 will all import — plus the shared `tests/extractors/conftest.py` (mock OllamaClient, sample ShipmentData, mini Shopify HTML) that Plans 02 and 03 will consume — under strict TDD with verified RED → GREEN cycles.

## Files Created

| File | Role | Lines |
|------|------|-------|
| `custom_components/shop2parcel/extractors/__init__.py` | Empty package marker | 0 |
| `custom_components/shop2parcel/extractors/types.py` | Stage2Result frozen dataclass (D-04) | 47 |
| `tests/extractors/__init__.py` | Empty test-package marker | 0 |
| `tests/extractors/conftest.py` | Shared fixtures (mock_client, sample_stage1, shopify_mini_html) | 63 |
| `tests/extractors/test_types.py` | Stage2Result + fixture invariant tests (7 tests) | 101 |

## Commits (TDD Sequence)

| # | Hash | Type | Description |
|---|------|------|-------------|
| 1 | `5de0c41` | test (RED) | Failing Stage2Result invariant tests — ModuleNotFoundError confirmed RED |
| 2 | `204ebda` | feat (GREEN) | extractors/ subpackage marker + Stage2Result frozen dataclass — 4 tests pass |
| 3 | `3f21cb6` | test (RED) | Failing fixture-consumer tests for conftest — 3 fixture-not-found errors confirmed RED |
| 4 | `8a04a37` | feat (GREEN) | tests/extractors/conftest.py with mock_client + sample_stage1 + shopify_mini_html — 7 tests pass |

Task 3 (lint + format + type-check gate) produced no file changes — all three CI gates passed on the first run, so no auto-fix commit was needed. The gate verification output (ruff check, ruff format --check, mypy) is captured in the Verification Results section below.

## D-04 Confirmation

The Stage2Result field order and types match `16-CONTEXT.md` §D-04 exactly:

```python
@dataclass(frozen=True, slots=True)
class Stage2Result:
    locked: dict[str, str | None]
    custom: dict[str, str | None]
    passes_used: int
    latency_ms: float
```

- All four fields are required (no defaults) — Phase 18's `Stage2Job` is the contract owner for defaults.
- PEP 604 unions throughout (`str | None`, never `Optional[str]`).
- `frozen=True` forbids attribute reassignment (verified by `test_stage2result_is_frozen_dataclass`).
- `slots=True` confirmed by `hasattr(instance, "__slots__")`.
- File-level docstring explicitly notes the in-place dict mutation caveat (T-16.01-01) and locks the "no HA imports" boundary.

## Fixture Confirmation (Plans 02 / 03 Consumers)

`tests/extractors/conftest.py` exposes exactly the three fixtures Plans 02 and 03 will consume:

| Fixture | Returns | Purpose |
|---------|---------|---------|
| `mock_client` | `AsyncMock(spec=OllamaClient)` | Spec-bound mock — attribute typos fail at construction. Plans 02/03 will set `mock_client.async_generate_with_metadata.return_value = ...` |
| `sample_stage1` | `ShipmentData(tracking_number="1Z999AA10123456784", carrier_name="UPS", order_name="#1234", message_id="msg-test-1", email_date=0)` | Satisfies `async_extract(html, stage1)` signature. D-01: not embedded in prompt. |
| `shopify_mini_html` | Inline HTML string with prose TN, "Tracking number:" label, and a tracking `<a href>` query string | Covers the prose / label / href signal paths Plan-02 `preprocess_html` and Plan-03 `build_prompt` rely on |

Verified by `tests/extractors/test_types.py::test_mock_client_fixture_is_spec_bound_to_ollama_client`, `::test_sample_stage1_is_real_shipment_data`, `::test_shopify_mini_html_fixture_contains_required_signals`.

## Verification Results

### Plan-level verification (post-Task-3)

```
$ .venv/bin/pytest tests/extractors/ -v --tb=short
============================== 7 passed in 0.52s ===============================

$ .venv/bin/pytest tests/ --tb=short -q
391 passed, 1 skipped in 9.67s
# (1 skip is the pre-existing 15-04 live_ollama smoke test)

$ .venv/bin/ruff check custom_components/shop2parcel/extractors/ tests/extractors/
All checks passed!

$ .venv/bin/ruff format --check custom_components/shop2parcel/extractors/ tests/extractors/
5 files already formatted

$ .venv/bin/python -m mypy custom_components/shop2parcel/extractors/
Success: no issues found in 2 source files

$ grep -r "homeassistant" custom_components/shop2parcel/extractors/
# (no output — clean)

$ .venv/bin/python -c "from custom_components.shop2parcel.extractors.types import Stage2Result"
# (succeeds)
```

### Acceptance grep counts (Task 2)

```
$ grep -c "AsyncMock(spec=OllamaClient)" tests/extractors/conftest.py
1
$ grep -c "ShipmentData(" tests/extractors/conftest.py
1
$ grep -c "<<<EMAIL>>>\|<<<LINKS>>>" tests/extractors/conftest.py
0
$ grep -c "enable_custom_integrations\|MockConfigEntry" tests/extractors/conftest.py
0
```

## Deviations from Plan

None against the **task-level** spec — all 5 files match PATTERNS.md verbatim and D-04 verbatim.

One **acceptance-criterion adjustment** during Task 2: an early draft of `tests/extractors/conftest.py` docstring mentioned the HA fixtures it intentionally avoids ("no MockConfigEntry, no enable_custom_integrations needed"). The plan's acceptance criterion `grep -c "enable_custom_integrations|MockConfigEntry" tests/extractors/conftest.py is 0` is a literal substring check, not an import check, so the docstring mention tripped it (count = 1 instead of 0). Rewrote the docstring to satisfy the literal criterion — semantic content unchanged (the conftest still imports zero HA fixtures). Committed as part of the same Task 2 GREEN commit; no separate fix commit.

Task 3 (lint + format + type-check gate) required no auto-fixes — all five new files passed `ruff check`, `ruff format --check`, and `mypy` on the first run. Per the executor rules ("If there are no changes to commit, do not create an empty commit"), no commit was created for Task 3; the gate-pass evidence is recorded above.

## TDD Gate Compliance

The plan's `tdd_discipline` block (RED → GREEN → REFACTOR for behavior-adding tasks) was honored for both Task 1 (Stage2Result) and Task 2 (conftest fixtures):

- **Task 1 RED** (`5de0c41`, `test(16-01)`): four invariant tests for the not-yet-existing `Stage2Result` — failed with `ModuleNotFoundError: No module named 'custom_components.shop2parcel.extractors'`.
- **Task 1 GREEN** (`204ebda`, `feat(16-01)`): created `extractors/__init__.py` + `extractors/types.py` — same four tests now pass.
- **Task 2 RED** (`3f21cb6`, `test(16-01)`): three fixture-consumer tests appended to `test_types.py` — failed with `fixture 'mock_client' not found` / `'sample_stage1' not found` / `'shopify_mini_html' not found`.
- **Task 2 GREEN** (`8a04a37`, `feat(16-01)`): created `tests/extractors/conftest.py` — same three tests now pass; combined `tests/extractors/test_types.py` suite is 7 / 7 passing.

No REFACTOR commits were necessary — both GREEN implementations are already minimal and idiomatic; no cleanup was required to keep tests passing.

## Threat Flags

None — Plan 01 introduces zero new external-input surfaces. `Stage2Result` is constructed only by trusted Phase-16/19 code, the conftest fixtures are test-only, and the package markers are empty. T-16.01-01 (in-place dict mutation) is documented in the `Stage2Result` class docstring per the threat-model plan; the mitigation discipline is owned by Phases 18/20/21 consumers.

## Self-Check

### Files

- FOUND: `custom_components/shop2parcel/extractors/__init__.py`
- FOUND: `custom_components/shop2parcel/extractors/types.py`
- FOUND: `tests/extractors/__init__.py`
- FOUND: `tests/extractors/conftest.py`
- FOUND: `tests/extractors/test_types.py`

### Commits

- FOUND: `5de0c41` — `test(16-01): add failing Stage2Result invariant tests (RED)`
- FOUND: `204ebda` — `feat(16-01): add extractors/ subpackage + Stage2Result frozen dataclass (GREEN)`
- FOUND: `3f21cb6` — `test(16-01): add failing fixture-consumer tests for conftest (RED)`
- FOUND: `8a04a37` — `feat(16-01): add tests/extractors/conftest.py with three Plan-02/03 fixtures (GREEN)`

## Self-Check: PASSED
