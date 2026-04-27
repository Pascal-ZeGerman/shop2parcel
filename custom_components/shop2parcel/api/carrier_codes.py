"""Shopify carrier name -> parcelapp.net carrier code mapping.

Shopify sends exact strings from their FulfillmentTrackingInfo carrier list.
Full parcelapp carrier codes: https://api.parcel.app/external/supported_carriers.json

IMPORTANT: Never submit 'none' to the parcelapp API — that is a parcel-ha internal
sentinel. Use 'pholder' as fallback (valid parcelapp code, returns HTTP 200).
Every failed POST (including invalid codes -> HTTP 400) consumes one of the 20/day quota.
"""

from __future__ import annotations

_SHOPIFY_TO_PARCEL: dict[str, str] = {
    "ups": "ups",
    "fedex": "fedex",
    "usps": "usps",
    "dhl express": "dhl",
    "dhl ecommerce": "dhl",
    "canada post": "cp",
    "royal mail": "rm",
    "australia post": "au",
    "japan post (en)": "jp",
    "la poste": "lp",
    "postnl": "tntp",
    "tnt": "tnt",
    "gls": "gls",
    "dpd": "dpd",
    "poste italiane": "it",
}


def normalize_carrier(shopify_name: str) -> str:
    """Map Shopify carrier name to parcelapp carrier code.

    Case-insensitive lookup with leading/trailing whitespace stripped.
    Falls back to 'pholder' (valid parcelapp placeholder code) for unrecognized carriers.
    Never returns 'none' — that is a parcel-ha internal sentinel, not a valid API code.
    """
    return _SHOPIFY_TO_PARCEL.get(shopify_name.strip().lower(), "pholder")
