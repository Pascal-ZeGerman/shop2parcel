"""Shop2Parcel diagnostic_sensor — 4 static DiagnosticSensor entity classes.

Phase 7 (DIAG-08, DIAG-09, DIAG-10):
- D-09: All 4 sensors registered statically via sensor.py::async_setup_entry.
- D-10: All 4 sensors use CoordinatorEntity[Shop2ParcelCoordinator]; read from
  coordinator._diagnostics (a PollStats instance, always non-None — Pitfall 5).
- D-11: Diagnostic sensors share the same Shop2Parcel DeviceInfo as shipment sensors
  (one device per config entry, identifiers={(DOMAIN, entry.entry_id)}).
- D-12: Sensor state/attribute mapping per CONTEXT.md D-12.

This module only exports sensor classes.  Registration happens in
sensor.py::async_setup_entry because HA's platform forwarding only supports
built-in platform domains (e.g. "sensor", "binary_sensor") — there is no
"diagnostic_sensor" platform domain in HA core.

MEASUREMENT state class is used because counters reset on HA restart,
which avoids false statistics anomalies on restart (RESEARCH.md Open Questions §1).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Shop2ParcelCoordinator

_LOGGER = logging.getLogger(__name__)


class DiagnosticSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Shared base for all 4 diagnostic sensors (D-10, D-11)."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    # D-12: counters reset on HA restart — MEASUREMENT avoids
    # false statistics anomalies on restart (RESEARCH.md A1).
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        # D-11: same DeviceInfo as shipment + binary sensors — one device per entry.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Shop2Parcel",
        )


class EmailsScannedSensor(DiagnosticSensor):
    """sensor.shop2parcel_emails_scanned — total emails scanned since HA restart."""

    _attr_name = "Emails Scanned"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_emails_scanned"

    @property
    def native_value(self) -> int:
        # Pitfall 5: _diagnostics is always a PollStats() — never None.
        return self.coordinator._diagnostics.emails_scanned_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator._diagnostics
        return {
            "last_poll_count": d.last_poll_emails_scanned,
            "last_poll_time": d.last_poll_time,
            "query_used": d.last_poll_query,
            "poll_duration_ms": d.last_poll_duration_ms,
        }


class EmailsMatchedSensor(DiagnosticSensor):
    """sensor.shop2parcel_emails_matched — total emails that produced a ShipmentData."""

    _attr_name = "Emails Matched"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_emails_matched"

    @property
    def native_value(self) -> int:
        return self.coordinator._diagnostics.emails_matched_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator._diagnostics
        unmatched = max(0, d.last_poll_emails_scanned - d.last_poll_emails_matched)
        return {
            "last_poll_matched": d.last_poll_emails_matched,
            "last_poll_unmatched": unmatched,
            "last_poll_skip_reasons": list(d.last_poll_skip_reasons),
        }


class TrackingNumbersFoundSensor(DiagnosticSensor):
    """sensor.shop2parcel_tracking_numbers_found — total tracking numbers extracted."""

    _attr_name = "Tracking Numbers Found"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_tracking_numbers_found"

    @property
    def native_value(self) -> int:
        return self.coordinator._diagnostics.tracking_numbers_found_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator._diagnostics
        return {"last_poll_found": list(d.last_poll_found)}


class KeywordHitsSensor(DiagnosticSensor):
    """sensor.shop2parcel_keyword_hits — cumulative fallback regex hit count."""

    _attr_name = "Keyword Hits"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_keyword_hits"

    @property
    def native_value(self) -> int:
        return self.coordinator._diagnostics.keyword_hits_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator._diagnostics
        return {
            "last_poll_hits": d.last_poll_keyword_hits,
            "per_keyword": dict(d.keyword_hits_per_key),
        }
