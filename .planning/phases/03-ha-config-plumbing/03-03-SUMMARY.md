---
phase: 03-ha-config-plumbing
plan: 03
subsystem: auth
tags: [oauth2, homeassistant, config-flow, gmail, parcelapp, google-api]

# Dependency graph
requires:
  - phase: 02-api-clients
    provides: ParcelAppClient.async_get_deliveries, ParcelAppAuthError, ParcelAppTransientError, DOMAIN constant

provides:
  - OAuth2FlowHandler subclassing AbstractOAuth2FlowHandler with full config flow logic
  - async_oauth_create_entry: Gmail email extraction via executor + unique_id dedup
  - async_step_finish: parcelapp API key validation via async_get_deliveries()
  - async_step_reauth + async_step_reauth_confirm: reauth path re-running OAuth2 only
  - extra_authorize_data: access_type=offline + prompt=consent guaranteeing refresh token
  - TDD test suite for config_flow (15 tests) with sys.modules mocking of HA + Google libs

affects:
  - 03-04-PLAN (conftest.py and test_init.py build on same mock pattern)
  - 04-coordinator (Phase 4 raises ConfigEntryAuthFailed triggering reauth path)
  - application_credentials.py must reference same DOMAIN and scopes

# Tech tracking
tech-stack:
  added: []
  patterns:
    - sys.modules mock at import time for HA framework isolation in minimal venv
    - TDD with fake base class __init_subclass__ accepting domain keyword argument
    - Inner sync function _get_profile() wrapped in async_add_executor_job for blocking google-api call
    - Exception-type-only catch (no message logging) for T-03-03-01 threat mitigation

key-files:
  created:
    - custom_components/shop2parcel/config_flow.py
    - tests/test_config_flow.py
  modified: []

key-decisions:
  - "async_step_finish (not async_step_creation) used as custom parcelapp step — async_step_creation is reserved by AbstractOAuth2FlowHandler"
  - "Gmail profile fetch wrapped in executor via async_add_executor_job — google-api call is synchronous"
  - "Tests mock HA and Google APIs via sys.modules at module load — pytest-homeassistant-custom-component unavailable (network-blocked RPi)"
  - "_FakeAbstractOAuth2FlowHandler.__init_subclass__ accepts domain keyword — required for OAuth2FlowHandler class declaration"

patterns-established:
  - "Pattern: sys.modules HA mock at test module top level for config_flow tests"
  - "Pattern: _make_handler() factory creates OAuth2FlowHandler via __new__ + fake base __init__ for unit testing"

requirements-completed: [CONF-01, CONF-02, CONF-03, CONF-04, CONF-05, CONF-07]

# Metrics
duration: 18min
completed: 2026-04-25
---

# Phase 03 Plan 03: Config Flow Summary

**OAuth2FlowHandler with Gmail executor fetch, parcelapp validation via async_get_deliveries, and reauth path; 15 passing TDD tests using sys.modules HA mocking**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-04-25T00:00:00Z
- **Completed:** 2026-04-25T00:18:00Z
- **Tasks:** 1 (TDD: RED + GREEN commits)
- **Files modified:** 2

## Accomplishments

- config_flow.py implements OAuth2FlowHandler with all required methods and no async_step_creation override
- extra_authorize_data forces refresh token issuance on every consent (access_type=offline + prompt=consent)
- Gmail profile fetch runs in executor via async_add_executor_job (synchronous google-api call guarded from event loop)
- async_set_unique_id(email) + _abort_if_unique_id_configured() prevents duplicate config entries (T-03-03-04)
- async_step_finish validates parcelapp API key via async_get_deliveries() and maps ParcelAppAuthError/TransientError to UI error keys
- Reauth flow re-runs OAuth2 only without asking for parcelapp key again (D-11)
- 15 TDD tests covering all behavioral specifications; run entirely without HA or google-api installed

## Task Commits

Each task was committed atomically:

1. **RED: Failing tests for OAuth2FlowHandler** - `f0adab8` (test)
2. **GREEN: Implement config_flow.py** - `ff3d9a3` (feat)

**Plan metadata:** (docs commit follows)

_Note: TDD task has two commits — test (RED) then feat (GREEN)._

## Files Created/Modified

- `custom_components/shop2parcel/config_flow.py` — OAuth2FlowHandler with all config flow methods
- `tests/test_config_flow.py` — 15 TDD tests with sys.modules HA + Google mocking

## Decisions Made

- Used `async_step_finish` (not `async_step_creation`) as the custom parcelapp step name. `async_step_creation` is reserved by `AbstractOAuth2FlowHandler` for OAuth2 token exchange — overriding it would break the framework flow.
- Tests mock HA framework and Google libraries via `sys.modules.setdefault()` at module load time. The dev environment is a network-blocked Raspberry Pi; `pytest-homeassistant-custom-component` is unavailable via PyPI. The existing test suite (test_gmail_client.py) uses the same pattern successfully.
- `_FakeAbstractOAuth2FlowHandler.__init_subclass__` accepts `domain` keyword argument. Python raises `TypeError: __init_subclass__() takes no keyword arguments` when the class keyword `domain=DOMAIN` is passed to a base class without this hook — the fake base required this fix.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] _FakeAbstractOAuth2FlowHandler missing __init_subclass__ for domain keyword**

- **Found during:** Task 1 (GREEN phase — first test run after writing config_flow.py)
- **Issue:** `TypeError: OAuth2FlowHandler.__init_subclass__() takes no keyword arguments` — the fake base class did not accept the `domain=DOMAIN` class keyword that the real `AbstractOAuth2FlowHandler` requires
- **Fix:** Added `__init_subclass__(cls, domain: str = "", **kwargs)` to `_FakeAbstractOAuth2FlowHandler` in the test file
- **Files modified:** tests/test_config_flow.py
- **Verification:** All 15 tests collect and pass
- **Committed in:** ff3d9a3 (test file updated alongside implementation)

---

**Total deviations:** 1 auto-fixed (Rule 3 — blocking issue in test infrastructure)
**Impact on plan:** Minimal — test infrastructure fix only. Implementation matches plan specification exactly.

## Issues Encountered

- `pytest-homeassistant-custom-component` unavailable (network-blocked Raspberry Pi dev environment, confirmed by STATE.md note about aiohttp/aioresponses symlinked from sibling venv). Adopted sys.modules mocking pattern consistent with test_gmail_client.py. All 15 tests pass without real HA installation.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced beyond what the plan's `<threat_model>` already covers. All five STRIDE threats (T-03-03-01 through T-03-03-05) are mitigated:

- T-03-03-01: api_key never logged — exceptions caught by type only
- T-03-03-02: access_token/refresh_token never logged — _get_profile() uses token only for API call
- T-03-03-03: CSRF in OAuth2 redirect — accepted (HA framework handles state parameter)
- T-03-03-04: Duplicate entry prevention via async_set_unique_id + _abort_if_unique_id_configured
- T-03-03-05: Scope locked to gmail.readonly only in extra_authorize_data

## Known Stubs

None — config_flow.py contains no placeholder values, hardcoded empty collections, or TODO markers that affect plan goal delivery.

## User Setup Required

None — no external service configuration required for this plan.

## Next Phase Readiness

- config_flow.py is complete and ready for Phase 4 coordinator integration
- ConfigEntryAuthFailed raised in Phase 4 coordinator will trigger the reauth path implemented here
- async_step_reauth → async_step_reauth_confirm → async_step_user flow is tested and committed
- Remaining Phase 3 plans (03-04) can proceed: strings.json, translations/en.json, __init__.py, manifest.json, conftest.py, test_init.py

---
*Phase: 03-ha-config-plumbing*
*Completed: 2026-04-25*
