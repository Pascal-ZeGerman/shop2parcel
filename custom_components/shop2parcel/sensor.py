"""Shop2Parcel sensor platform — operational-health and diagnostic sensors.

Phase 26 Plan 03 (P26-ENT-01..03, P26-REMOVE-01):
- ShipmentSensor and per-shipment dynamic-add machinery removed.
- HasActiveShipmentsBinarySensor removed (handled in binary_sensor.py Plan 03).
- Three new primary (non-diagnostic) operational sensors registered:
    1. ShipmentsForwardedSensor  — TOTAL_INCREASING; reads coordinator.total_forwarded
    2. LastForwardedSensor        — TIMESTAMP device class; reads coordinator.last_forwarded_ts
    3. ParcelAppQuotaSensor       — estimate max(0, PARCELAPP_DAILY_LIMIT - used_today)

Phase 7 (D-09/D-13): 14 static diagnostic sensors are still co-registered here.
"diagnostic_sensor" is not a built-in HA platform domain and cannot be used
in PLATFORMS directly — sensors belonging to the "sensor" domain must be
registered from sensor.py's async_setup_entry.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    CarrierFormatRejectionsSensor,
    EmailsMatchedSensor,
    EmailsParsedByLLMSensor,
    EmailsScannedSensor,
    EmailsSentToLLMSensor,
    GlobalQueueSensor,
    KeywordHitsSensor,
    NewEmailsInspectedSensor,
    OllamaLatencySensor,
    OllamaParseQualitySensor,
    PendingPostsSensor,
    Stage2ConsecutiveFailuresSensor,
    Stage2Sensor,
    TrackingNumbersFoundSensor,
)

if TYPE_CHECKING:
    # Phase 34-03: hub.py imports coordinator.py, so a module-level runtime
    # import here would create a circular import. TYPE_CHECKING-only import
    # lets GlobalQuotaSensor's hub param be typed without one (mirrors
    # coordinator.py's identical TYPE_CHECKING guard for self._hub).
    from .hub import Shop2ParcelHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shop2Parcel sensor platform.

    Pitfall 4: hass.data[DOMAIN][entry.entry_id] is a dict {"coordinator": ..., "cancel_cleanup": ...}
    after Phase 5 changes to __init__.py — use ["coordinator"] key, not bare access.

    Phase 7 (D-09): 14 static diagnostic sensors co-registered here.
    Phase 26 Plan 03 (P26-ENT-01..03): 3 primary operational sensors registered here.
    Phase 34-05 (R-01/DIAG-01/DIAG-02): registers the two hub-owned global
    sensors from EXACTLY ONE entry (whichever first claims ownership via
    hub.claim_global_sensor_ownership) — never unconditionally from every
    entry, which would produce a unique_id collision (RESEARCH.md
    Anti-Pattern). Every entry's async_add_entities callback is stored on
    the hub regardless of ownership so a LATER entry can still serve as a
    re-home target if it becomes the survivor after the owner unloads.
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
            # 14 diagnostic sensors
            EmailsScannedSensor(coordinator, entry),
            NewEmailsInspectedSensor(coordinator, entry),
            EmailsMatchedSensor(coordinator, entry),
            TrackingNumbersFoundSensor(coordinator, entry),
            KeywordHitsSensor(coordinator, entry),
            ActivityLogSensor(coordinator, entry),
            Stage2Sensor(coordinator, entry),
            OllamaLatencySensor(coordinator, entry),
            OllamaParseQualitySensor(coordinator, entry),
            CarrierFormatRejectionsSensor(coordinator, entry),
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

    # Phase 34-05 (R-01/DIAG-01/DIAG-02): remember this entry's callback so
    # the hub can re-add the global sensors here later if THIS entry becomes
    # the new owner (maybe_rehome_global_sensors, hub.py). Guarded with
    # .get() defensively — the hub is always present after __init__.py's
    # attach() by this point, but this mirrors the .get()-guard style used
    # elsewhere in this codebase (e.g. _debug_mode_active's None tolerance).
    hub: Shop2ParcelHub | None = hass.data.get(DOMAIN, {}).get("__shared__")
    if hub is not None:
        hub.register_sensor_add_entities(entry.entry_id, async_add_entities)
        if hub.claim_global_sensor_ownership(entry.entry_id):
            # First entry ever to attach (or the hub had no owner) —
            # register the two global sensors now. This is the ONLY
            # registration site for them — never registered unconditionally
            # from every entry (unique_id-collision anti-pattern).
            async_add_entities(
                [
                    GlobalQuotaSensor(coordinator, hub),
                    GlobalQueueSensor(coordinator, hub),
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
    _unique_id_suffix = "shipments_forwarded"  # single source of truth (finding 9)

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{self._unique_id_suffix}"
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
    _unique_id_suffix = "last_forwarded"  # single source of truth (finding 9)

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{self._unique_id_suffix}"
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
    _unique_id_suffix = "parcelapp_quota"  # single source of truth (finding 9)

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{self._unique_id_suffix}"
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


class GlobalQuotaSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Hub-owned global remaining-ParcelApp-quota sensor (DIAG-01, D-01/D-02/D-03).

    Additive to ParcelAppQuotaSensor (D-01) — the three per-account sensors
    are NOT removed. Post-Phase-31, every per-account quota sensor already
    reads the shared hub value via coordinator.used_today, so this entity's
    number is deliberately identical; its purpose is being the ONE
    hub-scoped, removal-surviving source of truth under a distinct
    "Shop2Parcel Hub" device (D-02), disambiguated by device + name rather
    than by removing existing entities.

    Registered under a hub-scoped identifier (not entry_id-scoped, D-02) so
    exactly one instance exists regardless of which/how many accounts are
    attached. Registration in async_setup_entry is deferred to 34-05 to
    avoid the unique_id-collision anti-pattern (R-01) — this class only
    defines the value/attribute/identity surface, unit-tested by driving
    hub state directly.

    Reads ONLY the public hub.used_today / hub.quota_is_exhausted
    properties — never the private _used_today / quota_exhausted_until
    (mirrors ParcelAppQuotaSensor's discipline, RESEARCH Pitfall 4 /
    STRIDE T-26-05 / T-34-06).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Hub Quota Remaining"
    _unique_id_suffix = "hub_quota_remaining"  # hub-scoped — never entry_id-scoped (D-02)

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        hub: Shop2ParcelHub,
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._attr_unique_id = f"{DOMAIN}___shared___{self._unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "__shared__")},
            name="Shop2Parcel Hub",
        )

    @property
    def native_value(self) -> int:
        """Estimated shared remaining quota; clamped to 0 (never negative)."""
        return max(0, PARCELAPP_DAILY_LIMIT - self._hub.used_today)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """daily_limit, used_today, exhausted flag, scope='shared', and estimate caveat."""
        return {
            "daily_limit": PARCELAPP_DAILY_LIMIT,
            "used_today": self._hub.used_today,
            "exhausted": self._hub.quota_is_exhausted,
            "scope": "shared",
            "description": ("estimate from our own count; authoritative only once a 429 is seen"),
        }


# Single source of truth for operational sensor uid suffixes (finding 9).
# __init__.py imports this to build KNOWN_GOOD_UID_SUFFIXES without duplication.
OPERATIONAL_SENSOR_UID_SUFFIXES: frozenset[str] = frozenset(
    {
        ShipmentsForwardedSensor._unique_id_suffix,
        LastForwardedSensor._unique_id_suffix,
        ParcelAppQuotaSensor._unique_id_suffix,
    }
)
