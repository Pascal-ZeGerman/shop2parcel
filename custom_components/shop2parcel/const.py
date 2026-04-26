"""Constants for the Shop2Parcel integration."""

DOMAIN = "shop2parcel"

# Phase 4: coordinator + options flow constants
CONF_POLL_INTERVAL = "poll_interval"        # minutes (int)
CONF_GMAIL_QUERY = "gmail_query"            # Gmail search query string
DEFAULT_POLL_INTERVAL = 30                  # 30 minutes (CONTEXT.md D-08)
DEFAULT_GMAIL_QUERY = "from:no-reply@shopify.com subject:shipped"

