"""Shop2Parcel button platform — ResetEmailCacheButton."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Shop2ParcelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel button platform."""
    coordinator: Shop2ParcelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([ResetEmailCacheButton(coordinator, entry)])


class ResetEmailCacheButton(ButtonEntity):
    """Clears forwarded_ids and resets scan window so next poll rescans the corpus.

    ButtonEntity directly (not CoordinatorEntity) — this triggers an action,
    it does not display coordinator data. Direct _coordinator reference is correct.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Reset Email Cache"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_reset_email_cache"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    async def async_press(self) -> None:
        """Clear dedup cache and trigger immediate rescan from window start."""
        await self._coordinator.async_reset_forwarded_ids()
        await self._coordinator.async_request_refresh()
