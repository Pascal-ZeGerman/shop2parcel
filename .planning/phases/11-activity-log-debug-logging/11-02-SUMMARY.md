---
phase: "11-activity-log-debug-logging"
plan: "02"
subsystem: debug-logging
tags: [debug-logging, _LOGGER, gmail-client, parcelapp, imap-client, email-parser, coordinator]
dependency_graph:
  requires: [11-01]
  provides: [DBG-01, DBG-02, DBG-03, DBG-04, DBG-05]
  affects:
    - custom_components/shop2parcel/api/gmail_client.py
    - custom_components/shop2parcel/api/parcelapp.py
    - custom_components/shop2parcel/api/imap_client.py
    - custom_components/shop2parcel/api/email_parser.py
    - custom_components/shop2parcel/coordinator.py
tech_stack:
  added: []
  patterns: [Python stdlib logging, _LOGGER = logging.getLogger(__name__), HA native debug toggle]
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/api/gmail_client.py
    - custom_components/shop2parcel/api/parcelapp.py
    - custom_components/shop2parcel/api/imap_client.py
    - custom_components/shop2parcel/api/email_parser.py
    - custom_components/shop2parcel/coordinator.py
decisions:
  - "DEBUG calls added after scan_events.append() — log line immediately follows the event recording at every exit point"
  - "All per-email verbose output is at DEBUG; no INFO calls added for per-email events per DBG-01"
  - "gmail_client and parcelapp had no existing _LOGGER — added import logging + _LOGGER = logging.getLogger(__name__) per D-10 Pitfall 3"
  - "imap_client and email_parser and coordinator already had _LOGGER — no import changes needed for those files"
metrics:
  duration_minutes: 20
  completed_date: "2026-05-12"
  tasks_completed: 2
  files_modified: 5
---

# Phase 11 Plan 02: Comprehensive DEBUG Logging — Summary

Added `import logging + _LOGGER = logging.getLogger(__name__)` to gmail_client.py and parcelapp.py (no existing logger). Added `_LOGGER.debug()` calls at all key operation boundaries across all five files: query string and message count in gmail_client, TN + HTTP status in parcelapp, connection and UID count in imap_client, carrier detection/match/no-match in email_parser, and poll start + message count + all 10 per-message outcome exit points in coordinator.

## Completed Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add _LOGGER + DEBUG calls to gmail_client, parcelapp, imap_client | 163f1f1 | 3 files, +19 lines |
| 2 | Add DEBUG calls to email_parser and coordinator | c5ec1ca | 2 files, +35 lines |

## What Was Built

### gmail_client.py (DBG-04)
- Added `import logging` and `_LOGGER = logging.getLogger(__name__)` at module level
- `async_list_messages`: DEBUG for full query string (after `build_incremental_query`) and message count (before return)

### parcelapp.py (DBG-02)
- Added `import logging` and `_LOGGER = logging.getLogger(__name__)` at module level
- `async_add_delivery`: DEBUG before POST (TN + carrier_code) and after response status is known (HTTP status + TN)

### imap_client.py (DBG-04)
- `_LOGGER` already present — no import changes
- `_fetch_sync`: DEBUG at connection entry (host + port, after TLS mode selection) and after SEARCH returns (UID count + folder)

### email_parser.py (DBG-03)
- `_LOGGER` already present — no import changes
- `parse()` CARRIER_REGISTRY loop: DEBUG on detection (`detect_fn.__name__` + message_id) and on match (TN + strategy + message_id)
- `parse()` no-match fallback: DEBUG at `no_tracking_pattern` early return

### coordinator.py (DBG-01, DBG-09)
- `_LOGGER` already present — no import changes
- Gmail path: DEBUG for poll start (query + rescan_window_days), fetch count, and all 5 per-message outcomes (error, no_match, skipped_dedup, skipped_quota, posted)
- IMAP path: DEBUG for poll start (host + query + since_date), fetch count, and all 5 per-message outcomes (error, no_match, skipped_dedup, skipped_quota, posted)

## Test Results

- Full test suite: 244/244 PASS (no test changes required)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Surface Scan

No new network endpoints or trust boundaries introduced. Debug log lines in coordinator emit subject and sender verbatim — this is the accepted T-11-03 risk (opt-in via HA toggle, single-user personal instance, explicit user decision D-06). Tracking number logged in parcelapp.py is the accepted T-11-04 risk (not a secret credential).

## Self-Check

### Files exist
- `custom_components/shop2parcel/api/gmail_client.py` — contains `import logging` and `_LOGGER = logging.getLogger(__name__)`
- `custom_components/shop2parcel/api/parcelapp.py` — contains `import logging` and `_LOGGER = logging.getLogger(__name__)`
- `custom_components/shop2parcel/api/imap_client.py` — contains `_LOGGER.debug("IMAP connecting to %s:%s"...)`
- `custom_components/shop2parcel/api/email_parser.py` — contains `_LOGGER.debug("Carrier template detected...`
- `custom_components/shop2parcel/coordinator.py` — contains `_LOGGER.debug("Gmail poll start...`

### Commits exist
- `163f1f1` — Task 1 commit (gmail_client, parcelapp, imap_client)
- `c5ec1ca` — Task 2 commit (email_parser, coordinator)

### Verification checks
- `grep -c "_LOGGER" gmail_client.py` returns 3 (>= 3 required)
- `grep -c "_LOGGER" parcelapp.py` returns 3 (>= 3 required)
- Full test suite: 244/244 PASS

## Self-Check: PASSED
