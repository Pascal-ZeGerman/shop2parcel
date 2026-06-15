"""Constants for the Shop2Parcel integration."""

DOMAIN = "shop2parcel"

# Phase 4: coordinator + options flow constants
CONF_POLL_INTERVAL = "poll_interval"  # minutes (int)
CONF_GMAIL_QUERY = "gmail_query"  # Gmail search query string
DEFAULT_POLL_INTERVAL = 30  # 30 minutes (CONTEXT.md D-08)
# Phase 8 D-03: extended to capture Shopify merchant emails AND direct carrier
# shipping notifications (UPS, USPS, FedEx). The 'from:' anchor is required
# because real UPS/USPS/FedEx subjects use 'out for delivery' / 'scheduled
# for delivery' rather than 'shipped' (RESEARCH.md Gmail Query Update Research).
# User can override via Options flow at any time.
#
# QF-01 fix: removed 'label:inbox' from the broad-fallback arm. Users who
# auto-archive shipping mail (via Gmail filters) never had those messages in
# the inbox, so 'label:inbox' silently excluded them. Removing the token
# broadens recall to archived messages while '-label:spam' retains the spam
# guard. This restores correct match coverage for auto-archiving users.
DEFAULT_GMAIL_QUERY = (
    "(from:no-reply@shopify.com OR from:mcinfo@ups.com OR "
    "from:USPSInformeddelivery@email.informeddelivery.usps.com OR from:USPSPackageTracker@usps.com OR "
    "from:TrackingUpdates@fedex.com) "
    "subject:(shipped OR delivered OR tracking OR package)"
    " OR "
    "-label:spam "
    "subject:(tracking OR shipped OR shipment OR delivery OR parcel)"
)

# QF-02: Gmail-only rescan window option. Controls the minimum lookback period
# used in build_incremental_query. Allows users to widen the after: filter
# without wiping forwarded_ids (which would cause duplicate ParcelApp POSTs).
CONF_RESCAN_WINDOW_DAYS = "rescan_window_days"  # int, days; Gmail-only
DEFAULT_RESCAN_WINDOW_DAYS = 30
MIN_RESCAN_WINDOW_DAYS = 7
MAX_RESCAN_WINDOW_DAYS = 365

# Phase 9: IMAP connection + multi-account constants
CONF_CONNECTION_TYPE = "connection_type"  # str: "gmail" | "imap"
CONNECTION_TYPE_GMAIL = "gmail"
CONNECTION_TYPE_IMAP = "imap"
CONF_IMAP_HOST = "imap_host"  # str
CONF_IMAP_PORT = "imap_port"  # int
CONF_IMAP_USERNAME = "imap_username"  # str
CONF_IMAP_PASSWORD = "imap_password"  # str (encrypted in entry.data)
CONF_IMAP_TLS = "imap_tls"  # str: "ssl" | "starttls" | "none"
CONF_IMAP_SEARCH = "imap_search"  # str: IMAP SEARCH criteria
DEFAULT_IMAP_SEARCH = (
    'OR OR OR SUBJECT "shipped" SUBJECT "tracking" SUBJECT "delivery" SUBJECT "shipment"'
)

# Parcel API key (stored in config entry data, shared between config_flow and coordinator)
CONF_API_KEY = "api_key"

# PR4-C2: opt-in gate for Tier 2 broad-scan. OFF by default to prevent
# false-positive forwards to ParcelApp consuming the 20/day quota.
CONF_ENABLE_BROAD_SCAN = "enable_broad_scan"
DEFAULT_ENABLE_BROAD_SCAN = False

# Phase 10 (D-09): LRU cap for submitted_tracking_numbers OrderedDict.
MAX_SUBMITTED_TRACKING_NUMBERS = 1000

# Phase 14 (DBG-01): debug/dry-run mode toggle; stored in entry.options.
CONF_DEBUG_MODE = "debug_mode"

# Phase 14 (WR-03): per-entry notification ID prefix.
# Using a per-entry suffix prevents notification collision when multiple
# Shop2Parcel config entries exist (e.g., one Gmail + one IMAP account).
# The helper below builds the full notification_id for a given entry_id.
DEBUG_MODE_NOTIFICATION_ID_PREFIX = "shop2parcel_debug_mode"


def debug_mode_notification_id(entry_id: str) -> str:
    """Return the persistent-notification ID scoped to a single config entry.

    Using a per-entry suffix prevents notification collision when multiple
    Shop2Parcel config entries coexist (P14-WR-03).  The prefix alone
    (``shop2parcel_debug_mode``) was the pre-fix value — callers must now
    use this helper so HA's notification store keeps each entry's banner
    separate.
    """
    return f"{DEBUG_MODE_NOTIFICATION_ID_PREFIX}_{entry_id}"


# Phase 20 MRG-05 (CONTEXT.md D-08): per-poll Stage-2 POST cap.
# Caps Stage-2 POSTs at 5 per poll cycle — 25% of parcelapp's 20-POST daily
# quota. Even 4 polls/day at the cap stays below the daily limit.
# stage2_cap_notification_id mirrors debug_mode_notification_id pattern.
MAX_STAGE2_POSTS_PER_POLL: int = 5
STAGE2_CAP_NOTIFICATION_ID_PREFIX = "shop2parcel_stage2_cap"


def stage2_cap_notification_id(entry_id: str) -> str:
    """Return the persistent-notification ID for Stage-2 cap-hit events.

    Mirrors debug_mode_notification_id pattern (P14-WR-03). Using a per-entry
    suffix prevents notification collision when multiple Shop2Parcel config
    entries coexist. Fired at most once per poll cycle (D-08) so the user
    sees a single banner rather than per-job spam.
    """
    return f"{STAGE2_CAP_NOTIFICATION_ID_PREFIX}_{entry_id}"


def normalize_tracking_number(tracking_number: str) -> str:
    """Normalize a tracking number for dedup comparison.

    strip() removes whitespace from parser extraction.
    upper() handles casing inconsistencies in email content.
    """
    return tracking_number.strip().upper()


# Phase 16: Stage-2 LLM extraction (locked field set — owned by extractor,
# surfaced by Phase 17 options flow). The tuple is consumed by:
#   * extractors/ollama_extractor.py.build_schema (required keys)
#   * extractors/ollama_extractor.py._validate_fields (collision check, Plan 03)
#   * Phase 20 merge_llm_authoritative (locked-vs-custom routing)
# Order is observable downstream — JSON Schema ``required`` array semantics
# depend on declared order for some validators (D-06).
LOCKED_OLLAMA_FIELDS: tuple[str, str, str] = (
    "tracking_number",
    "carrier_name",
    "order_name",
)

# Phase 17: Ollama Stage-2 configuration constants.
# CONF_OLLAMA_URL: user-supplied Ollama server base URL (required for Stage 2;
#   empty/absent → Stage-1-only path; no default — hardcoding localhost is unsafe
#   because Pi-on-Pi co-located Docker is only one of several network topologies).
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b"
CONF_OLLAMA_TIMEOUT = "ollama_timeout"
DEFAULT_OLLAMA_TIMEOUT = 60  # seconds
CONF_QUEUE_MAXLEN = "queue_maxlen"
DEFAULT_QUEUE_MAXLEN = 32
CONF_CUSTOM_FIELDS = "custom_fields"  # list[dict]: {"name": str, "description": str | None}
# CONF_STAGE2_ENABLED: derived boolean; set in async_setup_entry from CONF_OLLAMA_URL presence.
# Never exposed as a user-editable form field (D-05, T-17-02-03).
CONF_STAGE2_ENABLED = "stage2_enabled"
CONF_FIELD_NAME = "field_name"  # str, used in add/remove options steps
CONF_FIELD_DESCRIPTION = "field_description"  # str | None
