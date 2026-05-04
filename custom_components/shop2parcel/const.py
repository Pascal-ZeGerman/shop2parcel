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
DEFAULT_GMAIL_QUERY = (
    "(from:no-reply@shopify.com OR from:mcinfo@ups.com OR "
    "from:inform@informeddelivery.usps.com OR from:USPSPackageTracker@usps.com OR "
    "from:TrackingUpdates@fedex.com) "
    "subject:(shipped OR delivered OR tracking OR package)"
)

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
DEFAULT_IMAP_SEARCH = 'SUBJECT "shipped"'

# Parcel API key (stored in config entry data, shared between config_flow and coordinator)
CONF_API_KEY = "api_key"
