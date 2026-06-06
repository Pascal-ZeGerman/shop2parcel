# Shop2Parcel — Shopify → Parcel App Home Assistant Integration

## What This Is

Shop2Parcel is a Home Assistant custom integration that monitors Gmail (OAuth2) or any IMAP mailbox for Shopify-style shipping confirmation emails, extracts tracking data (tracking number, carrier, order number) via a two-stage pipeline — fast template parsers first, then a local Ollama LLM for emails the templates miss or need augmenting — and forwards it to parcelapp.net. Shipments appear as HA sensor entities automatically. Multiple accounts (Gmail and/or IMAP) can be configured per HA instance, each running its own polling coordinator. A configurable debug/dry-run mode allows full diagnostic output without submitting real data to parcelapp.net.

## Core Value

Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.

## Current Milestone: v1.3 AI-based email analysis

**Goal:** Add a second-stage local-LLM extractor (Ollama) so emails the template parsers miss still produce complete `ShipmentData` — with a bounded async queue smoothing slow-machine inference and loud, surfaced failures.

**Target features:**

- `OllamaExtractor` Stage-2 client — `aiohttp` POST to `/api/generate`, `stream:false`, JSON-schema-driven extraction. Configurable URL/model/timeout. Default model `qwen3.5:2b`; URL is required user input (no preset)
- Always-on Stage 2 with LLM-authoritative merge — Stage 1 (template parsers) still runs first; Stage 2 runs on every sender-matched email and overwrites Stage-1 fields where both produced a value
- Per-coordinator bounded `asyncio.Queue` + long-lived worker — coordinator enqueues raw emails; worker drains one-at-a-time, calls Ollama, then POSTs to parcelapp. In-memory only (HA-restart-lossy by design; full-window rescan re-discovers)
- Config-flow expansion — Ollama URL (required), model name, timeout, queue maxlen, extraction-field list. The 3 parcelapp-required fields (`tracking_number`, `carrier`, `order_name`) are locked; user can add custom fields stored as sensor attributes (not POSTed)
- Loud failure surface — `_LOGGER.error()` per Stage-2 fail, persistent HA notification after N consecutive failures, activity-log entry (`stage2_failed`), email NOT written to dedup store on failure
- README setup section — Ollama install via Docker/Portainer, networking options, working curl reference, recommended starter model, reachability sanity-check command

## Previous Milestones

<details>
<summary>v1.2 Debug Switch — shipped 2026-06-05</summary>

- Dedup store persistence bug fixed: already-added 400s treated as idempotent success; no more restart loops
- HA Store STORAGE_VERSION 3: persists `coordinator.data` (ShipmentData objects) across HA restarts — sensors reappear after first poll without manual intervention
- Debug/dry-run mode: 365-day window override, dedup bypass, dry-run POST suppression, per-email INFO logging, persistent HA notification
- **Codebase:** ~13,641 LOC total (integration + tests), 361 tests passing, manifest v1.2.x
- **Connection methods:** Gmail OAuth2 and IMAP (app-password / SSL / STARTTLS)
- **Carriers supported:** Shopify standard, UPS, USPS, FedEx

</details>

<details>
<summary>v1.1 Debug-Ready — shipped 2026-05-17</summary>

- Full-window scanning always on; tracking-number-based dedup (LRU 1000) persisted across restarts
- Per-email activity log ring buffer (50 events) + ActivityLogSensor + diagnostics download
- Comprehensive `_LOGGER.debug()` — HA native debug toggle reveals full operational detail
- Coordinator split into `GmailCoordinator` + `ImapCoordinator` subclasses
- **Codebase:** ~3,600 LOC integration + ~6,000 LOC tests, 265 tests passing, manifest v1.1.1

</details>

<details>
<summary>v1.0 MVP — shipped 2026-05-04</summary>

- **HACS-installable:** Yes — v0.1.0 tagged, CI green
- **Codebase:** ~6,800 LOC Python across `custom_components/shop2parcel/` and `tests/`
- **Test suite:** 156+ tests passing, 4 GitHub Actions workflows green (pytest, hassfest, hacs, release)
- **Connection methods:** Gmail OAuth2 and IMAP (app-password / SSL / STARTTLS)
- **Carriers supported:** Shopify standard, UPS, USPS, FedEx (via template registry)

</details>

## Requirements

### Validated

- ✓ Parse shipment data: tracking number, carrier, order name — v1.0 (Phase 2)
- ✓ POST shipment data to parcelapp.net API in expected format — v1.0 (Phase 2)
- ✓ Handle auth failures, rate limiting, and transient errors with structured exception taxonomy — v1.0 (Phase 2)
- ✓ Config flow in HA UI for entering credentials (Gmail OAuth2 + parcelapp key) — v1.0 (Phase 3)
- ✓ Handle deduplication — don't re-submit shipments already sent to parcelapp.net — v1.0 (Phase 4)
- ✓ Poll Gmail/IMAP for new shipments on configurable schedule — v1.0 (Phase 4)
- ✓ Expose HA sensor per active shipment (tracking number, carrier, order name) — v1.0 (Phase 5)
- ✓ Binary sensor for "has active shipments" — v1.0 (Phase 5)
- ✓ Automated test suite + CI (hassfest, HACS, pytest) — v1.0 (Phase 6)
- ✓ HACS-installable via tagged GitHub release — v1.0 (Phase 6)
- ✓ Diagnostic sensors: emails scanned, matched, tracking numbers found, keyword hits — v1.0 (Phase 7)
- ✓ UPS, USPS, FedEx email template parsers alongside Shopify dual-strategy — v1.0 (Phase 8)
- ✓ IMAP connection method (alternative to Gmail OAuth2) — v1.0 (Phase 9)
- ✓ Multiple accounts per HA instance (Gmail and/or IMAP, each with own coordinator) — v1.0 (Phase 9)
- ✓ Full-window email scanning — always scan all emails in rescan_window_days; no last_seen_message_id / last_imap_uid gate — v1.1 (Phase 10)
- ✓ Tracking-number-based submission dedup — persisted OrderedDict (LRU cap 1000) replaces message-ID dedup; STORAGE_VERSION 1→2 migration — v1.1 (Phase 10)
- ✓ Per-email diagnostic activity log — ring buffer of scan events (subject, sender, template matched, tracking data extracted, submission outcome) via ActivityLogSensor + diagnostics download — v1.1 (Phase 11)
- ✓ HA debug mode integration — comprehensive _LOGGER.debug() calls across coordinator, parsers, and API clients; HA's native debug toggle reveals full operational detail — v1.1 (Phase 11)
- ✓ Fixed dedup store persistence: already-added 400 responses treated as idempotent success; coordinator no longer writes rejected TNs to dedup store on restart — v1.2 (Phase 13)
- ✓ HA Store STORAGE_VERSION 3: persists coordinator.data (ShipmentData objects) across restarts; v2→v3 migration with per-entry type validation — v1.2 (Phase 13.1)
- ✓ Debug/dry-run mode: 365-day window override, dedup bypass, dry-run POST suppression, per-email INFO logging, persistent HA notification — v1.2 (Phase 14)

### Active (v1.3)

- [ ] Add a second-stage Ollama-based extractor invoked on every sender-matched email
- [ ] Add per-coordinator bounded async queue + worker to decouple poll cadence from LLM latency
- [ ] Expand config flow with Ollama URL (required), model name, timeout, queue maxlen, and user-extensible field list (3 parcelapp fields locked)
- [ ] Make Stage 2 LLM-authoritative: its values overwrite Stage-1 template-parser values on conflict
- [ ] Surface Stage-2 failures loudly: ERROR log + persistent HA notification + `stage2_failed` activity-log entry + skip-dedup-on-failure
- [ ] Document Ollama setup in README (Docker/Portainer, networking, model recommendation, reachability test)

### Active (deferred to future milestones)

- [ ] Delivery status tracking — shipment sensor state reflects current delivery status
- [ ] Configurable cleanup grace period for delivered shipments (currently hardcoded 24h)
- [ ] Reauth flow for IMAP credential failures (currently logs error, no HA Repairs notification)
- [ ] Deduplication against tracking numbers already in parcelapp.net GET endpoint (server-side cross-check)
- [ ] Add forwarded email sender configuration — allow configuring trusted forwarder addresses in options flow

### Out of Scope

- Shopify mobile/storefront API reverse-engineering — replaced by Gmail email parsing approach
- Webhook-based push integration — polling is sufficient and simpler for HA
- Writing back to Shopify (order updates, cancellations) — read-only
- Multi-store Shopify support — single store per integration instance
- European carrier templates (Amazon DE, DHL, DPD, Zalando, OTTO) — US-focused product
- Debug mode as auto-trigger on HA debug logging — orthogonal concerns; would silently suppress real parcelapp POSTs when user enables HA debug logging
- Hosted/cloud LLM providers (OpenAI, Anthropic, Gemini) for Stage 2 — local-only by design; user-owned model, no third-party email-content exposure
- HA-Store-persisted Stage-2 queue — restart-survival adds storage migration burden; full-window rescan already re-discovers unprocessed emails next poll
- Hardcoded `localhost:11434` default for Ollama URL — Pi-on-Pi co-located Docker is one of several layouts; URL must be user-supplied to avoid silently-misconfigured installs

## Context

- **Architecture:** `DataUpdateCoordinator` (one per account) → `GmailClient` or `ImapClient` → `EmailParser` → `ParcelAppClient`. Sensor entities subscribe to coordinator updates.
- **Auth storage:** Gmail OAuth2 tokens and IMAP credentials stored in HA config entry `data` (encrypted at rest). Parcelapp API key also in `data`.
- **Dedup:** `Shop2ParcelStore` (STORAGE_VERSION 3) persists `_submitted_tracking_numbers: OrderedDict[str, None]` (LRU cap 1000) AND `_persisted_shipments: dict[str, ShipmentData]` across HA restarts. v2→v3 migration path included.
- **Parser:** Template registry (CARRIER_REGISTRY) with `detect_fn` + `parse_fn` per carrier. Shopify fallback uses BS4 `<p>` scan then regex keyword match.
- **Environment:** Raspberry Pi dev environment with PyPI network-blocked — aiohttp/aioresponses symlinked from sibling venv.
- **Codebase:** ~13,641 LOC total (integration + tests), 361 tests, manifest v1.2.x.

## Constraints

- Python 3.14+ (HA 2026.x requirement)
- HA async architecture — all I/O via `aiohttp` with shared session from `async_get_clientsession(hass)`
- IMAP fetch is synchronous (`imaplib`) — runs via `async_add_executor_job`
- Shopify REST Admin API rate limit: 2 req/s leaky bucket (not currently used — email-based approach bypasses Shopify API entirely)
- parcelapp.net quota: 20 POST/day — coordinator backs off on `ParcelAppQuotaError`

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Gmail API over Shopify mobile API | Shopify mobile API is undocumented; Gmail shipping emails are reliable and format-stable | ✓ Good — worked for Shopify, UPS, USPS, FedEx |
| Polling over webhooks | No public HA endpoint required | ✓ Good |
| HACS-compatible repo structure | Easy install, future sharing | ✓ Good — v0.1.0 shipped via HACS |
| DataUpdateCoordinator (one per account) | Single poll per account feeds all entities | ✓ Good |
| Dual-strategy parsing (template + regex fallback) | Handles both standard and non-standard Shopify emails | ✓ Good |
| normalize_carrier falls back to `pholder` | Prevents quota waste on unknown carriers | ✓ Good |
| GmailClient/ImapClient accept executor injection | No HA imports in `api/` modules — independently testable | ✓ Good |
| IMAP via `imaplib` in executor thread | `imaplib` is synchronous; executor keeps event loop free | ✓ Good |
| UID-based IMAP dedup with Store persistence | Survives HA restarts without re-scanning old mail | ⚠️ Revisit — v1.1 migrated to TN-based dedup; UID dedup removed |
| European carrier templates out of scope | US-focused product; complexity not justified | ✓ Decided v1.0 |
| Tracking-number dedup replaces message-ID dedup | Message-ID dedup missed re-scanned emails; TN dedup is semantically correct | ✓ Good — v1.1 |
| Full-window scan always on | Simpler logic; dedup handles duplicates | ✓ Good — no missed shipments |
| `collections.deque(maxlen=50)` for activity log | Auto-eviction, no manual size check | ✓ Good — v1.1 |
| Coordinator split into subclasses | 926-line monolith was unmanageable; subclass pattern clean | ✓ Good — v1.1 Phase 12 |
| FedEx `_parse_fedex` href fallback added | FedEx Delivery Manager emails use href for TN, not text prefix | ✓ Fixed — PR #10 |
| Already-added 400 routed as idempotent success | Previous code wrote rejected TNs back to dedup store, causing restart loops | ✓ Good — v1.2 Phase 13 |
| STORAGE_VERSION 3 persists ShipmentData | Sensors vanished after HA restart because coordinator.data was not persisted | ✓ Good — v1.2 Phase 13.1 |
| _SHIPMENT_FIELD_TYPES for per-entry validation | Prevents corrupt store entries from crashing coordinator on load | ✓ Good — v1.2 Phase 13.1 |
| Debug mode as config option (not auto-trigger) | Debug logging and dry-run mode are orthogonal; debug mode suppresses real POSTs intentionally | ✓ Good — v1.2 Phase 14 |

## Evolution

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Move shipped requirements to Validated
4. Add new requirements to Active
5. Update Context with current state

---
*Last updated: 2026-06-05 after starting v1.3 AI-based email analysis milestone*
