"""Shop2Parcel sensor platform — operational-health and diagnostic sensors.

Phase 26 Plan 03 (P26-ENT-01..03, P26-REMOVE-01):
- ShipmentSensor and per-shipment dynamic-add machinery removed.
- HasActiveShipmentsBinarySensor removed (handled in binary_sensor.py Plan 03).
- Three new primary (non-diagnostic) operational sensors registered:
    1. ShipmentsForwardedSensor  — TOTAL_INCREASING; reads coordinator.total_forwarded
    2. LastForwardedSensor        — TIMESTAMP device class; reads coordinator.last_forwarded_ts
    3. ParcelAppQuotaSensor       — estimate max(0, PARCELAPP_DAILY_LIMIT - used_today)

Phase 7 (D-09/D-13): 13 static diagnostic sensors are still co-registered here.
"diagnostic_sensor" is not a built-in HA platform domain and cannot be used
in PLATFORMS directly — sensors belonging to the "sensor" domain must be
registered from sensor.py's async_setup_entry.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PARCELAPP_DAILY_LIMIT
from .coordinator import Shop2ParcelCoordinator
from .diagnostic_sensor import (
    ActivityLogSensor,
    EmailsMatchedSensor,
    EmailsParsedByLLMSensor,
    EmailsScannedSensor,
    EmailsSentToLLMSensor,
    KeywordHitsSensor,
    NewEmailsInspectedSensor,
    OllamaLatencySensor,
    OllamaParseQualitySensor,
    PendingPostsSensor,
    Stage2ConsecutiveFailuresSensor,
    Stage2Sensor,
    TrackingNumbersFoundSensor,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel sensor platform.

    Pitfall 4: hass.data[DOMAIN][entry.entry_id] is a dict {"coordinator": ..., "cancel_cleanup": ...}
    after Phase 5 changes to __init__.py — use ["coordinator"] key, not bare access.

    Phase 7 (D-09): 13 static diagnostic sensors co-registered here.
    Phase 26 Plan 03 (P26-ENT-01..03): 3 primary operational sensors registered here.
    """
    coordinator: Shop2ParcelCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Phase 7 (D-09): register static diagnostic sensors.
    # Phase 11 (ACTLOG-04): ActivityLogSensor added as 6th diagnostic sensor.
    # Phase 21 (DIAG-01): Stage2Sensor added as 7th diagnostic sensor — unconditionally
    #   (no stage2_enabled gate: Pitfall 6). Stage-1-only users see zero values.
    # LLM performance sensors registered unconditionally; Stage-1-only users see zero/None.
    # Phase 26 Plan 03 (P26-ENT-01..03): 3 new primary operational sensors appended.
    async_add_entities(
        [
            # 13 diagnostic sensors
            EmailsScannedSensor(coordinator, entry),
            NewEmailsInspectedSensor(coordinator, entry),
            EmailsMatchedSensor(coordinator, entry),
            TrackingNumbersFoundSensor(coordinator, entry),
            KeywordHitsSensor(coordinator, entry),
            ActivityLogSensor(coordinator, entry),
            Stage2Sensor(coordinator, entry),
            OllamaLatencySensor(coordinator, entry),
            OllamaParseQualitySensor(coordinator, entry),
            Stage2ConsecutiveFailuresSensor(coordinator, entry),
            EmailsSentToLLMSensor(coordinator, entry),
            EmailsParsedByLLMSensor(coordinator, entry),
            PendingPostsSensor(coordinator, entry),
            # 3 primary operational sensors (Phase 26 Plan 03)
            ShipmentsForwardedSensor(coordinator, entry),
            LastForwardedSensor(coordinator, entry),
            ParcelAppQuotaSensor(coordinator, entry),
        ]
    )


class ShipmentsForwardedSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Lifetime count of genuine 2xx POSTs to ParcelApp (P26-ENT-01).

    state_class TOTAL_INCREASING: counter only ever grows; HA can use it for
    long-term statistics. Reads coordinator.total_forwarded which is persisted
    across HA restarts via the Phase 26 store keys.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Shipments Forwarded"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_shipments_forwarded"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def native_value(self) -> int:
        """Lifetime count of forwarded shipments; persisted across HA restarts."""
        return self.coordinator.total_forwarded

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """currently_tracked: live in-memory shipments from the current session."""
        return {
            "currently_tracked": self.coordinator.currently_tracked_count,
        }


class LastForwardedSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Timestamp of the most recent successful ParcelApp POST (P26-ENT-02).

    native_value is None before the first forward (HA shows 'unknown').
    After the first forward, returns a timezone-aware UTC datetime derived from
    coordinator.last_forwarded_ts (epoch seconds).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Last Forwarded"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_last_forwarded"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def native_value(self) -> datetime | None:
        """UTC datetime of last forward; None if no forwarding has occurred yet."""
        ts = self.coordinator.last_forwarded_ts
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=UTC)


class ParcelAppQuotaSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Estimated remaining ParcelApp daily POST quota (P26-ENT-03).

    native_value = max(0, PARCELAPP_DAILY_LIMIT - used_today).
    This is an advisory estimate based on our own counter; it becomes authoritative
    once a real 429 has been seen (coordinator then tracks the reset window).

    Reads only the public coordinator.quota_is_exhausted / coordinator.used_today
    properties — never the private _quota_exhausted_until (RESEARCH Pitfall 4 /
    STRIDE T-26-05).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "ParcelApp Quota"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_parcelapp_quota"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )

    @property
    def native_value(self) -> int:
        """Estimated remaining quota; clamped to 0 (never negative)."""
        return max(0, PARCELAPP_DAILY_LIMIT - self.coordinator.used_today)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """daily_limit, used_today, exhausted flag, and estimate caveat description."""
        return {
            "daily_limit": PARCELAPP_DAILY_LIMIT,
            "used_today": self.coordinator.used_today,
            "exhausted": self.coordinator.quota_is_exhausted,
            "description": ("estimate from our own count; authoritative only once a 429 is seen"),
        }
