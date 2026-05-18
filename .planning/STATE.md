---
gsd_state_version: 1.0
milestone: v1.2
milestone_name: Debug Switch
status: planning
last_updated: "2026-05-18T19:28:16.183Z"
last_activity: 2026-05-18
progress:
  total_phases: 0
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-17)

**Core value:** Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.
**Current focus:** Planning v1.2 — run `/gsd:new-milestone` to define next milestone

## Current Position

Phase: Not started (defining requirements)
Plan: —
Status: Defining requirements
Last activity: 2026-05-18 — Milestone v1.2 started

## Performance Metrics

**Velocity:**

- Total plans completed: 32
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 02 | 3 | - | - |
| 04 | 3 | - | - |
| 05 | 2 | - | - |
| 06 | 4 | - | - |
| 07 | 3 | - | - |
| 08 | 3 | - | - |
| 9 | 5 | - | - |
| 10 | 3 | - | - |
| 11 | 3 | - | - |
| 12 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 02-api-clients P01 | 9 | 2 tasks | 9 files |
| Phase 02-api-clients P02 | 4 | 2 tasks | 5 files |
| Phase 02-api-clients P03 | 9 | 1 tasks | 2 files |
| Phase 05-sensor-entities P01 | 10 | 3 tasks | 4 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Gmail API over IMAP — IMAP being shut down for OAuth by Google; Gmail API is the supported path
- Dual-strategy email parsing — Shopify standard HTML template extraction first, keyword/regex fallback for non-standard emails
- Configurable Gmail search query — user can customize the email search filter in options flow (default: from:no-reply@shopify.com subject:shipped)
- Polling over webhooks — no public HA endpoint required
- HACS-compatible repo structure — enables easy install and future sharing
- GmailClient accepts async_add_executor_job as Callable injection — no HA import in api/ module
- Tests mock googleapiclient via sys.modules at module load time — runs without network/PyPI access
- ParcelAppQuotaError carries optional reset_at field per D-02 for smarter coordinator backoff
- EmailParser._parse_html_template scans only <p> elements — Shopify standard template uses prose in <p>, no CSS class on tracking data
- normalize_carrier falls back to pholder not none — pholder is a valid parcelapp code returning HTTP 200, preventing quota waste on unknown carriers
- ShipmentData.carrier_name stores raw Shopify string — Phase 4 coordinator calls normalize_carrier() before POSTing to parcelapp
- aiohttp/aioresponses symlinked from sibling VW-CarNet venv — PyPI network-blocked in Raspberry Pi dev environment
- ParcelAppClient session injection pattern — Phase 4 coordinator injects shared HA session, client never creates its own
- v0.1.0 released (D-10 + D-11) — first HACS-installable tag, all 4 CI workflows green, GitHub Release at https://github.com/Pascal-ZeGerman/shop2parcel/releases/tag/v0.1.0
- v1.1 scanning architecture: full-window scan always on (no message-ID/UID gate), dedup shifts to persisted tracking-number set in HA Store

### Roadmap Evolution

- Phase 7 added: Diagnostic Tooling — HA sensors with rich attributes for email scan/parse/tracking-number stats
- Phase 8 added: Parser Template Expansion — UPS/USPS/FedEx/Amazon/DHL template matchers (porting from Mail & Packages addon)
- Phase 9 added: IMAP Support & Multi-Account — IMAP connection method + multiple accounts per HA instance
- Phase 10 added (v1.1): Full-Window Scanning & Tracking Dedup — remove last_seen_message_id/last_imap_uid gates, add persisted tracking-number dedup
- Phase 11 added (v1.1): Activity Log & Debug Logging — per-email scan event ring buffer + comprehensive DEBUG-level logging
- Phase 12 added: Address tech debt

### Pending Todos

None yet.

### Blockers/Concerns

None — Phase 1 gates cleared. parcelapp.net API documented. Gmail OAuth2 is standard (no discovery needed).

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260424-d83 | Gmail API pivot — update REQUIREMENTS.md, ROADMAP.md, STATE.md, delete stale Shop app API docs | 2026-04-24 | n/a (gitignored) | [260424-d83-gmail-email-pivot](./quick/260424-d83-gmail-email-pivot/) |
| 260501-n8g | Fix Phase 07 code review findings: WR-01 coordinator diagnostics, WR-02 dead code, WR-03 regex, IN-01 docstring | 2026-05-01 | 502167f | [260501-n8g-fix-phase-07-code-review-findings-wr-01-](./quick/260501-n8g-fix-phase-07-code-review-findings-wr-01-/) |
| 260504-dgz | Commit simplify fixes and archive 4 stale todos | 2026-05-04 | 366388f | [260504-dgz-commit-simplify-fixes-and-archive-4-stal](./quick/260504-dgz-commit-simplify-fixes-and-archive-4-stal/) |
| 260504-k1b | Fix all PR #2 review findings — 5 critical bugs, 7 important issues, 3 missing tests, 7 doc fixes, 4 suggestions | 2026-05-04 | ef0d402 | [260504-k1b-fix-pr-review-findings](./quick/260504-k1b-fix-pr-review-findings/) |
| 260504-mxy | Fix 3 real CodeQL findings — unused _LOGGER in diagnostic_sensor.py, unused imports in test_multi_account.py | 2026-05-04 | 1eb2314 | [260504-mxy-fix-3-codeql-findings-remove-unused-logg](./quick/260504-mxy-fix-3-codeql-findings-remove-unused-logg/) |
| 260504-ci1 | Fix 3 CI failures — stale setup-python SHA, missing hacs/action tag, invalid homeassistant key in manifest.json | 2026-05-04 | 636fa41 | n/a |
| 260504-uik | Add diagnostics.py — HA quality-scale diagnostics platform with PollStats + last 10 shipments, credential redaction | 2026-05-05 | 3bd29b5 | [260504-uik-add-diagnostics-py-to-shop2parcel-integr](./quick/260504-uik-add-diagnostics-py-to-shop2parcel-integr/) |
| 260505-wl8 | Fix all critical and important issues from PR4 review (C1 order regex, C2 broad-scan gate, C3 ValueError crash, I1 html.escape, I2 carrier path, I3 debug logging, I4 carrier regex) | 2026-05-06 | 3fc8a47 | [260505-wl8-fix-all-critical-and-important-issues-fr](./quick/260505-wl8-fix-all-critical-and-important-issues-fr/) |
| 260506-dhd | Fix DEFAULT_GMAIL_QUERY label:inbox (archived emails excluded) and add rescan_window_days option (Gmail-only, 7-365d) with min(stored_ts, now-window) semantics for non-destructive lookback widening | 2026-05-06 | ac6d01a | [260506-dhd-fix-gmail-query-label-inbox-and-add-resc](./quick/260506-dhd-fix-gmail-query-label-inbox-and-add-resc/) |
| 260513-p01 | Write missing summary stubs for 01-01-PLAN.md and 06-03-PLAN.md to close the v1.0 archive artifact gap | 2026-05-13 | n/a (docs only) | [260513-p01-write-missing-summary-stubs-for-01-01-pl](./quick/260513-p01-write-missing-summary-stubs-for-01-01-pl/) |

## Deferred Items

Items acknowledged and deferred at milestone close on 2026-05-04:

| Category | Item | Status |
|----------|------|--------|
| uat_gap | Phase 02: 02-HUMAN-UAT.md — 2 pending scenarios | partial |
| uat_gap | Phase 03: 03-HUMAN-UAT.md — 3 pending scenarios | partial |
| uat_gap | Phase 04: 04-HUMAN-UAT.md — 3 pending scenarios | partial |
| uat_gap | Phase 05: 05-HUMAN-UAT.md — 2 pending scenarios | partial |
| uat_gap | Phase 07: 07-HUMAN-UAT.md — 1 pending scenario | partial |
| verification_gap | Phase 02: 02-VERIFICATION.md | human_needed |
| verification_gap | Phase 03: 03-VERIFICATION.md | human_needed |
| verification_gap | Phase 04: 04-VERIFICATION.md | human_needed |
| verification_gap | Phase 05: 05-VERIFICATION.md | human_needed |
| verification_gap | Phase 06: 06-VERIFICATION.md | human_needed |
| quick_task | 260424-001-shop-app-api-docs | missing summary |

All UAT and verification gaps require live HA instance testing (hardware-dependent). Deferred — not blocking milestone close.

Items acknowledged and deferred at v1.1 milestone close on 2026-05-17:

| Category | Item | Status |
|----------|------|--------|
| debug_session | no-new-numbers-email-search-2026-05-17 — 3 stuck message IDs loop with "already added" 400s on every HA restart; dedup store persistence | root_cause_identified |
| tech_debt | CONF_RESCAN_WINDOW_DAYS comment reads "Gmail-only" but IMAP also uses it | minor |
| tech_debt | ACTLOG-02 test gap: no_html_body outcome has no ACTLOG assertion | minor |
| tech_debt | DBG-01..04 have no automated caplog test coverage | minor |
| tech_debt | ParcelAppTransientError/InvalidTrackingError paths don't emit scan events | minor |

## Session Continuity

Last session: 2026-05-17
Stopped at: v1.1 milestone archived — ready to plan v1.2
Next action: /gsd:new-milestone
