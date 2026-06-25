"""Shop2Parcel binary_sensor platform — operational health + email processing.

Phase 26 Plan 03 (P26-ENT-04, P26-REMOVE-02):
- HasActiveShipmentsBinarySensor removed; use Shipments Forwarded sensor instead.
- ProblemBinarySensor added as PRIMARY health indicator (device_class=PROBLEM).
  is_on when ANY of:
    1. stage2_consecutive_failures >= STAGE2_NOTIFY_THRESHOLD
    2. coordinator.quota_is_exhausted
    3. coordinator.pending_posts_depth > 0

Phase 5 (M6A-01):
- EmailProcessingActiveBinarySensor retained (DIAGNOSTIC category).
  On while a poll is fetching/parsing OR the Stage-2 queue still has items.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STAGE2_NOTIFY_THRESHOLD
from .coordinator import Shop2ParcelCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel binary_sensor platform — static entities.

    Pitfall 4: dict-shaped hass.data after Phase 5 — use ["coordinator"] key.
    Phase 26 Plan 03: HasActiveShipmentsBinarySensor removed; ProblemBinarySensor added.
    """
    coordinator: Shop2ParcelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            ProblemBinarySensor(coordinator, entry),
            EmailProcessingActiveBinarySensor(coordinator, entry),
        ]
    )


class ProblemBinarySensor(CoordinatorEntity[Shop2ParcelCoordinator], BinarySensorEntity):
    """Primary health indicator — on when any critical issue is detected (P26-ENT-04).

    Three-condition is_on:
      1. Stage-2 consecutive failures have reached the notify threshold.
      2. ParcelApp quota is exhausted (429 block window is active).
      3. Pending-posts backlog exists (quota-deferred shipments awaiting retry).

    Uses only public coordinator properties — never reads private attributes
    (RESEARCH Pitfall 4 / STRIDE T-26-05).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    # NOT DIAGNOSTIC: Problem is a primary health indicator visible in the main entity list.

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_problem"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def is_on(self) -> bool:
        """True when any critical operational issue is detected."""
        return (
            self.coordinator.stage2_consecutive_failures >= STAGE2_NOTIFY_THRESHOLD
            or self.coordinator.quota_is_exhausted
            or self.coordinator.pending_posts_depth > 0
        )


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
