---
gsd_state_version: 1.0
milestone: v1.4
milestone_name: Stage-2 Reliability
current_phase: 23
current_phase_name: Decouple Stage-2 LLM Extraction from parcelapp POST + debug POST suppression
status: planning
stopped_at: Phase 23 created — entering discuss/plan flow (decouple LLM from POST, fix debug dry-run)
last_updated: "2026-06-24T00:00:00.000Z"
last_activity: 2026-06-24
last_activity_desc: opened Phase 23 (v1.4) after live root-cause of Stage-2 quota-flood (debug_mode + coupled POST)
progress:
  total_phases: 10
  completed_phases: 8
  total_plans: 23
  completed_plans: 29
  percent: 80
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-05)

**Core value:** Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.
**Current focus:** v1.4 Phase 23 — decouple Stage-2 LLM extraction from the parcelapp POST; fix debug-mode POST suppression.

## Current Position

Phase: 23 — Decouple Stage-2 LLM Extraction from parcelapp POST + debug POST suppression
Plan: Not yet planned (discuss → plan → execute)
Status: v1.4 opened. Phase 23 is bug-fix-driven: live root-cause found debug_mode + POST-coupled-to-quota causing a quota-skip flood with 0 LLM parses.
Last activity: 2026-06-24 — Created Phase 23; reconciled v1.4 roadmap numbering (backlog ideas bumped to 24/25).

## Accumulated Context

### Roadmap Evolution

- Phase 23 added (v1.4): Decouple Stage-2 LLM extraction from parcelapp POST + honor debug-mode POST suppression. Backlog ideas renumbered: Custom Extraction Field Persistence → 24, Stage-2 Observability → 25.

## Performance Metrics

**Velocity:**

- Total plans completed: 55
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
| 15 | 4 | - | - |
| 16 | 3 | - | - |
| 17 | 4 | - | - |
| 18 | 2 | - | - |
| 19 | 2 | - | - |
| 20 | 3 | - | - |
| 22 | 2 | - | - |
| 21 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 02-api-clients P01 | 9 | 2 tasks | 9 files |
| Phase 02-api-clients P02 | 4 | 2 tasks | 5 files |
| Phase 02-api-clients P03 | 9 | 1 tasks | 2 files |
| Phase 05-sensor-entities P01 | 10 | 3 tasks | 4 files |
| Phase 15-ollamaclient-foundation P03 | 7 | 3 tasks | 2 files |
| Phase 15-ollamaclient-foundation P04 | 4 | 3 tasks | 2 files |
| Phase 22 P01 | 15 | 2 tasks | 1 files |
| Phase 22 P02 | 2 | 1 tasks | 1 files |
| Phase 21 P01 | 25 | 2 tasks | 7 files |
| Phase 21 P02 | 26 | 2 tasks | 4 files |
| Phase 21 P03 | 8m | 2 tasks | 5 files |

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
- Phase 13.1: STORAGE_VERSION bumped to 3; v2->v3 migration preserves submitted_tracking_numbers + quota_exhausted_until, seeds persisted_shipments as {}
- Phase 13.1: _SHIPMENT_FIELD_TYPES module-level constant used for per-entry type validation in _async_load_store (T-13.1-04 ASVS V5)
- Phase 13.1: _pending_shipments assigned before _async_save_store() so debounced lambda captures updated state
- v1.3 Phase 15: OllamaClient mirrors `parcelapp.py` shape — session injection, no HA imports, custom exception taxonomy (`OllamaTransientError`, `OllamaSchemaError`)
- v1.3 Phase 15-03: OllamaClient 2-pass parse pipeline shipped (Pass 1 = normalize + json.loads; Pass 2 = fence-strip + normalize + json.loads on Pass 1 JSONDecodeError only). Missing-`{` from normalize is a hard fail (no Pass 2 retry). NFKC preserves Cyrillic A — Phase 20 carrier-regex pre-POST validation will catch any homoglyph slips on real tracking numbers.
- v1.3 architecture lock: in-memory queue only (HA-restart-lossy by design); full-window rescan re-discovers un-dedup'd emails — no STORAGE_VERSION bump
- v1.3 architecture lock: single long-lived worker per coordinator; multi-worker rejected (Ollama serializes per-model + ParcelApp 20/day quota)
- v1.3 architecture lock: drop-newest backpressure on QueueFull (drop-oldest rejected — wastes head-of-queue work, breaks FIFO activity-log ordering)
- v1.3 quota-burn mitigation set is INSEPARABLE: per-field merge guards + carrier-regex pre-POST validation + `temperature:0` + `MAX_STAGE2_POSTS_PER_POLL` cap + scoped skip-dedup. All five land in Phase 20.
- v1.3 Phase 15-04: `live_ollama` pytest marker registered in `pyproject.toml`; single opt-in smoke test `tests/api/test_ollama_client_live.py` gated by `OLLAMA_URL` env var (D-10/D-11/D-12/D-13). CI silently skips. Phase 15 complete: 384 tests passing, 1 skipped (the live smoke).
- Phase 20-01: merge_llm_authoritative returns tuple[ShipmentData, list[dict]] (Option A) — merge.py stays HA-free per D-02; coordinator emits stage2_conflict event using returned conflicts list
- Phase 20-01: type: ignore[arg-type] on dataclasses.replace(**overrides) — mypy cannot resolve dict[str, str | None] spread against ShipmentData typed replace() overload; runtime values always valid
- [Phase ?]: Phase 22 D-12/D-13/D-14/D-15: tests/test_stage2_e2e_live.py ships with live_ollama marker, OLLAMA_URL gate, and OllamaExtractor construction — fulfills Phase 15 D-13 deferral

### Roadmap Evolution

- Phase 7 added: Diagnostic Tooling — HA sensors with rich attributes for email scan/parse/tracking-number stats
- Phase 8 added: Parser Template Expansion — UPS/USPS/FedEx/Amazon/DHL template matchers (porting from Mail & Packages addon)
- Phase 9 added: IMAP Support & Multi-Account — IMAP connection method + multiple accounts per HA instance
- Phase 10 added (v1.1): Full-Window Scanning & Tracking Dedup — remove last_seen_message_id/last_imap_uid gates, add persisted tracking-number dedup
- Phase 11 added (v1.1): Activity Log & Debug Logging — per-email scan event ring buffer + comprehensive DEBUG-level logging
- Phase 12 added: Address tech debt
- Phase 13.1 inserted after Phase 13: Sensor Restore on Restart — coordinator.data not persisted means all sensors unavailable after restart (HA log audit finding) (URGENT)
- v1.3 Phases 15–22 added: OllamaClient foundation → extractor → config-flow → queue → worker → merge+quota guards → failure surface+diagnostics → README+e2e. Phases 16, 19, 20 flagged NEEDS RESEARCH.

### Pending Todos

| File | Title | Area |
|------|-------|------|
| [2026-06-02-add-forwarded-email-sender-configuration.md](./todos/pending/2026-06-02-add-forwarded-email-sender-configuration.md) | Add forwarded email sender configuration | api |

### Blockers/Concerns

None — v1.3 roadmap drafted, 37/37 requirements mapped, three NEEDS RESEARCH phases flagged. Ready to run `/gsd:plan-phase 15`.

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
| 260523-g8u | Reconcile ROADMAP and STATE for Phase 14 (4/4 complete 2026-05-20); audit WR-01..WR-04 — all already shipped on origin/main | 2026-05-23 | n/a (docs only) | [260523-g8u-reconcile-roadmap-and-state-for-phase-14](./quick/260523-g8u-reconcile-roadmap-and-state-for-phase-14/) |
| 260601-x94 | Fix wrong USPS Informed Delivery sender address in DEFAULT_GMAIL_QUERY | 2026-06-02 | cfb3567 | [260601-x94-fix-wrong-usps-informed-delivery-sender-](./quick/260601-x94-fix-wrong-usps-informed-delivery-sender-/) |
| 260618-iro | Fix 10 issues from PR 20 code review: I1 (cap notification dismiss), I2 (remove_entry missing dismiss), I3 (drain loop task_done), I4 (cancel-mid-success store flush), I5 (wrong error message in _split_and_coerce), I6 (MRG-04 tracking_number=None invariant), I7 (cap counter includes AlreadyAdded), S1 (BeautifulSoup error handling), S2 (snapshot-before-set_updated_data), S3 (empty URL UX note) | 2026-06-18 | d1b2a09 | [260618-iro-fix-10-issues-from-pr-20-code-review-i1-](./quick/260618-iro-fix-10-issues-from-pr-20-code-review-i1-/) |
| 260623-d9s | Add description attribute to all 7 diagnostic sensors so users can read what each sensor measures in the HA entity detail panel | 2026-06-23 | a9296fa | [260623-d9s-add-description-attrs-to-diagnostic-sensors](./quick/260623-d9s-add-description-attrs-to-diagnostic-sensors/) |
| 260623-f2k | Add 3 LLM performance diagnostic sensors: OllamaLatencySensor (avg/last/min/max ms), OllamaParseQualitySensor (fence-strip retry count + rate), Stage2ConsecutiveFailuresSensor (failure streak) | 2026-06-23 | 34385e0 | [260623-f2k-add-llm-performance-diagnostic-sensors](./quick/260623-f2k-add-llm-performance-diagnostic-sensors/) |

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

Items acknowledged and deferred at v1.2 milestone close on 2026-06-05:

| Category | Item | Status |
|----------|------|--------|
| debug_session | no-new-numbers-email-search-2026-05-17 — RESOLVED by Phase 13 (already-added 400s now treated as idempotent success; dedup store no longer writes rejected TNs) | resolved |
| debug_session | oauth-token-rejected-after-update | root_cause_identified |
| todo | add-forwarded-email-sender-configuration — future capability, not blocking v1.2 | deferred |
| seed | SEED-001-debug-dry-run-mode — SHIPPED in Phase 14 (v1.2); seed can be closed | resolved |
| seed | SEED-002-activity-log-human-readable-poll-summary — future feature | dormant |
| quick_task | 17 quick tasks with missing summary files — administrative debt; work is completed | missing_summary |

Note: UAT gaps (phases 02-07) and verification gaps (phases 02-06) are continuing deferrals from v1.0/v1.1 (hardware-dependent live HA testing).

## Session Continuity

Last session: 2026-06-18T16:30:00.000Z
Stopped at: PR #20 CI all green — waiting for UAT in live HA before cutting v1.3.0 final tag.
Next action: Run UAT on HA instance, then cut v1.3.0 final tag and merge PR #20.

## Operator Next Steps

- Run UAT on live HA instance — install v1.3.0-rc1 via HACS, test Stage-2 Ollama extraction end-to-end
- After UAT passes: `git tag v1.3.0 origin/main && git push origin v1.3.0` (after merging PR #20)
- `/gsd-new-milestone` — start v1.4 milestone planning
