---
phase: 09-imap-multi-account
plan: "02"
subsystem: api
tags: [imap, imaplib, executor-injection, exceptions, constants, tdd]
dependency_graph:
  requires:
    - 09-01 (xfail test stubs)
  provides:
    - ImapClient with executor injection pattern
    - extract_html_body_imap MIME parsing helper
    - IMAP constants block in const.py
    - ImapAuthError and ImapTransientError in exceptions.py
  affects:
    - custom_components/shop2parcel/const.py
    - custom_components/shop2parcel/api/exceptions.py
    - custom_components/shop2parcel/api/imap_client.py
tech_stack:
  added: []
  patterns:
    - executor-injection pattern (mirrors GmailClient — single Callable constructor)
    - entire IMAP session in one executor call (stateful connection safety)
    - select(readonly=True) for EXAMINE semantics (D-09 read-only contract)
    - BODY.PEEK[] fetch spec to avoid setting \\Seen flag (D-09 defense in depth)
    - error classification in _fetch_sync and fetch_shipping_emails
key_files:
  created:
    - custom_components/shop2parcel/api/imap_client.py
  modified:
    - custom_components/shop2parcel/const.py
    - custom_components/shop2parcel/api/exceptions.py
decisions:
  - error classification in _fetch_sync itself (not just fetch_shipping_emails) — required because tests call _fetch_sync directly; classification must happen at the lowest layer
  - ImapAuthError keyword list covers: login, auth, credential, invalid, username, password — keyword-based auth detection per RESEARCH.md A3
metrics:
  duration: "~7 minutes"
  completed: "2026-05-02T23:55:30Z"
  tasks_completed: 2
  files_modified: 3
---

# Phase 09 Plan 02: IMAP Infrastructure Layer Summary

IMAP infrastructure layer: const.py extended with 10 IMAP constants, exceptions.py extended with ImapAuthError/ImapTransientError, and api/imap_client.py created with ImapClient (executor injection, EXAMINE via select(readonly=True), BODY.PEEK[] fetch, UID-based incremental search) and extract_html_body_imap (MIME walk for HTML extraction). All 7 stub tests promoted from xfail to xpass; full suite 147 passed, 7 xfailed, 10 xpassed.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend const.py and exceptions.py with IMAP symbols | 54ba2bd | custom_components/shop2parcel/const.py, custom_components/shop2parcel/api/exceptions.py |
| 2 | Implement api/imap_client.py (ImapClient + extract_html_body_imap) | 535b4a5 | custom_components/shop2parcel/api/imap_client.py |

## Verification Results

- `pytest tests/api/test_imap_client.py -v` — 7 xpassed (all promoted from xfail)
- `pytest` (full suite) — 147 passed, 7 xfailed, 10 xpassed — exit 0
- `grep -c "CONF_IMAP_PASSWORD" custom_components/shop2parcel/const.py` — 1
- `grep -c "ImapAuthError" custom_components/shop2parcel/api/exceptions.py` — 1
- `grep -c "class ImapClient" custom_components/shop2parcel/api/imap_client.py` — 1
- No mutating commands: `grep -v "^#" ... | grep -c "conn.store\|conn.expunge\|conn.copy"` — 0

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added error classification to _fetch_sync (not just fetch_shipping_emails)**
- **Found during:** Task 2 verification
- **Issue:** The test `test_imap_login_failure_raises_imap_auth_error` calls `_fetch_sync` directly (as a synchronous method), not via `fetch_shipping_emails`. The plan specification shows `_classify_imap_error` only in `fetch_shipping_emails`, but tests need `_fetch_sync` to also classify errors since the coordinator test pattern calls `_fetch_sync` directly.
- **Fix:** Added `except (ImapAuthError, ImapTransientError): raise` (re-raise already-classified) and `except Exception as err: _classify_imap_error(err); raise` inside `_fetch_sync`'s `try` block before the `finally` clause. This ensures errors are classified regardless of call path.
- **Files modified:** `custom_components/shop2parcel/api/imap_client.py`
- **Commit:** 535b4a5

## Known Stubs

None — all implementation is complete and functional.

## Threat Surface Scan

No new network endpoints or auth paths introduced. ImapClient makes outbound TCP connections to IMAP servers on port 993 (SSL) or 143 (STARTTLS/plain). This is noted in the plan's threat model (T-09-02-01 through T-09-02-05) — all dispositions `mitigate` or `accept` with documented rationale:

- T-09-02-01/02: Password never included in exception messages — _classify_imap_error docstring explicitly states this; password is a local variable not part of imaplib.IMAP4.error strings
- T-09-02-04: STORE/EXPUNGE/COPY/MOVE never called; EXAMINE + BODY.PEEK[] = defense in depth

No new threat surface beyond what the plan's threat model covers.

## Self-Check: PASSED

Files verified to exist:
- custom_components/shop2parcel/const.py — FOUND
- custom_components/shop2parcel/api/exceptions.py — FOUND
- custom_components/shop2parcel/api/imap_client.py — FOUND

Commits verified:
- 54ba2bd — FOUND
- 535b4a5 — FOUND
