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
    "from:inform@informeddelivery.usps.com OR from:TrackingUpdates@fedex.com) "
    "subject:(shipped OR delivered OR tracking OR package)"
)
