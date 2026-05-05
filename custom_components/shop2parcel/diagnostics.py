"""HA diagnostics platform for the Shop2Parcel integration.

Implements async_get_config_entry_diagnostics which HA auto-discovers.
No manifest.json change needed — HA detects diagnostics.py by convention.

Security: api_key, token, and imap_password are intentionally excluded from
the returned dict (T-uik-01: Information Disclosure mitigation).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_IMAP_USERNAME,
    CONF_POLL_INTERVAL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for this config entry.

    Top-level keys:
      "config"           — sanitized connection info (no credentials)
      "poll_stats"       — PollStats accumulator fields as primitives
      "recent_shipments" — last 10 entries from coordinator.data

    Called by HA when the user clicks "Download Diagnostics" in the UI.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Build config section — redact api_key, token, imap_password.
    connection_type: str = entry.data.get(CONF_CONNECTION_TYPE, "gmail")
    if connection_type == CONNECTION_TYPE_IMAP:
        account: str = entry.data[CONF_IMAP_USERNAME]
    else:
        account = entry.unique_id or ""

    config: dict[str, Any] = {
        "connection_type": connection_type,
        "account": account,
        "poll_interval": entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    }

    # Build poll_stats from the PollStats dataclass — all fields are primitives.
    poll_stats: dict[str, Any] = dataclasses.asdict(coordinator._diagnostics)

    # Build recent_shipments — cap at last 10 items.
    data = coordinator.data or {}
    recent_values = list(data.values())[-10:]
    recent_shipments: list[dict[str, Any]] = [
        dataclasses.asdict(shipment) for shipment in recent_values
    ]

    return {
        "config": config,
        "poll_stats": poll_stats,
        "recent_shipments": recent_shipments,
    }
