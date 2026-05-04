---
phase: 09-imap-multi-account
plan: "03"
subsystem: config-flow
tags: [imap, config-flow, multi-account, connection-type-picker, reauth, strings]
dependency_graph:
  requires:
    - 09-01 (xfail test stubs for IMAP config flow)
    - 09-02 (ImapClient, IMAP constants, ImapAuthError/ImapTransientError)
  provides:
    - async_step_user connection type picker (gmail/imap)
    - async_step_imap IMAP credential collection and connection test
    - async_step_reauth branching on connection_type
    - async_step_reauth_imap IMAP reauth credential update
    - strings.json IMAP UI strings (user, imap, reauth_confirm_imap steps)
  affects:
    - custom_components/shop2parcel/config_flow.py
    - custom_components/shop2parcel/strings.json
tech_stack:
  added: []
  patterns:
    - async_step_user override with connection_type picker before Gmail OAuth2 redirect
    - IMAP connection test in executor before storing credentials (security pattern)
    - unique_id set only after successful connection test (prevents stale unique_id registration)
    - credentials stored in entry.data never entry.options (HA encryption)
    - password field never pre-filled in reauth form (T-09-03-02)
    - async_step_reauth branching pattern for multi-connection-type entries
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/config_flow.py
    - custom_components/shop2parcel/strings.json
decisions:
  - D-01: async_step_user override injects connection_type picker before Gmail OAuth2 redirect
  - D-02: async_step_imap collects host/port/username/password/tls_mode in one form; port defaults to 993 (SSL)
  - D-03: unique_id set to username@host after successful connection test only
  - D-04: async_step_reauth branches on connection_type; IMAP entries routed to async_step_reauth_imap
  - D-12: Entry title is 'Shop2Parcel (username@host)' for IMAP entries
metrics:
  duration: "~5 minutes"
  completed: "2026-05-03T00:04:32Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase 09 Plan 03: IMAP Config Flow Steps Summary

IMAP config flow integration: config_flow.py extended with async_step_user connection-type picker, async_step_imap credential collection + connection test, async_step_reauth branching on connection_type, and async_step_reauth_imap for IMAP reauth; strings.json updated with user/imap/reauth_confirm_imap steps, invalid_auth/imap_cannot_connect errors, and already_configured_imap abort. All 6 IMAP config flow stubs from Plan 09-01 promoted from xfail to xpass; 147 tests passing, 1 xfailed, 16 xpassed — no regressions.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend config_flow.py with IMAP flow steps | 0dad08b | custom_components/shop2parcel/config_flow.py |
| 2 | Update strings.json with IMAP config flow UI strings | a85c73d | custom_components/shop2parcel/strings.json |

## Verification Results

- `pytest tests/test_config_flow.py -k "imap or user" -x -v` — 1 passed, 6 xpassed (all IMAP stubs promoted)
- `pytest tests/test_config_flow.py -x` — 16 passed, 6 xpassed (no regressions in Gmail tests)
- `.venv/bin/python -c "import json; json.load(open('strings.json'))"` — Valid JSON
- `grep -c "async_step_imap" config_flow.py` — 4 (definition, call from reauth, 2 call sites)
- `grep -c "async_step_reauth_imap" config_flow.py` — 2 (definition + call from reauth)
- `pytest` (full suite) — 147 passed, 1 xfailed, 16 xpassed — exit 0

## Deviations from Plan

None — plan executed exactly as written.

Note: The `grep -c "super().async_step_user()"` acceptance criterion says "returns 1" but the actual count is 3 because the docstring of `async_step_user` mentions the call pattern twice. The actual functional call is on one line (line 123). This does not affect correctness — the behavior is correct.

## Threat Mitigations Applied (from plan threat model)

All five threat register entries mitigated as specified:

- **T-09-03-01**: IMAP password stored only in `self._data` then passed to `async_create_entry(data=...)` which HA stores encrypted; exceptions caught by type only, never by message content
- **T-09-03-02**: `CONF_IMAP_PASSWORD` field in `async_step_reauth_imap` has no `default=` argument — password is never pre-filled
- **T-09-03-03**: `CONF_IMAP_TLS` defaults to `"ssl"` (port 993) in both forms; user must explicitly choose "starttls" or "none"
- **T-09-03-04**: IMAP credentials stored in `entry.data` (encrypted), never in `entry.options`
- **T-09-03-05**: `async_set_unique_id(f"{username}@{host}")` called AFTER successful connection test; `_abort_if_unique_id_configured()` prevents duplicate entries

## Known Stubs

None — all implementation is complete and functional.

## Threat Surface Scan

No new network endpoints introduced. async_step_imap makes one outbound IMAP connection (via ImapClient executor call) to validate credentials before storing them. This connection test pattern is documented in the plan's threat model (T-09-03-01 through T-09-03-05) — all dispositions are `mitigate` with implemented mitigations above.

## Self-Check: PASSED

Files verified to exist:
- custom_components/shop2parcel/config_flow.py — FOUND
- custom_components/shop2parcel/strings.json — FOUND

Commits verified:
- 0dad08b — FOUND
- a85c73d — FOUND
