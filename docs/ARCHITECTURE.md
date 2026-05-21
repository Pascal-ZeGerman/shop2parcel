<!-- generated-by: gsd-doc-writer -->
# Architecture

Shop2Parcel is a Home Assistant custom integration that monitors an email inbox (Gmail via OAuth2 or any IMAP server) for Shopify shipping confirmation emails and direct carrier notifications. When a shipping email is found, the integration parses the tracking number and carrier, deduplicates it against previously submitted numbers, then POSTs the shipment to [parcelapp.net](https://web.parcelapp.net). Each successfully forwarded shipment appears as a sensor entity in Home Assistant.

The integration follows the standard HA `cloud_polling` pattern: a `DataUpdateCoordinator` subclass drives all external I/O on a configurable interval (default 30 minutes), and `CoordinatorEntity` subclasses read from the coordinator's data dict without making independent API calls.

---

## Component Diagram

```mermaid
graph TD
    subgraph HA["Home Assistant Core"]
        CF[config_flow.py<br/>OAuth2FlowHandler] --> |entry.data| INIT
        OF[options_flow.py<br/>OptionsFlowHandler] --> |entry.options| INIT
        INIT[__init__.py<br/>async_setup_entry] --> |constructs| COORD
        INIT --> |registers| CLEANUP[cleanup timer<br/>async_track_time_interval]

        COORD{Shop2ParcelCoordinator<br/>base class} --> |subclass| GC[GmailCoordinator]
        COORD --> |subclass| IC[ImapCoordinator]

        GC --> |uses| GCLIENT[api/gmail_client.py<br/>GmailClient]
        IC --> |uses| ICLIENT[api/imap_client.py<br/>ImapClient]

        GC --> |uses| PARSER[api/email_parser.py<br/>EmailParser]
        IC --> |uses| PARSER

        GC --> |uses| PAPP[api/parcelapp.py<br/>ParcelAppClient]
        IC --> |uses| PAPP

        COORD --> |persists dedup state| STORE[HA Store<br/>shop2parcel.{entry_id}]

        COORD --> |data dict| SENSOR[sensor.py<br/>ShipmentSensor x N]
        COORD --> |data dict| BSENSOR[binary_sensor.py<br/>HasActiveShipmentsBinarySensor]
        COORD --> |diagnostics| DSENSOR[diagnostic_sensor.py<br/>DiagnosticSensor x 6]
        COORD --> |diagnostics| DIAG[diagnostics.py<br/>async_get_config_entry_diagnostics]
    end

    GCLIENT --> |Gmail REST API| GMAIL[(Gmail API)]
    ICLIENT --> |imaplib| IMAP[(IMAP Server)]
    PAPP --> |HTTPS POST| PARCEL[(parcelapp.net)]
    CLEANUP --> |GET deliveries| PAPP
```

---

## Directory Structure

```
custom_components/shop2parcel/
├── __init__.py               # Entry point: async_setup_entry / async_unload_entry
├── manifest.json             # HA integration manifest (version, requirements, iot_class)
├── const.py                  # All constants and normalize_tracking_number()
├── config_flow.py            # OAuth2FlowHandler — Gmail and IMAP setup + reauth flows
├── options_flow.py           # OptionsFlowHandler — poll interval, query, debug mode
├── application_credentials.py  # Google OAuth2 authorization server declaration
├── coordinator.py            # Shop2ParcelCoordinator base class, PollStats, Shop2ParcelStore
├── gmail_coordinator.py      # GmailCoordinator — Gmail poll path
├── imap_coordinator.py       # ImapCoordinator — IMAP poll path
├── sensor.py                 # ShipmentSensor + diagnostic sensor registration
├── binary_sensor.py          # HasActiveShipmentsBinarySensor
├── diagnostic_sensor.py      # 6 diagnostic sensor entity classes
├── diagnostics.py            # async_get_config_entry_diagnostics (HA diagnostics platform)
└── api/
    ├── __init__.py
    ├── carrier_codes.py      # Shopify carrier name → parcelapp carrier code mapping
    ├── email_parser.py       # EmailParser: tiered HTML/regex/broad-scan strategy
    ├── exceptions.py         # Custom exception taxonomy (Gmail, IMAP, ParcelApp)
    ├── gmail_client.py       # GmailClient: async wrapper for google-api-python-client
    ├── imap_client.py        # ImapClient: async wrapper for imaplib
    └── parcelapp.py          # ParcelAppClient: async aiohttp client for parcelapp.net API
```

---

## Data Flow

### Poll Cycle (Gmail path)

1. **Token refresh** — `GmailCoordinator._async_update_data()` calls `OAuth2Session.async_ensure_token_valid()` to silently refresh the short-lived access token. On token failure, raises `ConfigEntryAuthFailed` which triggers HA's built-in reauth flow.
2. **List messages** — `GmailClient.async_list_messages()` queries the Gmail API with the configured search query and an `after:` filter based on `rescan_window_days` (default 30 days). Returns paginated message metadata.
3. **Fetch body** — For each message, `GmailClient.async_get_message()` fetches the full MIME payload. `extract_html_body()` decodes the base64url HTML part; `extract_text_body()` is used as fallback and wrapped in `<pre>` tags for the parser.
4. **Parse** — `EmailParser.parse()` runs the tiered strategy pipeline (see Parsing Strategies below). Returns a `ParseResult` containing a `ShipmentData` or `None`.
5. **Dedup** — The normalized tracking number is checked against `_submitted_tracking_numbers` (an `OrderedDict` capped at 1,000 entries). Duplicates are counted in `PollStats` and skipped.
6. **Quota guard** — If `_quota_exhausted_until` is set and still in the future, the POST step is skipped for the current cycle.
7. **POST to parcelapp** — `ParcelAppClient.async_add_delivery()` sends `tracking_number`, `carrier_code` (normalized via `carrier_codes.normalize_carrier()`), and `description` (order name or tracking number).
8. **Record success** — The normalized tracking number is appended to `_submitted_tracking_numbers` and persisted to the HA Store immediately via `_async_save_store()`. `coordinator.data[msg_id] = shipment` causes HA to push state updates to all `CoordinatorEntity` subscribers.

### Poll Cycle (IMAP path)

Identical to the Gmail path except steps 1–3 differ:
- No OAuth2 token refresh — IMAP credentials come from `entry.data` directly.
- `ImapClient.fetch_shipping_emails()` opens a synchronous `imaplib` session in an executor thread, issues `EXAMINE INBOX` (read-only), then `UID SEARCH SINCE {since_date} {search_criteria}`, and fetches each matching message with `BODY.PEEK[]` (no `\Seen` flag mutation).
- Returns `list[dict]` with keys `uid` (int) and `raw` (bytes). The UID string is used as the `message_id` key in `coordinator.data`.
- The `since_date` is computed from `rescan_window_days` in RFC 3501 `DD-Mon-YYYY` format using a fixed English month abbreviation table (not `strftime('%b')` which is locale-dependent).

### Delivered Shipment Cleanup

`async_cleanup_delivered()` runs once every 24 hours via `async_track_time_interval`. It calls `ParcelAppClient.async_get_deliveries(filter_mode="recent")` and removes any entries from `coordinator.data` whose tracking number has `status_code == 0` (completed). Entity registry removal is explicit — HA does not auto-remove entities when a key disappears from `coordinator.data`.

---

## Key Classes

### `Shop2ParcelCoordinator` (`coordinator.py`)

Base class, extends `DataUpdateCoordinator[dict[str, ShipmentData]]`.

| Method / Attribute | Description |
|---|---|
| `__init__(hass, entry)` | Constructs store, empty `_submitted_tracking_numbers` `OrderedDict`, `PollStats` accumulator |
| `diagnostics` (property) | Read-only view of `_diagnostics: PollStats` |
| `_async_load_store()` | Hydrates `_submitted_tracking_numbers` and `_quota_exhausted_until` from HA Store. **Must be called before `async_config_entry_first_refresh()`** |
| `_async_save_store()` | Persists dedup state to Store (called after every successful POST) |
| `async_cleanup_delivered(now)` | Removes delivered shipments from `coordinator.data` and the entity registry; called by daily timer |

### `GmailCoordinator` (`gmail_coordinator.py`)

Extends `Shop2ParcelCoordinator`. Sets `self._email_client = GmailClient(hass.async_add_executor_job)`.

| Method | Description |
|---|---|
| `_async_update_data()` | Full Gmail poll cycle: token refresh → list messages → fetch bodies → parse → dedup → POST |

### `ImapCoordinator` (`imap_coordinator.py`)

Extends `Shop2ParcelCoordinator`. Sets `self._email_client = ImapClient(hass.async_add_executor_job)`.

| Method | Description |
|---|---|
| `_async_update_data()` | Full IMAP poll cycle: compute `since_date` → fetch via IMAP → parse → dedup → POST |

### `GmailClient` (`api/gmail_client.py`)

| Method | Description |
|---|---|
| `__init__(async_add_executor_job)` | Injects executor callable; no HA imports |
| `_get_service(access_token)` | Returns cached `googleapiclient` service, rebuilding only when token rotates |
| `async_list_messages(access_token, query, rescan_window_days)` | Returns `(list[dict], effective_query_str)` — paginates all results |
| `async_get_message(access_token, message_id)` | Fetches full MIME payload for one message |

Module-level helpers: `build_incremental_query(base_query, rescan_window_days)`, `extract_html_body(payload)`, `extract_text_body(payload)`, `_classify_gmail_error(err)`.

### `ImapClient` (`api/imap_client.py`)

| Method | Description |
|---|---|
| `__init__(async_add_executor_job)` | Injects executor callable; no HA imports |
| `fetch_shipping_emails(host, port, username, password, tls_mode, search_criteria, since_date)` | Returns `list[dict]` with `uid` (int) and `raw` (bytes); runs entire session in one executor call |
| `_fetch_sync(...)` | Synchronous imaplib session in executor thread; uses `EXAMINE` (read-only) + `BODY.PEEK[]` (no `\Seen` flag mutation) |

Module-level helpers: `extract_html_body_imap(raw_bytes)`, `extract_text_body_imap(raw_bytes)`, `_classify_imap_error(err)`.

### `EmailParser` (`api/email_parser.py`)

| Method | Description |
|---|---|
| `__init__(enable_broad_scan)` | `enable_broad_scan=False` by default; gates Tier 2 sweep |
| `parse(html, message_id, email_date)` | Returns `ParseResult` (never `None`); runs carrier registry then tiered Shopify strategies |
| `_parse_html_template(html, message_id, email_date)` | Strategy 1: BeautifulSoup on `<p>`/`<td>` elements + href fallback |
| `_parse_regex_tier1(html, message_id, email_date)` | Strategy 2: labeled keyword regex (`Tracking number: ...`) + href fallback |
| `_parse_regex_tier2(html, message_id, email_date)` | Strategy 3: broad token sweep (opt-in, default off) |

### `ParcelAppClient` (`api/parcelapp.py`)

| Method | Description |
|---|---|
| `__init__(session, api_key)` | Receives injected `aiohttp.ClientSession`; never creates its own |
| `async_add_delivery(tracking_number, carrier_code, description)` | `POST https://api.parcel.app/external/add-delivery/`; raises typed exceptions on all error conditions |
| `async_get_deliveries(filter_mode)` | `GET https://api.parcel.app/external/deliveries/`; used for cleanup and initial dedup |

### `PollStats` (`coordinator.py`)

`@dataclass(slots=True)` — in-memory diagnostic accumulator, reset on HA restart. Never persisted.

Key fields: `emails_returned_total`, `emails_scanned_total`, `emails_matched_total`, `tracking_numbers_found_total`, `keyword_hits_total`, `scan_events` (deque, maxlen=50), `scan_events_total`, `last_poll_*` fields (reset at the top of each poll cycle).

### `Shop2ParcelStore` (`coordinator.py`)

Extends `homeassistant.helpers.storage.Store`. Implements `_async_migrate_func` for v1→v2 migration (drops `forwarded_ids`/`last_imap_uid` schema; seeds `submitted_tracking_numbers` as empty list).

Store schema (version 2):
```json
{
  "submitted_tracking_numbers": ["1Z...", "9400..."],
  "quota_exhausted_until": null
}
```

---

## Parsing Strategies

`EmailParser.parse()` evaluates strategies in this order. First match wins.

| Priority | Strategy | Condition | Description |
|---|---|---|---|
| 0 | Carrier template registry | HTML contains `ups.com` / `usps.com` / `fedex.com` AND not `shopify` | Carrier-specific bounded regex (`_UPS_TRACKING_RE`, `_USPS_TRACKING_RE`, `_FEDEX_TRACKING_RE`) + href fallback. Sets `strategy_used` to `ups_template`, `usps_template`, or `fedex_template` |
| 1 | HTML template (`html_template`) | Always attempted if carrier template fails | BeautifulSoup scan of `<p>` and `<td>` elements for order `#`, carrier `via`, and 10–40 char alphanumeric tokens validated against `_TRACKING_PATTERNS` |
| 2 | Regex Tier 1 (`regex_fallback`) | HTML template finds no tracking | Full `get_text()` + labeled anchor `Tracking number: ...` regex + href fallback via `_extract_tracking_from_hrefs()` |
| 3 | Regex Tier 2 (`broad_regex`) | Tier 1 fails AND `enable_broad_scan=True` AND no carrier detected | Sweeps all 10–40 char tokens and href query params; returns longest match; sets `candidate_tokens` in `ParseResult` |

Tracking number validation uses `_TRACKING_PATTERNS`:
- UPS: `^1Z[A-Z0-9]{16}$`
- USPS domestic: `^9[12345][0-9]{15,24}$`
- USPS international: `^[A-Z]{2}[0-9]{9}[A-Z]{2}$`
- FedEx: `^(?:[0-9]{12}|[0-9]{15}|[0-9]{20})$`
- DHL: `^[0-9]{10,11}$`

---

## Entity Model

All entities share one `DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Shop2Parcel")` per config entry.

| Entity Class | Platform | `unique_id` pattern | State | Key attributes |
|---|---|---|---|---|
| `ShipmentSensor` | `sensor` | `shop2parcel_{entry_id}_{message_id}` | `"in_transit"` (static) | `order_name`, `tracking_number`, `carrier`, `email_date` |
| `HasActiveShipmentsBinarySensor` | `binary_sensor` | `shop2parcel_{entry_id}_has_active_shipments` | `True` when `len(coordinator.data) > 0` | — |
| `EmailsScannedSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_emails_scanned` | `emails_returned_total` | `last_poll_returned`, `query_used`, `effective_query_used`, `poll_duration_ms` |
| `NewEmailsInspectedSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_new_emails_inspected` | `emails_scanned_total` | `last_poll_count` |
| `EmailsMatchedSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_emails_matched` | `emails_matched_total` | `last_poll_matched`, `last_poll_skip_reasons` |
| `TrackingNumbersFoundSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_tracking_numbers_found` | `tracking_numbers_found_total` | `last_poll_found` |
| `KeywordHitsSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_keyword_hits` | `keyword_hits_total` | `last_poll_hits`, `per_keyword` |
| `ActivityLogSensor` | `sensor` (diagnostic) | `shop2parcel_{entry_id}_activity_log` | `scan_events_total` | `recent_events` (last 10 scan event dicts) |

Diagnostic sensors are registered in `sensor.py::async_setup_entry` because `"diagnostic_sensor"` is not a built-in HA platform domain. `ShipmentSensor` instances are added dynamically via `coordinator.async_add_listener` callback — new entities appear when `coordinator.data` gains a key not present at setup time.

---

## Configuration and Secrets Handling

Credentials are stored in `entry.data` (HA's encrypted config entry storage), never in `entry.options` or `configuration.yaml`.

| Key (`CONF_*`) | Storage | Description |
|---|---|---|
| `api_key` | `entry.data` | parcelapp.net API key |
| `token` | `entry.data` | Gmail OAuth2 token dict (access + refresh token) |
| `imap_password` | `entry.data` | IMAP account password |
| `imap_host`, `imap_port`, `imap_username`, `imap_tls` | `entry.data` | IMAP server connection details |
| `connection_type` | `entry.data` | `"gmail"` or `"imap"` |
| `poll_interval` | `entry.options` | Poll interval in minutes (5–1440, default 30) |
| `gmail_query` | `entry.options` | Gmail search query string |
| `imap_search` | `entry.options` | IMAP SEARCH criteria string |
| `rescan_window_days` | `entry.options` | Lookback window in days (7–365, default 30; Gmail only) |
| `enable_broad_scan` | `entry.options` | Gate for Tier 2 parser (default `False`) |
| `debug_mode` | `entry.options` | Dry-run mode — suppresses all POSTs (default `False`) |

The `diagnostics.py` module redacts `api_key`, `imap_password`, `token`, `access_token`, and `refresh_token` from the HA diagnostics download (`TO_REDACT` set).

The `OptionsFlowHandler` subclasses `OptionsFlowWithReload` — saving options automatically reloads the config entry, re-instantiating the coordinator with the new `update_interval`.

---

## Error Handling Strategy

The integration separates error taxonomy across two layers.

**`api/` layer** — raises typed exceptions; no HA imports:

| Exception | Meaning |
|---|---|
| `GmailAuthError` | OAuth2 token expired or revoked |
| `GmailTransientError` | Network failure or Gmail 5xx |
| `ImapAuthError` | IMAP login failure |
| `ImapTransientError` | IMAP connection failure, timeout, or socket error |
| `ParcelAppAuthError` | Invalid `api-key` (HTTP 401/403) |
| `ParcelAppQuotaError` | HTTP 429 — 20/day add-delivery quota exhausted; carries optional `reset_at` epoch |
| `ParcelAppTransientError` | Network error or parcelapp 5xx |
| `ParcelAppInvalidTrackingError` | HTTP 400 with unrecognized tracking number; still consumes one quota slot |
| `ParcelAppAlreadyAddedError` | HTTP 400 with `"You have already added this delivery to the app"` — treated as idempotent success |

**Coordinator layer** — translates to HA exceptions:

| Condition | HA exception raised | Effect |
|---|---|---|
| `GmailAuthError`, `ImapAuthError`, `ParcelAppAuthError` | `ConfigEntryAuthFailed` | HA triggers reauth flow |
| `GmailTransientError`, `ImapTransientError` | `UpdateFailed` | HA retains last known state; logs warning; retries next poll |
| `ParcelAppQuotaError` | Sets `_quota_exhausted_until`; skips POST for the rest of the cycle | No exception raised — poll continues without forwarding |
| `ParcelAppTransientError` | Logs warning; `continue` — skips this message | No exception raised — remaining messages are still processed |
| `ParcelAppInvalidTrackingError` | Logs error; records normalized TN in dedup store | Suppresses infinite retries for malformed tracking numbers |
| `ParcelAppAlreadyAddedError` | Records normalized TN in dedup store | Permanent dedup suppression |

`async_cleanup_delivered()` catches all exceptions internally and returns early without raising — it is a background maintenance task, not a poll cycle, so raising would violate the `async_track_time_interval` callback contract.

---

## Deduplication

Dedup is tracking-number-based (not message-ID-based, which was the v1 approach).

1. After a successful POST (or `ParcelAppAlreadyAddedError`), the normalized tracking number (`strip().upper()`) is appended as a key to `_submitted_tracking_numbers: OrderedDict[str, None]`.
2. The `OrderedDict` is capped at `MAX_SUBMITTED_TRACKING_NUMBERS = 1000` entries using `popitem(last=False)` (LRU eviction of the oldest entry).
3. On every poll cycle, before any POST attempt, the normalized TN is checked in O(1) against the dict.
4. The current state of `_submitted_tracking_numbers` and `_quota_exhausted_until` is persisted to the HA Store after every mutation.
5. On HA startup, `_async_load_store()` hydrates these fields before the first refresh, preventing duplicate POSTs across restarts.

---

## Connection Type Selection

The `connection_type` key in `entry.data` determines which coordinator is instantiated at setup time:

```python
# __init__.py — async_setup_entry
if conn_type == CONNECTION_TYPE_IMAP:
    coordinator = ImapCoordinator(hass, entry)
else:
    coordinator = GmailCoordinator(hass, entry)
```

**Gmail path** requires:
- A Google Cloud project with Gmail API enabled
- OAuth2 credentials (client ID + client secret) registered in HA's Application Credentials UI
- `https://www.googleapis.com/auth/gmail.readonly` scope
- `google-api-python-client>=2.194.0` and `google-auth>=2.0.0` (declared in `manifest.json` `requirements`)

**IMAP path** requires:
- IMAP server host, port, username, password, and TLS mode (`ssl` / `starttls` / `none`)
- Uses Python's stdlib `imaplib`; no extra requirements
- Opens a fresh connection per poll (stateful IMAP connections must not be shared across threads)

---

## CI/CD Pipeline

| Workflow | File | Trigger | Jobs |
|---|---|---|---|
| `pytest + lint` | `.github/workflows/pytest.yml` | Push and PR on all branches | `pytest` (Python 3.14, `pytest tests/ -v --tb=short`), `lint` (ruff check, ruff format --check, mypy) |
| `hassfest` | `.github/workflows/hassfest.yml` | Push and PR on all branches | Validates `manifest.json` via `home-assistant/actions/hassfest` |
| `HACS Action` | `.github/workflows/hacs.yml` | Push and PR on all branches | Validates HACS repo structure (`category: integration`, `ignore: brands`) |
| `Release` | `.github/workflows/release.yml` | Push of `v*` tag | Validates that `manifest.json` version matches the tag, auto-detects pre-release (`-rc`/`-beta`/`-alpha`), creates GitHub Release with auto-generated notes |

The release workflow enforces that `manifest.json` `version` must match the pushed tag before the release is created. Tags containing `-rc`, `-beta`, or `-alpha` are published as GitHub pre-releases automatically.
