<!-- generated-by: gsd-doc-writer -->
# Configuration

Shop2Parcel is configured entirely through the Home Assistant UI — there is no `configuration.yaml` involvement. Credentials and sensitive tokens are stored in HA's encrypted config entry storage (`core.config_entries`). Runtime options are adjusted via the integration's Options flow (the gear icon in the integrations panel).

---

## Connection Type

During initial setup you choose one of two connection methods. This choice determines which credentials are collected and which options are available thereafter.

| Connection Type | Value | Description |
|-----------------|-------|-------------|
| Gmail OAuth2 | `gmail` | Connects via Google OAuth2. Requires OAuth2 client credentials set up in HA's Application Credentials UI. |
| IMAP | `imap` | Connects via standard IMAP with username and password (or app password). |

---

## Credential Settings (Config Entry `data`)

These fields are collected once during setup and stored encrypted in HA's config entry `data` dict. They cannot be changed without removing and re-adding the integration (or using the reauth flow).

### Shared (all connection types)

| Field | Key | Type | Required | Description |
|-------|-----|------|----------|-------------|
| parcelapp.net API Key | `api_key` | `str` | Yes | API key for your parcelapp.net account. Found in the parcelapp app under Settings > API. Validated at setup time by calling the view-deliveries endpoint. |

### Gmail connection

The Gmail path stores only the OAuth2 token bundle and the API key in `entry.data`. The `connection_type` key is **not** written for Gmail entries — `__init__.py` detects Gmail as the default by checking `entry.data.get(CONF_CONNECTION_TYPE, "gmail")` and falling back to `"gmail"` when the key is absent.

| Field | Key | Type | Description |
|-------|-----|------|-------------|
| OAuth2 token bundle | `token` | `dict` | Token dict issued by Google after OAuth2 consent: includes `access_token`, `refresh_token`, `token_uri`, `client_id`, `client_secret`, and `expires_in`. Managed automatically by HA's OAuth2 framework. |

The Gmail path requires OAuth2 client credentials (client ID and client secret) to be registered once via **Settings > Devices & Services > Application Credentials** before adding the integration. The authorization server is `https://accounts.google.com/o/oauth2/v2/auth` and the token endpoint is `https://oauth2.googleapis.com/token`. The scope requested is `gmail.readonly` — no write or send access is granted.

### IMAP connection

The IMAP path explicitly writes `connection_type: "imap"` into `entry.data` alongside the server credentials.

| Field | Key | Type | Required | Default | Description |
|-------|-----|------|----------|---------|-------------|
| Connection type marker | `connection_type` | `str` | — | — | Set to `"imap"` by the config flow. Used by `__init__.py` and `options_flow.py` to select the IMAP coordinator and schema. |
| IMAP Server | `imap_host` | `str` | Yes | — | Hostname of your IMAP server (e.g. `imap.gmail.com`, `imap.outlook.com`). |
| Port | `imap_port` | `int` | Yes | `993` | IMAP port. 993 for SSL; 143 for STARTTLS or unencrypted. |
| Username | `imap_username` | `str` | Yes | — | Your email address used as the IMAP login. |
| Password | `imap_password` | `str` | Yes | — | Email password or app-specific password. Stored encrypted in config entry `data`. Never logged. |
| TLS Mode | `imap_tls` | `str` | Yes | `"ssl"` | Encryption mode: `"ssl"` (port 993, recommended), `"starttls"` (port 143), or `"none"` (unencrypted, not recommended). |

The config flow tests the IMAP connection before saving credentials. If authentication fails, an `invalid_auth` error is shown and no entry is created. On success, the unique ID is set to `{username}@{host}` and the entry title is `Shop2Parcel ({username}@{host})`.

---

## Connection Type Detection Logic

`__init__.py` selects the coordinator at startup using:

```python
conn_type = entry.data.get(CONF_CONNECTION_TYPE, "gmail")
if conn_type == CONNECTION_TYPE_IMAP:
    coordinator = ImapCoordinator(hass, entry)
else:
    coordinator = GmailCoordinator(hass, entry)
```

Similarly, `options_flow.py` reads:

```python
conn_type = self.config_entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)
```

This means:

- Gmail entries have no `connection_type` key in `entry.data` — both components default to `"gmail"`.
- IMAP entries have `connection_type: "imap"` explicitly written by `async_step_imap`.

---

## Runtime Options (Config Entry `options`)

These settings are adjustable at any time via the integration's gear icon in **Settings > Devices & Services**. Saving the options form automatically reloads the integration via `OptionsFlowWithReload` — no HA restart is required.

### Common options (all connection types)

| Option | Key | Type | Default | Range / Constraint | Description |
|--------|-----|------|---------|-------------------|-------------|
| Poll Interval | `poll_interval` | `int` | `30` | 5 – 1440 minutes | How often the coordinator checks for new shipping emails. Lower values increase API call frequency. |
| Debug Mode | `debug_mode` | `bool` | `false` | — | Dry-run mode. When enabled, tracking numbers are extracted and logged but no POST requests are sent to parcelapp.net. A persistent notification is shown while this mode is active. |

### Gmail-only options

| Option | Key | Type | Default | Range / Constraint | Description |
|--------|-----|------|---------|-------------------|-------------|
| Gmail Search Query | `gmail_query` | `str` | See below | 1 – 500 characters | Gmail search query used to find shipping confirmation emails. Supports all standard Gmail search operators. Must not be empty (an empty query would match all mail). |
| Rescan Window | `rescan_window_days` | `int` | `30` | 7 – 365 days | How many days back the incremental Gmail query looks. Increasing this widens the `after:` filter. Does not cause duplicate parcelapp.net submissions — deduplication by tracking number happens before any POST. |

**Default Gmail query (`DEFAULT_GMAIL_QUERY`):**

```
(from:no-reply@shopify.com OR from:mcinfo@ups.com OR
from:inform@informeddelivery.usps.com OR from:USPSPackageTracker@usps.com OR
from:TrackingUpdates@fedex.com) subject:(shipped OR delivered OR tracking OR package)
OR
-label:spam subject:(tracking OR shipped OR shipment OR delivery OR parcel)
```

The `label:inbox` token was intentionally removed (fix QF-01): users who auto-archive shipping mail via Gmail filters never had those messages in the inbox, so the token silently excluded them. The `-label:spam` guard is retained.

The second arm is a broad fallback for shipment emails not covered by the sender-anchored first arm. Whether broad-scan results are actually forwarded to parcelapp.net is controlled by the `enable_broad_scan` option.

### IMAP-only options

| Option | Key | Type | Default | Range / Constraint | Description |
|--------|-----|------|---------|-------------------|-------------|
| IMAP Search Criteria | `imap_search` | `str` | See below | 1 – 500 characters | RFC 3501 IMAP SEARCH criteria string sent to the server on every poll. |

**Default IMAP search criteria (`DEFAULT_IMAP_SEARCH`):**

```
OR OR OR OR OR OR SUBJECT "shipped" SUBJECT "tracking" SUBJECT "delivery" SUBJECT "delivered" SUBJECT "shipment" SUBJECT "order" SUBJECT "confirmed"
```

### Broad scan gate (`enable_broad_scan`)

| Option | Key | Type | Default | Description |
|--------|-----|------|---------|-------------|
| Enable Broad Scan | `enable_broad_scan` | `bool` | `false` | Opt-in Tier 2 scan gate. When `false` (default), only emails matched by the sender-anchored portion of the query are forwarded to parcelapp.net. Enabling this allows the broad subject-only fallback arm to also trigger forwards. Off by default to prevent false-positive forwards that consume the 20/day parcelapp.net add-delivery quota. |

> **Note:** `enable_broad_scan` is defined in `const.py` but is not currently surfaced in the Options flow UI form (`options_flow.py`). It can be set programmatically via the config entry options dict if needed.

---

## Persistent Storage (HA Store)

In addition to the config entry, Shop2Parcel writes a per-entry HA Store file to prevent duplicate parcelapp.net submissions across HA restarts.

**Storage key:** `shop2parcel.{entry_id}` (stored in `.storage/` within the HA config directory)

**Schema version:** 2

| Field | Type | Description |
|-------|------|-------------|
| `submitted_tracking_numbers` | `list[str]` | Tracking numbers already forwarded to parcelapp.net. Capped at 1000 entries (`MAX_SUBMITTED_TRACKING_NUMBERS`). Oldest entry is evicted when the cap is reached (LRU via `OrderedDict`). Normalized to uppercase before storage. |
| `quota_exhausted_until` | `int \| null` | Unix epoch timestamp until which the parcelapp.net add-delivery quota is exhausted. `null` when not currently throttled. When set, polling continues but POST requests are skipped until this timestamp passes. |

The store is loaded once at startup (before the first coordinator refresh) in `async_setup_entry`. A v1-to-v2 migration runs automatically if an older store file is detected. On migration, `submitted_tracking_numbers` resets to empty — tracking numbers already in parcelapp.net may be re-submitted once on the first post-migration poll. <!-- VERIFY: parcelapp.net handles duplicate submissions gracefully (returns 200 or equivalent non-error) -->

---

## Security Notes

- `api_key` and `imap_password` are stored in config entry `data`, which HA encrypts in `core.config_entries`. They are never written to `configuration.yaml`.
- Neither field is ever emitted to the HA log. Exception handlers catch error types only, not message content (enforced by security constraints T-03-03-01 and T-09-03).
- The Gmail OAuth2 scope is restricted to `gmail.readonly`. No other Gmail permission is requested.
- The Gmail unique ID is the account's email address. The IMAP unique ID is `{username}@{host}`. Attempting to add a duplicate account is blocked with an `already_configured` or `already_configured_imap` abort.

---

## Reauth

If authentication fails at runtime, the coordinator raises `ConfigEntryAuthFailed`, which triggers HA's built-in reauth flow automatically. The reauth path branches on `connection_type`:

- **Gmail:** The reauth confirm dialog re-runs the OAuth2 redirect. The parcelapp.net API key is not re-requested.
- **IMAP:** The reauth IMAP form collects a new password. Host, port, username, and TLS mode are pre-filled from the existing entry (password is never pre-filled) and can be updated if needed.
