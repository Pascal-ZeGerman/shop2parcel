"""Shop2Parcel sensor platform — one ShipmentSensor per coordinator.data entry.

Phase 5 (ENTT-01, ENTT-02, ENTT-04, ENTT-06):
- D-01: native_value is the static string "in_transit" — no per-poll status fetch.
- D-03: attributes are order_name, tracking_number, carrier, email_date.
- D-05: unique_id = f"{DOMAIN}_{entry.entry_id}_{message_id}".
- D-06: all entities share DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Shop2Parcel").

Dynamic entity addition: shipments accumulate over time as new emails arrive,
so we use the async_add_listener pattern from HA's dynamic-devices quality rule.
A `_check_shipments` callback compares current coordinator.data keys against a
known_ids set and adds entities for any new keys. Removals are NOT handled here
— Plan 01's coordinator.async_cleanup_delivered is responsible for explicit
entity_registry.async_remove() calls (RESEARCH.md Pitfall 1 — entities don't
auto-remove just because a key disappears from coordinator.data).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Shop2ParcelCoordinator
from .diagnostic_sensor import (
    EmailsMatchedSensor,
    EmailsScannedSensor,
    KeywordHitsSensor,
    TrackingNumbersFoundSensor,
)

_LOGGER = logging.getLogger(__name__)

# D-01: sensor state is the static literal — no enum, no parcelapp status fetch in v1.
STATE_IN_TRANSIT = "in_transit"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel sensor platform.

    Pitfall 4: hass.data[DOMAIN][entry.entry_id] is a dict {"coordinator": ..., "cancel_cleanup": ...}
    after Phase 5 changes to __init__.py — use ["coordinator"] key, not bare access.

    Phase 7 (D-09/D-13): 4 static diagnostic sensors are co-registered here.
    "diagnostic_sensor" is not a built-in HA platform domain and cannot be used
    in PLATFORMS directly — sensors belonging to the "sensor" domain must be
    registered from sensor.py's async_setup_entry.
    """
    coordinator: Shop2ParcelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Phase 7 (D-09): register the 4 static diagnostic sensors.
    async_add_entities(
        [
            EmailsScannedSensor(coordinator, entry),
            EmailsMatchedSensor(coordinator, entry),
            TrackingNumbersFoundSensor(coordinator, entry),
            KeywordHitsSensor(coordinator, entry),
        ]
    )

    known_ids: set[str] = set()

    @callback
    def _check_shipments() -> None:
        """Add entities for any new message_ids in coordinator.data."""
        if coordinator.data is None:
            return
        current_ids = set(coordinator.data)
        new_ids = current_ids - known_ids
        if new_ids:
            known_ids.update(new_ids)
            async_add_entities(ShipmentSensor(coordinator, entry, msg_id) for msg_id in new_ids)

    # Add entities for current coordinator.data first (handles existing data at setup time).
    _check_shipments()
    # Subscribe to future coordinator updates.
    entry.async_on_unload(coordinator.async_add_listener(_check_shipments))


class ShipmentSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """One sensor per forwarded shipment (ENTT-01)."""

    _attr_should_poll = False
    # D-02: no standard device class for parcel tracking; state_class is None
    # (string/enum "in_transit" state, not a measurement) — both are HA defaults.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
        message_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._message_id = message_id
        # D-05: stable unique_id from Gmail message ID (never changes across HA restarts)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{message_id}"
        # D-06: all entities share one device per config entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def name(self) -> str:
        """Human-readable name relative to device (per has_entity_name=True).

        Re-evaluated on each state write so updates if order_name becomes
        available after construction.  Falls back to message_id if not yet
        present in coordinator.data.
        """
        if self.coordinator.data:
            shipment = self.coordinator.data.get(self._message_id)
            if shipment is not None:
                return f"Shipment {shipment.order_name}"
        return f"Shipment {self._message_id}"

    @property
    def native_value(self) -> str:
        """D-01: static state, no per-poll parcelapp GET."""
        return STATE_IN_TRANSIT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """D-03: order_name, tracking_number, carrier, email_date.

        Returns {} when coordinator.data no longer contains this message_id
        (e.g. after async_cleanup_delivered drops the key but before the
        entity is removed from the registry — a transient state).
        """
        if self.coordinator.data is None:
            return {}
        shipment = self.coordinator.data.get(self._message_id)
        if shipment is None:
            return {}
        return {
            "order_name": shipment.order_name,
            "tracking_number": shipment.tracking_number,
            "carrier": shipment.carrier_name,
            "email_date": shipment.email_date,
        }
