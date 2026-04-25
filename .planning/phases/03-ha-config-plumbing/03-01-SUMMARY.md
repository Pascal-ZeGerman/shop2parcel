---
phase: 03-ha-config-plumbing
plan: "01"
subsystem: test-harness
tags: [testing, pytest, homeassistant, fixtures, scaffolding]
dependency_graph:
  requires: []
  provides: [test-fixtures, ha-test-harness]
  affects: [03-02, 03-03, 03-04]
tech_stack:
  added: [pytest-homeassistant-custom-component==0.13.316, annotatedyaml==1.0.2, homeassistant==2026.2.3]
  patterns: [MockConfigEntry fixture, xfail scaffold tests, pytest-homeassistant-custom-component hass fixture]
key_files:
  created:
    - tests/conftest.py
    - tests/test_init.py
  modified:
    - pyproject.toml
decisions:
  - "Preserved existing tests/test_config_flow.py from 03-03 wave (14 tests against real implementation) rather than replacing with 8 xfail scaffolds"
  - "Used --no-deps + --ignore-installed pattern to resolve symlink conflicts from sibling VW-CarNet venv"
  - "Converted propcache/aiohttp/frozenlist/multidict/yarl/attrs symlinks to real copies to unblock pip install"
metrics:
  duration_minutes: 23
  tasks_completed: 2
  files_created: 2
  files_modified: 1
  completed_date: "2026-04-25"
---

# Phase 03 Plan 01: HA Test Harness Installation and Test Scaffolds Summary

Install pytest-homeassistant-custom-component into .venv and create test/conftest.py fixtures plus test_init.py xfail scaffolds for async_setup_entry/unload coverage.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Install pytest-homeassistant-custom-component | 5665f55 | pyproject.toml |
| 2 | Create test scaffolds — conftest.py, test_init.py | a3b6bbf | tests/conftest.py, tests/test_init.py |

## Verification Results

- `.venv/bin/python -c "import pytest_homeassistant_custom_component"` exits 0
- `.venv/bin/python -c "from pytest_homeassistant_custom_component.common import MockConfigEntry"` exits 0
- `.venv/bin/pytest tests/test_config_flow.py tests/test_init.py --collect-only -q` exits 0, shows 19 collected
- `tests/conftest.py` defines `mock_config_entry` fixture with `data["api_key"] == "test-parcelapp-key"` and `unique_id == "user@gmail.com"`
- `tests/test_init.py` contains 4 xfail async test functions

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Resolved symlinked dependency conflict preventing pip install**
- **Found during:** Task 1
- **Issue:** The project .venv had aiohttp, propcache, multidict, yarl, frozenlist, attrs, aioresponses, aiohappyeyeballs, and aiosignal symlinked from a sibling VW-CarNet project venv. When pip tried to upgrade propcache (required by homeassistant), it failed with OSError 30 (read-only file system) because the symlink targets were considered "outside environment"
- **Fix:** Converted all 9 symlinks to real copies of the directories using `cp -rp`. Then ran full install successfully.
- **Files modified:** .venv/lib/python3.13/site-packages/{propcache,aiohttp,aiohappyeyeballs,aiosignal,frozenlist,multidict,yarl,attrs,aioresponses}
- **Commit:** 5665f55

**2. [Rule 2 - Missing critical functionality] Preserved existing test_config_flow.py from 03-03**
- **Found during:** Task 2
- **Issue:** Plan 03-03 (config_flow implementation) had already run in a parallel worktree and committed tests/test_config_flow.py with 14 tests that test the actual OAuth2FlowHandler implementation via sys.modules mocking. Plan 03-01 specified creating 8 xfail scaffold tests at the same path.
- **Fix:** Kept the existing 14-test file (which covers CONF-01 through CONF-07 with real assertions against the implementation). Created only conftest.py and test_init.py as new files. This preserves test coverage and avoids regressing 14 passing tests.
- **Files modified:** None (test_config_flow.py left as-is from 03-03)
- **Impact:** Collection shows 19 tests instead of the plan's expected 12. All plan requirements (CONF-01 through CONF-07 coverage, --collect-only exits 0) are still met.

## Known Stubs

None — test fixtures use fake-but-structurally-valid credential data, no production stubs introduced.

## Threat Flags

None — all API keys/tokens in test files are clearly fake literals (fake-access-token, test-parcelapp-key). No real credentials introduced.

## Self-Check

- [x] tests/conftest.py exists: FOUND
- [x] tests/test_init.py exists: FOUND
- [x] pyproject.toml updated: FOUND (contains pytest-homeassistant-custom-component)
- [x] Commit 5665f55 exists: FOUND
- [x] Commit a3b6bbf exists: FOUND

## Self-Check: PASSED
