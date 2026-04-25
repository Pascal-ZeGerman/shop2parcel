"""The Shop2Parcel integration.

Phase 3: Near-stub async_setup_entry — validates parcelapp API key and stores
a placeholder in hass.data for Phase 4 to replace with a DataUpdateCoordinator.

Phase boundary:
- Phase 3 owns: credential validation, hass.data placeholder, entry unload
- Phase 4 owns: coordinator instantiation, platform forwarding, options flow
- Phase 5 owns: sensor/binary_sensor platform setup
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.exceptions import ParcelAppAuthError, ParcelAppTransientError
from .api.parcelapp import ParcelAppClient
from .const import DOMAIN

# Phase 3: no platforms yet. Phase 4 adds coordinator; Phase 5 populates this list.
PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shop2Parcel from a config entry.

    Smoke-tests the parcelapp.net API key by calling the view-deliveries endpoint
    (20/hour quota — does not consume the 20/day add-delivery quota).

    Raises:
        ConfigEntryAuthFailed: API key is invalid (HTTP 401/403). HA surfaces a
            Repairs notification and offers to rerun the config flow.
        ConfigEntryNotReady: API is unreachable (network error, HTTP 5xx). HA will
            retry setup after a delay without user intervention.
    """
    api_key: str = entry.data["api_key"]
    session = async_get_clientsession(hass)
    client = ParcelAppClient(session=session, api_key=api_key)

    try:
        await client.async_get_deliveries()
    except ParcelAppAuthError as err:
        raise ConfigEntryAuthFailed(
            "Invalid parcelapp.net API key — reconfigure the integration"
        ) from err
    except ParcelAppTransientError as err:
        raise ConfigEntryNotReady(
            "Cannot connect to parcelapp.net — will retry"
        ) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}  # Phase 4 replaces with coordinator instance
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Phase 3: removes hass.data placeholder only. No platform unloads needed
    (PLATFORMS is empty). Phase 4 extends this to unload coordinator + platforms.
    """
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
