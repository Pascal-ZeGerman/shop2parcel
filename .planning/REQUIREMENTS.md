# Requirements: Shop2Parcel

**Defined:** 2026-04-02
**Core Value:** Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.
**Updated:** 2026-05-14 — Phase 12: marked all implemented v1 requirements complete

---

## v1 Requirements

### Setup & Configuration

- [x] **CONF-01**: User can add the integration via HA UI config flow (no YAML required)
- [x] **CONF-02**: User can enter their Gmail OAuth2 client credentials (client ID and client secret from Google Cloud Console) during setup
- [x] **CONF-03**: Integration validates Gmail OAuth2 credentials at config time and shows clear error on failure
- [x] **CONF-04**: User can enter parcelapp.net API key during setup
- [x] **CONF-05**: Integration validates parcelapp.net API key at config time and shows clear error on failure
- [x] **CONF-06**: Credentials are stored encrypted in HA config entry data (never in YAML)
- [x] **CONF-07**: User can trigger re-authentication (re-authorize Gmail OAuth2 access) if the token expires or access is revoked

### Email-Based Shipment Extraction

- [x] **EMAIL-01
**: Integration authenticates with Gmail API using OAuth2 client credentials (client ID + client secret configured in HA config flow)
- [x] **EMAIL-02
**: Integration searches Gmail for Shopify shipping confirmation emails using a configurable search query (default: `from:no-reply@shopify.com subject:"shipped"`, user-configurable via options flow)
- [x] **EMAIL-03
**: Integration parses emails using dual-strategy approach: first attempts structured extraction from Shopify's standard shipping confirmation HTML template (tracking number, carrier, order number from known DOM structure); falls back to keyword/regex extraction if template doesn't match
- [x] **EMAIL-04
**: Integration extracts per-shipment: tracking number, carrier name, order name/number from parsed email content
- [x] **EMAIL-05
**: Poll interval for Gmail API is configurable (default: 30 minutes) via options flow
- [x] **EMAIL-06
**: Integration handles Gmail API auth failures by raising ConfigEntryAuthFailed to trigger HA reauth flow (OAuth2 re-consent)
- [x] **EMAIL-07
**: Integration handles transient Gmail API errors (network, 5xx) without crashing HA
- [x] **EMAIL-08
**: Integration stores the timestamp of the last processed email to enable incremental polling (only processes new emails since last check)

### Parcel Forwarding

- [x] **FWRD-01**: New shipments discovered from parsed emails are POSTed to parcelapp.net API
- [x] **FWRD-02**: Already-forwarded shipments are never re-POSTed (persistent deduplication using Gmail message ID, survives HA restarts)
- [x] **FWRD-03**: Deduplication state is stored using HA's `homeassistant.helpers.storage.Store`
- [x] **FWRD-04**: Integration handles parcelapp.net 20 req/day quota by stopping forwarding attempts when limit is reached and logging a warning
- [x] **FWRD-05**: Integration handles parcelapp.net API errors gracefully (logs error, retries on next poll)

### Home Assistant Entities

- [x] **ENTT-01**: Integration exposes one sensor entity per active shipment (state = delivery status)
- [x] **ENTT-02**: Each shipment sensor has attributes: order name, tracking number, carrier, ETA string, shop name
- [x] **ENTT-03**: A `binary_sensor.shop2parcel_has_active_shipments` entity indicates whether any shipments are in transit
- [x] **ENTT-04**: Entities have stable unique IDs based on Gmail message ID (survive HA restarts)
- [x] **ENTT-05**: Delivered/closed shipments are eventually removed from HA entity registry (no orphaned entities)
- [x] **ENTT-06**: All entities are grouped under a single HA device (`Shop2Parcel`)

### Discovery: APIs

- [ ] **DISC-01**: parcelapp.net POST endpoint URL, auth scheme, and request body format are documented in `.planning/parcelapp-api.md` ✓ DONE
- [ ] **DISC-02**: parcelapp.net rate limit behavior (response body on quota exhaustion) is documented ✓ DONE

### HACS Packaging

- [x] **HACS-01**: Integration passes `hassfest` validation (manifest.json correct, no deprecated patterns)
- [x] **HACS-02**: Integration passes HACS validation (hacs.json, correct repo structure, tagged release)
- [x] **HACS-03**: README documents installation steps, how to set up Google Cloud OAuth2 credentials for Gmail API access, and parcelapp.net API key retrieval

### Diagnostic Tooling (Phase 7)

- [x] **DIAG-01**: `EmailParser.parse()` returns a `ParseResult` dataclass for every code path (success and failure) instead of `ShipmentData | None`
- [x] **DIAG-02**: `parse()` sets `result.strategy_used == "html_template"` when the HTML template strategy succeeds
- [x] **DIAG-03**: `parse()` sets `result.strategy_used == "regex_fallback"` when the regex fallback strategy succeeds
- [x] **DIAG-04**: `parse()` always populates `result.keyword_hits` with all 3 keys (`tracking_regex`, `order_regex`, `carrier_regex`) — bool values reflect fallback regex match outcomes; HTML-strategy parses leave all three False
- [x] **DIAG-05**: Coordinator accumulates `PollStats.emails_scanned_total`, `emails_matched_total`, and `tracking_numbers_found_total` for every non-already-forwarded message processed in `_async_update_data`
- [x] **DIAG-06**: Coordinator resets `last_poll_*` fields (last_poll_emails_scanned, last_poll_emails_matched, last_poll_skip_reasons, last_poll_found, last_poll_keyword_hits) at the start of each poll cycle
- [x] **DIAG-07**: Coordinator records `{"message_id": <id>, "reason": "no_html_body"}` in `last_poll_skip_reasons` when `extract_html_body` returns empty for a message (parser is never invoked in that case)
- [x] **DIAG-08**: 4 diagnostic sensor entities (`emails_scanned`, `emails_matched`, `tracking_numbers_found`, `keyword_hits`) are registered statically in `diagnostic_sensor.async_setup_entry`
- [x] **DIAG-09**: Each diagnostic sensor's state equals the matching cumulative `PollStats.*_total` field; `extra_state_attributes` matches the spec in CONTEXT.md D-12
- [x] **DIAG-10**: All diagnostic sensors share the same `Shop2Parcel` device (`identifiers={(DOMAIN, entry.entry_id)}`) — exactly one device per config entry
- [x] **DIAG-11**: Existing `tests/api/test_email_parser.py` tests pass after migrating to `result.shipment` accessor (no regression from ParseResult return-type change)
- [x] **DIAG-12**: Existing `tests/test_coordinator.py` tests pass after wrapping every `parser.parse(...)` mock return in `ParseResult(...)` (no regression from ParseResult consumption)

### Parser Template Expansion (Phase 8)

- [x] **PARSE-01**: `email_parser.py` exports module-level constants `STRATEGY_HTML="html_template"`, `STRATEGY_UPS="ups_template"`, `STRATEGY_USPS="usps_template"`, `STRATEGY_FEDEX="fedex_template"`, `STRATEGY_REGEX="regex_fallback"`
- [x] **PARSE-02**: `email_parser.py` defines a module-level `CARRIER_REGISTRY: list[tuple]` containing exactly three `(detect_fn, parse_fn)` tuples in the order UPS → USPS → FedEx
- [x] **PARSE-03**: `EmailParser.parse()` iterates `CARRIER_REGISTRY` (first match wins) before falling through to the existing `_parse_html_template` / `_parse_regex_fallback` Shopify path
- [x] **PARSE-04**: `_detect_ups` + `_parse_ups` extract a `1Z[A-Z0-9]{16}` tracking number from UPS direct shipping notification emails; result has `carrier_name="UPS"`, `order_name=""`, `strategy_used=STRATEGY_UPS`
- [x] **PARSE-05**: `_detect_usps` + `_parse_usps` extract a `9[2345][0-9]{15,26}` tracking number from USPS direct shipping notification emails; result has `carrier_name="USPS"`, `order_name=""`, `strategy_used=STRATEGY_USPS`
- [x] **PARSE-06**: `_detect_fedex` + `_parse_fedex` extract a `[0-9]{12,20}` tracking number from FedEx direct shipping notification emails; result has `carrier_name="FedEx"`, `order_name=""`, `strategy_used=STRATEGY_FEDEX`
- [x] **PARSE-07**: All carrier extraction regex (`_UPS_TRACKING_RE`, `_USPS_TRACKING_RE`, `_FEDEX_TRACKING_RE`) are compiled at module import with bounded quantifiers (ASVS V5 / T-ReDoS mitigation)
- [x] **PARSE-08**: `_TRACKING_PATTERNS` USPS entry updated to `^9[2345][0-9]{15,26}$` (carrier-specific anchor + extended length); FedEx entry updated to `^[0-9]{12,20}$` (extended for SmartPost)
- [x] **PARSE-09**: `_detect_ups` does NOT fire on the existing Shopify fixture (T-Spoof mitigation via `"shopify" not in html` guard); `_detect_usps` and `_detect_fedex` do not fire on the Shopify fixture either
- [x] **PARSE-10**: All 15 pre-existing tests in `tests/api/test_email_parser.py` still pass after refactor — no regression to Shopify/regex parsing
- [x] **PARSE-11**: `DEFAULT_GMAIL_QUERY` in `const.py` updated to combined `from:`+subject query covering Shopify (`no-reply@shopify.com`), UPS (`mcinfo@ups.com`), USPS (`auto-reply@usps.com`), and FedEx (`TrackingUpdates@fedex.com`) senders
- [x] **PARSE-12**: HTML fixture files exist at `tests/fixtures/{ups,usps,fedex}_shipping.html` containing the carrier domain fingerprint and a valid tracking number per format
- [x] **PARSE-13**: Carrier `parse_fn` always returns ParseResult with `keyword_hits` having all three keys (`tracking_regex`, `order_regex`, `carrier_regex`) all False (carrier templates don't run fallback regex — consistent with HTML-strategy convention from D-07)

---

## v1.1 Requirements (Milestone: Debug-Ready)

**Defined:** 2026-05-08

### Full-Window Scanning (SCAN)

- [x] **SCAN-01**: Coordinator scans ALL emails in the `rescan_window_days` window on every poll — no message-ID or IMAP UID skip gate
- [x] **SCAN-02**: Gmail coordinator query boundary is solely the `after:` date filter derived from `rescan_window_days` (not `last_seen_message_id`)
- [x] **SCAN-03**: IMAP coordinator fetches all UIDs in the folder from `(now − rescan_window_days)` on every poll (not filtered by `last_imap_uid`)

### Submission Dedup (DEDUP)

- [x] **DEDUP-01**: Coordinator maintains a persisted set of already-submitted tracking numbers in HA Store (`submitted_tracking_numbers`), surviving HA restarts
- [x] **DEDUP-02**: Before POSTing to parcelapp.net, coordinator checks `submitted_tracking_numbers` — matching tracking numbers are skipped without an API call
- [x] **DEDUP-03**: After a successful POST, coordinator adds the tracking number to `submitted_tracking_numbers` and immediately persists the Store

### Activity Log (ACTLOG)

- [x] **ACTLOG-01**: Coordinator maintains an in-memory ring buffer of the last 50 per-email scan events; buffer resets on HA restart
- [x] **ACTLOG-02**: Each scan event records: timestamp, message identifier, email subject, sender, template strategy used (or "no_match"), tracking number extracted (or null), submission outcome ("posted" / "skipped_dedup" / "skipped_quota" / "no_match" / "error")
- [x] **ACTLOG-03**: Existing counter sensors (emails_scanned, emails_matched, tracking_numbers_found, keyword_hits) are preserved unchanged — activity log is additive
- [x] **ACTLOG-04**: The activity log ring buffer is included in the HA diagnostics download
- [x] **ACTLOG-05**: A new diagnostic sensor entity exposes the last 10 scan events as `extra_state_attributes` (entity state = total events logged since last HA restart)

### Debug Logging (DBG)

- [x] **DBG-01**: All verbose/per-email processing output uses `_LOGGER.debug()` — INFO level is limited to user-actionable events (errors, new shipment found, shipment submitted)
- [x] **DBG-02**: Gmail and IMAP coordinators log at DEBUG per poll: query string / server+folder, message count returned, per-message processing decision
- [x] **DBG-03**: EmailParser logs at DEBUG per message: templates attempted in order, match/reject reason, data extracted
- [x] **DBG-04**: ParcelAppClient logs at DEBUG per API call: tracking number, HTTP response code, or skip reason
- [x] **DBG-05**: Debug logging is activated via HA's native integration debug toggle (Settings > Integrations > Shop2Parcel > Enable debug logging) — no additional integration-side configuration required

---

## v2 Requirements

### Auto Token Refresh

- **AUTH-01**: Integration uses Gmail OAuth2 refresh tokens to maintain persistent access without user re-authorization (standard OAuth2 flow, no custom refresh logic needed)

### Multi-Account

- **MULT-01**: User can configure multiple Gmail accounts in a single HA instance
- **MULT-02**: Entities from different accounts are grouped under separate HA devices

### Notifications

- **NOTF-01**: HA persistent notification is fired when a new shipment is detected
- **NOTF-02**: HA persistent notification is fired when a shipment is delivered

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| Shopify Admin REST API | User is a customer (buyer), not a merchant — Admin API access not available |
| Shopify GraphQL Admin API | Same reason — requires merchant credentials |
| Shop iOS app GraphQL API | Abandoned — requires reverse-engineered auth, fragile to app updates |
| Shopify Storefront Customer API | Per-store OAuth required; email parsing covers all stores in one inbox |
| Shopify store management (products, inventory, customers) | Out of domain — shipments only |
| Writing back to Shopify | Read-only integration |
| Multi-store/multi-account in v1 | Single account per integration instance sufficient for personal use |
| Webhook-based push | Requires public HA endpoint; polling is simpler and sufficient |
| Persisting diagnostic counters across HA restarts | Phase 7 deferred — counters reset on each HA restart per CONTEXT.md D-04 |
| User-configurable keyword monitoring (custom keywords in options flow) | Phase 7 deferred — would require options flow expansion and dynamic parser config |
| European carrier/retailer templates (Amazon, DHL, DPD, Zalando, OTTO) | Phase 8 D-02 deferred — US carriers (UPS/USPS/FedEx) only in Phase 8; European templates deferred to a sub-phase or Phase 9 |

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DISC-01 | Phase 1 — Foundation & Discovery | Done ✓ |
| DISC-02 | Phase 1 — Foundation & Discovery | Done ✓ |
| DISC-03 | Phase 1 — Foundation & Discovery | Removed — pivoted to Gmail API |
| DISC-04 | Phase 1 — Foundation & Discovery | Removed — pivoted to Gmail API |
| HACS-01 | Phase 1 — Foundation & Discovery | Done ✓ |
| EMAIL-01 | Phase 2 — API Clients | Complete |
| EMAIL-02 | Phase 2 — API Clients | Complete |
| EMAIL-03 | Phase 2 — API Clients | Complete |
| EMAIL-04 | Phase 2 — API Clients | Complete |
| EMAIL-05 | Phase 2 — API Clients | Complete |
| EMAIL-06 | Phase 2 — API Clients | Complete |
| EMAIL-07 | Phase 2 — API Clients | Complete |
| EMAIL-08 | Phase 2 — API Clients | Complete |
| CONF-01 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-02 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-03 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-04 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-05 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-06 | Phase 3 — HA Config & Plumbing | Complete |
| CONF-07 | Phase 3 — HA Config & Plumbing | Complete |
| EMAIL-05 (poll interval) | Phase 4 — Coordinator & Forwarding | Complete |
| FWRD-01 | Phase 4 — Coordinator & Forwarding | Complete |
| FWRD-02 | Phase 4 — Coordinator & Forwarding | Complete |
| FWRD-03 | Phase 4 — Coordinator & Forwarding | Complete |
| FWRD-04 | Phase 4 — Coordinator & Forwarding | Complete |
| FWRD-05 | Phase 4 — Coordinator & Forwarding | Complete |
| ENTT-01 | Phase 5 — Sensor Entities | Complete |
| ENTT-02 | Phase 5 — Sensor Entities | Complete |
| ENTT-03 | Phase 5 — Sensor Entities | Complete |
| ENTT-04 | Phase 5 — Sensor Entities | Complete |
| ENTT-05 | Phase 5 — Sensor Entities | Complete |
| ENTT-06 | Phase 5 — Sensor Entities | Complete |
| HACS-02 | Phase 6 — Testing & HACS Packaging | Complete |
| HACS-03 | Phase 6 — Testing & HACS Packaging | Complete |
| DIAG-01 | Phase 7 — Diagnostic Tooling (Plan 01) | Complete |
| DIAG-02 | Phase 7 — Diagnostic Tooling (Plan 01) | Complete |
| DIAG-03 | Phase 7 — Diagnostic Tooling (Plan 01) | Complete |
| DIAG-04 | Phase 7 — Diagnostic Tooling (Plan 01) | Complete |
| DIAG-05 | Phase 7 — Diagnostic Tooling (Plan 02) | Complete |
| DIAG-06 | Phase 7 — Diagnostic Tooling (Plan 02) | Complete |
| DIAG-07 | Phase 7 — Diagnostic Tooling (Plan 02) | Complete |
| DIAG-08 | Phase 7 — Diagnostic Tooling (Plan 03) | Complete |
| DIAG-09 | Phase 7 — Diagnostic Tooling (Plan 03) | Complete |
| DIAG-10 | Phase 7 — Diagnostic Tooling (Plan 03) | Complete |
| DIAG-11 | Phase 7 — Diagnostic Tooling (Plan 01) | Complete |
| DIAG-12 | Phase 7 — Diagnostic Tooling (Plan 02) | Complete |
| PARSE-01 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-02 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-03 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-04 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-05 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-06 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-07 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-08 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-09 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-10 | Phase 8 — Parser Template Expansion (Plan 02) | Complete |
| PARSE-11 | Phase 8 — Parser Template Expansion (Plan 03) | Complete |
| PARSE-12 | Phase 8 — Parser Template Expansion (Plan 01) | Complete |
| PARSE-13 | Phase 8 — Parser Template Expansion (Plans 01 & 02) | Complete |
| SCAN-01 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| SCAN-02 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| SCAN-03 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| DEDUP-01 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| DEDUP-02 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| DEDUP-03 | Phase 10 — Full-Window Scanning & Tracking Dedup | Complete |
| ACTLOG-01 | Phase 11 — Activity Log & Debug Logging | Complete |
| ACTLOG-02 | Phase 11 — Activity Log & Debug Logging | Complete |
| ACTLOG-03 | Phase 11 — Activity Log & Debug Logging | Complete |
| ACTLOG-04 | Phase 11 — Activity Log & Debug Logging | Complete |
| ACTLOG-05 | Phase 11 — Activity Log & Debug Logging | Complete |
| DBG-01 | Phase 11 — Activity Log & Debug Logging | Complete |
| DBG-02 | Phase 11 — Activity Log & Debug Logging | Complete |
| DBG-03 | Phase 11 — Activity Log & Debug Logging | Complete |
| DBG-04 | Phase 11 — Activity Log & Debug Logging | Complete |
| DBG-05 | Phase 11 — Activity Log & Debug Logging | Complete |

**Coverage:**
- v1 requirements: 56 total (31 + 12 DIAG + 13 PARSE)
- v1.1 requirements: 16 total (3 SCAN + 3 DEDUP + 5 ACTLOG + 5 DBG)
- Mapped to phases: 56/56 (v1) + 16/16 (v1.1)
- Unmapped: 0

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-05-14 — Phase 12: marked all implemented v1 requirements complete*
