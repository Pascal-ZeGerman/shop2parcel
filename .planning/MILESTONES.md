# Milestones

## v1.2 Debug Switch (Shipped: 2026-06-05)

**Phases completed:** 3 phases (13, 13.1, 14), 9 plans

**Key accomplishments:**

1. `ParcelAppAlreadyAddedError` exception class added — routes already-added 400 responses as idempotent success, ending the 3-stuck-message-ID restart loop (Phase 13)
2. Store observability: `_async_load_store` / `_async_save_store` now log tracking-number count at DEBUG level (Phase 13)
3. HA Store STORAGE_VERSION bumped to 3 — persists `coordinator.data` (ShipmentData objects) across HA restarts; v2→v3 migration with per-entry type validation via `_SHIPMENT_FIELD_TYPES` (Phase 13.1)
4. GmailCoordinator and ImapCoordinator seed `_pending_shipments` from restored state; end-of-poll FIFO-trim + save block (Phase 13.1)
5. Debug/dry-run mode: 365-day window override, dedup bypass, dry-run POST suppression, per-email INFO logging, persistent HA notification lifecycle (Phase 14)
6. 12 new debug mode tests (2 per DBG requirement × Gmail/IMAP); test suite at 361 tests (Phase 14)

**Stats:** 53 files changed · +8,746 / -1,398 lines · 2026-05-19 → 2026-05-23 (4 days)

**Known deferred items at close:** 32 (see STATE.md Deferred Items)

**Archive:** [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)

---

## v1.0 MVP (Shipped: 2026-05-04)

**Phases completed:** 9 phases, 29 plans, 24 tasks

**Key accomplishments:**

- parcelapp.net POST/GET endpoints and Shopify static-token/OAuth2 auth contracts fully documented from official sources and open-source reference implementation
- 1. [Rule 1 - Bug] Fixed _CapturingExecutor to correctly distinguish build() vs execute() calls
- Dual-strategy EmailParser (BS4+lxml primary, regex fallback) with ShipmentData @dataclass(slots=True) and 15-entry Shopify-to-parcelapp carrier code lookup with pholder fallback
- aiohttp-based parcelapp.net client with all 4 error types mapped to custom exceptions, api-key in header only, no HA imports
- 1. [Rule 1 - Bug] Resolved symlinked dependency conflict preventing pip install
- One-liner:
- OAuth2FlowHandler with Gmail executor fetch, parcelapp validation via async_get_deliveries, and reauth path; 15 passing TDD tests using sys.modules HA mocking
- One-liner:
- coordinator.py
- `__init__.py`
- Wave 0 test scaffolds for 6 Phase 5 entity requirements (ENTT-01..ENTT-06) plus async_cleanup_delivered added to coordinator with 5 passing tests enforcing filter_mode='recent' and safe error handling
- sensor.py (ShipmentSensor + dynamic async_add_listener), binary_sensor.py (HasActiveShipmentsBinarySensor), and __init__.py rewired with PLATFORMS=['sensor','binary_sensor'], dict hass.data, and 24h cleanup task — turning all 8 Wave 0 RED stubs GREEN
- One-liner:
- 1. [Rule 3 - Blocking] ruff format would fail on day-one CI without pre-pass
- One-liner:
- PollStats dataclass wired to Shop2ParcelCoordinator._diagnostics, instrumenting _async_update_data to accumulate emails scanned/matched/found and keyword hits per poll cycle with per-cycle reset semantics
- 4 static DiagnosticSensor entities reading from coordinator._diagnostics (PollStats) and co-registered under the sensor platform in sensor.py
- One-liner:
- Detection functions
- DEFAULT_GMAIL_QUERY in const.py updated from Shopify-only sender filter to a four-carrier from:-anchored query (Shopify + UPS + USPS + FedEx) with broad subject scope
- 1. [Rule 1 - Bug] Added error classification to _fetch_sync (not just fetch_shipping_emails)
- 1. [Rule 1 - Documentation] conn=None grep check pattern mismatch

---

## v1.1 Debug-Ready (Shipped: 2026-05-17)

**Phases completed:** 3 phases (10–12), 9 plans

**Key accomplishments:**

1. Full-window email scanning — every poll scans all emails in `rescan_window_days`; `last_seen_message_id` and `last_imap_uid` skip gates removed
2. Tracking-number submission dedup — `Shop2ParcelStore` v2 with persisted `OrderedDict` (LRU cap 1000) survives HA restarts
3. Per-email activity log ring buffer — 50-event `deque` with subject, sender, template, outcome; exposed via diagnostics download and `ActivityLogSensor`
4. Comprehensive `_LOGGER.debug()` across coordinator, parsers, and API clients — HA native debug toggle reveals full operational detail
5. Button platform removed; coordinator split into base + `GmailCoordinator` + `ImapCoordinator` subclasses
6. v1.1.1 patch: 3 code review findings fixed, manifest bumped

**Stats:** 105 commits · 42 files changed · +5,410 / -1,116 lines · 2026-05-04 → 2026-05-17

**Known deferred items at close:** 1 (see STATE.md Deferred Items)

- `no-new-numbers-email-search-2026-05-17` — 3 stuck message IDs + dedup persistence investigation

**Archive:** [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)

---
