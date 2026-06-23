"""Shop2Parcel diagnostic_sensor — 6 diagnostic sensor entity classes (5 PollStats-based + ActivityLogSensor).

Phase 7 (DIAG-08, DIAG-09, DIAG-10):
- D-09: All diagnostic sensors registered statically via sensor.py::async_setup_entry.
- D-10: All sensors use CoordinatorEntity[Shop2ParcelCoordinator]; read from
  coordinator.diagnostics (a PollStats instance, always non-None — Pitfall 5).
  W11/P12-WR-01: use the public .diagnostics property, not ._diagnostics, so the
  access goes through the documented API surface.
- D-11: Diagnostic sensors share the same Shop2Parcel DeviceInfo as shipment sensors
  (one device per config entry, identifiers={(DOMAIN, entry.entry_id)}).
- D-12: Sensor state/attribute mapping per CONTEXT.md D-12.

Phase 11 (ACTLOG-04, ACTLOG-05):
- ActivityLogSensor (6th sensor): state = scan_events_total, attributes = last 10 events.
  Reads from coordinator.diagnostics.scan_events (deque added in Plan 01).

This module only exports sensor classes.  Registration happens in
sensor.py::async_setup_entry because HA's platform forwarding only supports
built-in platform domains (e.g. "sensor", "binary_sensor") — there is no
"diagnostic_sensor" platform domain in HA core.

MEASUREMENT state class is used because counters reset on HA restart,
which avoids false statistics anomalies on restart (RESEARCH.md Open Questions §1).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Shop2ParcelCoordinator


class DiagnosticSensor(CoordinatorEntity[Shop2ParcelCoordinator], SensorEntity):
    """Shared base for all 6 diagnostic sensors (D-10, D-11)."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
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
    """sensor.shop2parcel_emails_scanned — raw emails returned by Gmail/IMAP before dedup."""

    _attr_name = "Emails Returned"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        # unique_id kept as _emails_scanned to avoid orphaning existing entity registry entry.
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_emails_scanned"

    @property
    def native_value(self) -> int:
        return self.coordinator.diagnostics.emails_returned_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Raw emails returned by Gmail/IMAP before dedup. Includes duplicates from previous polls.",
            "last_poll_returned": d.last_poll_emails_returned,
            "last_poll_skipped_dedup": d.last_poll_emails_skipped_dedup,
            "last_poll_inspected": d.last_poll_emails_scanned,
            "submitted_tracking_count": d.submitted_tracking_count,
            "last_poll_time": d.last_poll_time,
            "query_used": d.last_poll_query,
            "effective_query_used": d.last_poll_effective_query,
            "poll_duration_ms": d.last_poll_duration_ms,
        }


class NewEmailsInspectedSensor(DiagnosticSensor):
    """sensor.shop2parcel_new_emails_inspected — emails that passed dedup and were inspected."""

    _attr_name = "New Emails Inspected"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_new_emails_inspected"

    @property
    def native_value(self) -> int:
        return self.coordinator.diagnostics.emails_scanned_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Emails that passed dedup and were actually read. Excludes duplicates already seen in previous polls.",
            "last_poll_count": d.last_poll_emails_scanned,
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
        return self.coordinator.diagnostics.emails_matched_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        unmatched = max(0, d.last_poll_emails_scanned - d.last_poll_emails_matched)
        return {
            "description": "Emails that produced usable shipment data. Unmatched emails had no recognisable shipment content.",
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
        return self.coordinator.diagnostics.tracking_numbers_found_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Total tracking numbers extracted from matched emails. One email can contain multiple tracking numbers.",
            "last_poll_found": list(d.last_poll_found),
        }


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
        return self.coordinator.diagnostics.keyword_hits_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Times the fallback regex path matched. A high count relative to Emails Matched suggests the primary parser is missing patterns.",
            "last_poll_hits": d.last_poll_keyword_hits,
            "per_keyword": dict(d.keyword_hits_per_key),
        }


class ActivityLogSensor(DiagnosticSensor):
    """sensor.shop2parcel_activity_log — scan event ring buffer (Phase 11, ACTLOG-04/05).

    native_value: scan_events_total (cumulative count since last HA restart).
    extra_state_attributes: {"recent_events": last 10 scan event dicts from the ring buffer}.

    T-11-06: extra_state_attributes slices to [-10:] to cap the attribute payload
    regardless of the deque maxlen (currently 50). Prevents unbounded HA state storage.
    """

    _attr_name = "Activity Log"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_activity_log"

    @property
    def native_value(self) -> int:
        return self.coordinator.diagnostics.scan_events_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Cumulative scan event count since last HA restart. See recent_events for the last 10 scan events.",
            "recent_events": list(d.scan_events)[-10:],
        }


class Stage2Sensor(DiagnosticSensor):
    """sensor.shop2parcel_stage2_queue — Stage-2 queue depth and lifetime counters.

    DIAG-01: queue depth as native_value; 6 lifetime stage2 counters as
    extra_state_attributes. Registered unconditionally (no stage2_enabled
    gate) so Stage-1-only users see zero values and can confirm the
    integration is not silently failing.
    """

    _attr_name = "Stage-2 Queue"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_stage2_queue"

    @property
    def native_value(self) -> int:
        return self.coordinator.stage2_queue_depth

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Current items waiting for AI (Ollama) analysis. Zero is normal at rest. See attributes for lifetime counters.",
            "enqueued_total": d.stage2_enqueued_total,
            "succeeded_total": d.stage2_succeeded_total,
            "failed_total": d.stage2_failed_total,
            "dropped_backpressure_total": d.stage2_dropped_backpressure_total,
            "schema_error_total": d.stage2_schema_error_total,
            "conflict_total": d.stage2_conflict_total,
        }


class OllamaLatencySensor(DiagnosticSensor):
    """sensor.shop2parcel_ollama_latency — average Stage-2 Ollama /api/generate latency (ms).

    native_value: rolling average across all successful LLM calls since last HA restart.
    Returns None until the first call completes so HA shows 'unavailable' rather than 0.
    extra_state_attributes: last/min/max per-call latency + total call count.
    """

    _attr_name = "Stage-2 LLM Latency"
    _attr_native_unit_of_measurement = "ms"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_ollama_latency"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.diagnostics
        if d.stage2_llm_calls_total == 0:
            return None
        return round(d.stage2_llm_latency_ms_sum / d.stage2_llm_calls_total, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Average round-trip time for Ollama /api/generate calls since last HA restart. None until the first Stage-2 call completes.",
            "last_call_ms": round(d.stage2_llm_latency_ms_last, 1) if d.stage2_llm_latency_ms_last is not None else None,
            "min_ms": round(d.stage2_llm_latency_ms_min, 1) if d.stage2_llm_latency_ms_min is not None else None,
            "max_ms": round(d.stage2_llm_latency_ms_max, 1) if d.stage2_llm_latency_ms_max is not None else None,
            "call_count": d.stage2_llm_calls_total,
        }


class OllamaParseQualitySensor(DiagnosticSensor):
    """sensor.shop2parcel_ollama_parse_retries — cumulative fence-strip retry count.

    native_value: number of times the 2-pass parser needed the markdown-fence fallback.
    A non-zero value is not an error — it means the model wrapped its JSON in ```json```
    blocks, which the parser handles. A high fence_retry_rate_pct (>50 %) suggests
    switching to a model that outputs cleaner JSON.
    extra_state_attributes: retry rate %, clean parse count, total call count.
    """

    _attr_name = "Stage-2 Parse Retries"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_ollama_parse_retries"

    @property
    def native_value(self) -> int:
        return self.coordinator.diagnostics.stage2_fence_retry_total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        calls = d.stage2_llm_calls_total
        retry_rate = round(d.stage2_fence_retry_total / calls * 100, 1) if calls else 0.0
        return {
            "description": "Times the LLM response needed markdown-fence stripping before it could be parsed. A high rate means the model is wrapping JSON in ```json``` blocks.",
            "fence_retry_rate_pct": retry_rate,
            "clean_parses": max(0, calls - d.stage2_fence_retry_total),
            "total_calls": calls,
        }


class Stage2ConsecutiveFailuresSensor(DiagnosticSensor):
    """sensor.shop2parcel_stage2_consecutive_failures — current failure streak.

    native_value: number of back-to-back Stage-2 failures without a success.
    Resets to 0 on any successful extraction. HA sends a persistent notification
    once the streak exceeds the STAGE2_NOTIFY_THRESHOLD constant.
    extra_state_attributes: lifetime error sub-counters for triage.
    """

    _attr_name = "Stage-2 Consecutive Failures"

    def __init__(
        self,
        coordinator: Shop2ParcelCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_stage2_consecutive_failures"

    @property
    def native_value(self) -> int:
        return self.coordinator.stage2_consecutive_failures

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.diagnostics
        return {
            "description": "Current run of back-to-back Stage-2 failures without a success. Resets to 0 on any successful extraction. Triggers an HA notification after the threshold is reached.",
            "transient_error_total": d.stage2_transient_error_total,
            "schema_error_total": d.stage2_schema_error_total,
            "failed_total": d.stage2_failed_total,
        }
