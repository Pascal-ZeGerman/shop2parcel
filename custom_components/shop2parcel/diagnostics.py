"""HA diagnostics platform for the Shop2Parcel integration.

Implements async_get_config_entry_diagnostics which HA auto-discovers.
Adding "diagnostics": true to manifest.json is optional — HA detects
diagnostics.py by filename convention and registers it automatically.
However, the explicit declaration is recommended for hassfest/HACS validators.

Security: api_key, token, and imap_password are excluded via TO_REDACT
(T-uik-01: Information Disclosure mitigation). Any new credential field
added to entry.data must also be added to TO_REDACT.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_KEY,
    CONF_CONNECTION_TYPE,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_USERNAME,
    CONF_POLL_INTERVAL,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TO_REDACT = {CONF_API_KEY, CONF_IMAP_PASSWORD, "token", "access_token", "refresh_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for this config entry.

    Top-level keys:
      "config"           — sanitized connection info; entry_data has credentials stripped
      "poll_stats"       — PollStats fields serialised via dataclasses.asdict()
      "recent_shipments" — 10 most recent entries from coordinator.data, sorted by email_date

    Called by HA when the user clicks "Download Diagnostics" in the UI or via
    the /api/diagnostics/config_entry/<entry_id> REST endpoint.
    """
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not domain_data or "coordinator" not in domain_data:
        _LOGGER.error(
            "diagnostics requested for entry %s but coordinator not found "
            "(integration may not be loaded or setup failed)",
            entry.entry_id,
        )
        return {
            "error": "Coordinator not loaded — setup may have failed or entry is in re-auth state"
        }
    coordinator = domain_data["coordinator"]

    # Build config section — credentials stripped via TO_REDACT.
    connection_type: str = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)
    if connection_type == CONNECTION_TYPE_IMAP:
        account: str = entry.data.get(CONF_IMAP_USERNAME, "")
        if not account:
            _LOGGER.warning(
                "IMAP entry %s is missing %s — diagnostics will show empty account",
                entry.entry_id,
                CONF_IMAP_USERNAME,
            )
    else:
        # For Gmail entries, unique_id is the Google account subject identifier set
        # during OAuth flow — safe to surface as an account label.
        account = entry.unique_id or ""

    config: dict[str, Any] = {
        "connection_type": connection_type,
        "account": account,
        "poll_interval": entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
    }

    # Build poll_stats — dataclasses.asdict() recursively converts nested lists and dicts
    # to JSON-safe values (PollStats contains list[dict] and dict[str, int] fields).
    diag_obj = coordinator.diagnostics
    if not dataclasses.is_dataclass(diag_obj) or isinstance(diag_obj, type):
        _LOGGER.error(
            "coordinator.diagnostics for entry %s is not a dataclass instance (got %r) "
            "— poll_stats will be empty",
            entry.entry_id,
            type(diag_obj),
        )
        poll_stats: dict[str, Any] = {}
    else:
        poll_stats = dataclasses.asdict(diag_obj)

    # Build recent_shipments — 10 most recent by email_date. Insertion order is not
    # used because the dict is repopulated across polls and restarts in poll-discovery
    # sequence, which does not match email_date order.
    raw_data = coordinator.data
    if raw_data is not None and not isinstance(raw_data, dict):
        _LOGGER.error(
            "coordinator.data for entry %s is not a dict (got %s) — recent_shipments will be empty",
            entry.entry_id,
            type(raw_data).__name__,
        )
        raw_data = None
    data = raw_data or {}
    sorted_shipments = sorted(data.values(), key=lambda s: s.email_date or 0, reverse=True)
    recent_shipments: list[dict[str, Any]] = []
    for i, shipment in enumerate(sorted_shipments[:10]):
        if not dataclasses.is_dataclass(shipment) or isinstance(shipment, type):
            _LOGGER.error(
                "coordinator.data contains a non-dataclass value at index %d (type: %s) "
                "— skipping in diagnostics",
                i,
                type(shipment).__name__,
            )
            continue
        recent_shipments.append(dataclasses.asdict(shipment))

    return {
        "config": config,
        "poll_stats": poll_stats,
        "recent_shipments": recent_shipments,
    }
