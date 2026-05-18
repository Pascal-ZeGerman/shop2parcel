# Shop2Parcel — Shopify → Parcel App Home Assistant Integration

## What This Is

Shop2Parcel is a Home Assistant custom integration that monitors Gmail (OAuth2) or any IMAP mailbox for Shopify shipping confirmation emails, extracts tracking data (tracking number, carrier, order number), and forwards it to parcelapp.net. Shipments appear as HA sensor entities automatically — no manual entry. Multiple accounts (Gmail and/or IMAP) can be configured per HA instance, each running its own polling coordinator.

## Core Value

Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.

## Current State: v1.1 Shipped

**Shipped:** 2026-05-17 — v1.1 Debug-Ready complete.

- Full-window scanning always on; tracking-number-based dedup (LRU 1000) persisted across restarts
- Per-email activity log ring buffer (50 events) + ActivityLogSensor + diagnostics download
- Comprehensive `_LOGGER.debug()` — HA native debug toggle reveals full operational detail
- Coordinator split into `GmailCoordinator` + `ImapCoordinator` subclasses
- **Codebase:** ~3,600 LOC integration + ~6,000 LOC tests, 265 tests passing, manifest v1.1.1
- **Connection methods:** Gmail OAuth2 and IMAP (app-password / SSL / STARTTLS)
- **Carriers supported:** Shopify standard, UPS, USPS, FedEx

**Next:** `/gsd:new-milestone` to plan v1.2.

## Previous Milestones

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

### Active (v1.2)

- [ ] Dry-run / debug mode — configurable mode disabling Parcel App routing, dedup, and setting scan window to 365d; posts verbose per-email diagnostic output (SEED-001)
- [ ] Fix dedup store persistence: 3 stuck message IDs loop with "already added" 400s on every HA restart (deferred from v1.1 debug session)

### Queued (future milestones)

- [ ] Delivery status tracking — shipment sensor state reflects current delivery status
- [ ] Configurable cleanup grace period for delivered shipments (currently hardcoded 24h)
- [ ] Reauth flow for IMAP credential failures (currently logs error, no HA Repairs notification)
- [ ] Deduplication against tracking numbers already in parcelapp.net GET endpoint (server-side cross-check)

### Out of Scope

- Shopify mobile/storefront API reverse-engineering — replaced by Gmail email parsing approach
- Webhook-based push integration — polling is sufficient and simpler for HA
- Writing back to Shopify (order updates, cancellations) — read-only
- Multi-store Shopify support — single store per integration instance
- European carrier templates (Amazon DE, DHL, DPD, Zalando, OTTO) — US-focused product

## Context

- **Architecture:** `DataUpdateCoordinator` (one per account) → `GmailClient` or `ImapClient` → `EmailParser` → `ParcelAppClient`. Sensor entities subscribe to coordinator updates.
- **Auth storage:** Gmail OAuth2 tokens and IMAP credentials stored in HA config entry `data` (encrypted at rest). Parcelapp API key also in `data`.
- **Dedup:** `Shop2ParcelStore` (STORAGE_VERSION 2) persists `_submitted_tracking_numbers: OrderedDict[str, None]` (LRU cap 1000) across HA restarts. Replaces v1 message-ID/UID dedup.
- **Parser:** Template registry (CARRIER_REGISTRY) with `detect_fn` + `parse_fn` per carrier. Shopify fallback uses BS4 `<p>` scan then regex keyword match.
- **Environment:** Raspberry Pi dev environment with PyPI network-blocked — aiohttp/aioresponses symlinked from sibling venv.

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

## Evolution

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Move shipped requirements to Validated
4. Add new requirements to Active
5. Update Context with current state

---
*Last updated: 2026-05-17 after v1.1 Debug-Ready milestone*
