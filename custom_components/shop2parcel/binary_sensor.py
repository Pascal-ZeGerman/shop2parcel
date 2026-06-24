"""Shop2Parcel binary_sensor platform — single HasActiveShipmentsBinarySensor.

Phase 5 (ENTT-03):
- D-07: is_on = len(coordinator.data) > 0.
- D-05: unique_id = f"{DOMAIN}_{entry.entry_id}_has_active_shipments".
- D-06: shares DeviceInfo with ShipmentSensor under one Shop2Parcel device.

CoordinatorEntity automatically calls async_write_ha_state() on every
coordinator update, so is_on re-evaluates without any custom override.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Shop2ParcelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel binary_sensor platform — static entities.

    Pitfall 4: dict-shaped hass.data after Phase 5 — use ["coordinator"] key.
    """
    coordinator: Shop2ParcelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            HasActiveShipmentsBinarySensor(coordinator, entry),
            EmailProcessingActiveBinarySensor(coordinator, entry),
        ]
    )


class HasActiveShipmentsBinarySensor(CoordinatorEntity[Shop2ParcelCoordinator], BinarySensorEntity):
    """True when coordinator.data has at least one active shipment (ENTT-03)."""

    _attr_should_poll = False
    # D-02: no standard device class (None is the default)
    _attr_has_entity_name = True
    _attr_name = "Has Active Shipments"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        # D-05: stable unique_id for binary sensor
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_has_active_shipments"
        # D-06: same device as all ShipmentSensors
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def is_on(self) -> bool:
        """D-07: True when at least one shipment is in coordinator.data."""
        if self.coordinator.data is None:
            return False
        return len(self.coordinator.data) > 0


class EmailProcessingActiveBinarySensor(
    CoordinatorEntity[Shop2ParcelCoordinator], BinarySensorEntity
):
    """True while emails are actively being processed (M6A-01).

    On when a poll is fetching/parsing emails OR the Stage-2 LLM queue still has
    items waiting/draining. Off at rest.  Diagnostic-category so it groups with
    the other diagnostic entities in the HA UI rather than appearing alongside
    the shipment sensors.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Email Processing Active"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_email_processing_active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def is_on(self) -> bool:
        """True while a poll is running OR the Stage-2 queue is non-empty."""
        return self.coordinator.email_processing_active
