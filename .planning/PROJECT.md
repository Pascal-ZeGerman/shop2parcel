# Shop2Parcel — Shopify → Parcel App Home Assistant Integration

## What This Is

Shop2Parcel is a Home Assistant custom integration that monitors Gmail (OAuth2) or any IMAP mailbox for Shopify-style shipping confirmation emails, extracts tracking data (tracking number, carrier, order number) via a two-stage pipeline — fast template parsers first, then a local Ollama LLM for emails the templates miss or need augmenting — and forwards it to parcelapp.net. Shipments appear as HA sensor entities automatically. Multiple accounts (Gmail and/or IMAP) can be configured per HA instance, each running its own polling coordinator. A configurable debug/dry-run mode allows full diagnostic output without submitting real data to parcelapp.net.

## Core Value

Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.

## Current State: v1.3 AI-based Email Analysis — SHIPPED 2026-06-17

Full two-stage extraction pipeline shipped: template parsers (Stage 1) + local Ollama LLM (Stage 2), always-on, with bounded async queue, LLM-authoritative merge, five quota-burn mitigations, loud failure surface, `Stage2Sensor` diagnostic entity, and complete README setup section.

## Previous Milestones

<details>
<summary>v1.3 AI-based Email Analysis — shipped 2026-06-17</summary>

- Two-stage extraction pipeline: template parsers (Stage 1) + local Ollama LLM (Stage 2), always-on
- `OllamaClient` with 2-pass JSON parse, NFKC normalization; `OllamaExtractor` with dynamic JSON-Schema from field list
- Bounded async queue + long-lived worker per coordinator; drop-newest backpressure + in-flight key dedup
- Five quota-burn mitigations: per-field merge guards, carrier-regex TN sanity, `temperature:0`, `MAX_STAGE2_POSTS_PER_POLL=5` cap, skip-dedup-on-failure
- `Stage2Sensor` diagnostic entity + extended `PollStats` with 6 Stage-2 counters
- User-extensible custom extraction fields as sensor attributes; persistent HA notification with 1-hour cooldown after N consecutive Stage-2 failures
- README "AI-based email analysis (v1.3)" section: 3 networking topologies, localhost foot-gun warning, model guidance, reachability sanity-check
- **Codebase:** ~22,458 LOC total (integration + tests), 616 tests passing, manifest v1.3.x
- **Config flow:** Ollama URL (required), model name, timeout, queue maxlen, custom field CRUD with locked-field disclosure

</details>

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
- ✓ Two-stage extraction pipeline: always-on Ollama Stage 2 after Stage-1 template parsers — v1.3 (Phases 15–22)
- ✓ Bounded async queue + long-lived worker per coordinator; drop-newest backpressure + in-flight key dedup — v1.3 (Phases 18–19)
- ✓ LLM-authoritative merge with per-field conflict guards + carrier-regex TN sanity + `MAX_STAGE2_POSTS_PER_POLL=5` cap — v1.3 (Phase 20)
- ✓ Stage-2 failure surface: ERROR log + `stage2_failed` activity event + persistent HA notification + skip-dedup-on-failure — v1.3 (Phase 21)
- ✓ `Stage2Sensor` diagnostic entity + PollStats Stage-2 counters + user custom extraction fields as sensor attributes — v1.3 (Phase 21)
- ✓ README "AI-based email analysis (v1.3)" section with Ollama networking guide and reachability sanity-check — v1.3 (Phase 22)

### Active (v1.4+)

- [ ] Custom extraction fields persisted across HA restarts (STORAGE_VERSION 3→4 migration)
- [ ] Stage-2 inference-latency rolling-average sensor
- [ ] "Test extraction" dry-run textarea in options flow
- [ ] Delivery status tracking — shipment sensor state reflects current delivery status (formerly deferred)

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

- **Architecture:** `DataUpdateCoordinator` (one per account) → `GmailClient` or `ImapClient` → `EmailParser` → Stage-1 template parsers → bounded `asyncio.Queue` → long-lived Stage-2 worker → `OllamaExtractor` + `merge_llm_authoritative` → `ParcelAppClient`. Sensor entities subscribe to coordinator updates.
- **Auth storage:** Gmail OAuth2 tokens and IMAP credentials stored in HA config entry `data` (encrypted at rest). Parcelapp API key and Ollama URL also in `data`.
- **Dedup:** `Shop2ParcelStore` (STORAGE_VERSION 3) persists `_submitted_tracking_numbers: OrderedDict[str, None]` (LRU cap 1000) AND `_persisted_shipments: dict[str, ShipmentData]` across HA restarts. v2→v3 migration path included.
- **Stage-2 queue:** In-memory only (HA-restart-lossy by design); full-window rescan re-discovers unprocessed emails. Drop-newest backpressure + in-flight key dedup set.
- **Parser:** Template registry (CARRIER_REGISTRY) with `detect_fn` + `parse_fn` per carrier. Shopify fallback uses BS4 `<p>` scan then regex keyword match. Stage 2 always runs after Stage 1 on sender-matched emails.
- **Merge:** `merge_llm_authoritative(stage1, stage2_result)` returns `(merged_ShipmentData, conflicts_list)`. LLM overwrites Stage-1 fields only when Stage 1 returned `None` or values match (normalized). Conflicts keep Stage 1 and emit a `stage2_conflict` activity event.
- **Environment:** Raspberry Pi dev environment with PyPI network-blocked — aiohttp/aioresponses symlinked from sibling venv.
- **Codebase:** ~22,458 LOC total (integration + tests), 616 tests passing, manifest v1.3.x.

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
| In-memory Stage-2 queue (HA-restart-lossy) | Storage migration burden not justified; full-window rescan re-discovers unprocessed emails | ✓ Good — v1.3 Phase 18 |
| Single long-lived worker per coordinator | Ollama serializes per-model + parcelapp 20/day quota — multi-worker rejected | ✓ Good — v1.3 Phase 19 |
| Drop-newest backpressure (not drop-oldest) | Drop-oldest wastes head-of-queue work and breaks FIFO activity-log ordering | ✓ Good — v1.3 Phase 18 |
| 5-mitigation quota-burn set is inseparable | All five land together — partial deployment leaves parcelapp quota exposed | ✓ Good — v1.3 Phase 20 |
| merge_llm_authoritative returns (ShipmentData, list) tuple | Keeps merge.py HA-free per D-02; coordinator emits events using returned conflicts list | ✓ Good — v1.3 Phase 20 |
| stage2_enabled derived from options (not stored) | Avoids STORAGE_VERSION bump for v1.2 → v1.3 upgrades; backward-compat out of the box | ✓ Good — v1.3 Phase 17 |
| OllamaClient 2-pass JSON parse pipeline | Pass 1 = normalize + json.loads; Pass 2 = fence-strip + normalize + json.loads on JSONDecodeError only | ✓ Good — v1.3 Phase 15 |

## Evolution

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Move shipped requirements to Validated
4. Add new requirements to Active
5. Update Context with current state

---
*Last updated: 2026-06-17 after v1.3 AI-based email analysis milestone*
