"""Tests for Shop2Parcel diagnostic_sensor.py — Phase 7 DIAG-08, DIAG-09, DIAG-10.

These tests assume Plan 03 has landed:
- custom_components/shop2parcel/diagnostic_sensor.py exists with 6 sensor classes.
- Diagnostic sensors are registered via sensor.py::async_setup_entry (not via a
  "diagnostic_sensor" platform — HA only supports built-in platform domains).
- coordinator._diagnostics is a PollStats instance (Plan 02).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.const import DOMAIN


def _make_shipment(message_id: str = "msg1") -> ShipmentData:
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1700000000,
    )


def _make_parse_result(shipment: ShipmentData) -> ParseResult:
    return ParseResult(
        shipment=shipment,
        skip_reason=None,
        strategy_used="html_template",
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


async def _setup_integration(hass, mock_config_entry, *, with_message: bool = False):
    """Set up the integration with mocked Gmail/parcelapp/parser/Store/oauth.

    When with_message=True, Gmail returns one message that produces a shipment so
    coordinator._diagnostics accumulates non-zero values for state/attribute tests.
    """
    mock_config_entry.add_to_hass(hass)
    gmail_messages = [{"id": "msg1"}] if with_message else []
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>" if with_message else "",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=(gmail_messages, "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        await hass.async_block_till_done()
        return coordinator


async def test_emails_scanned_sensor_registered(hass, mock_config_entry):
    """DIAG-08 / DIAG-09: sensor.shop2parcel_emails_scanned registered at setup; state=0.

    Pitfall 5: native_value is int 0 before any poll runs — never None.
    """
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_emails_scanned"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "emails_scanned diagnostic sensor not registered"
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"


async def test_all_six_diagnostic_sensors_registered(hass, mock_config_entry):
    """DIAG-08: all 6 diagnostic sensors registered at setup."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    expected_suffixes = {
        "emails_scanned",
        "new_emails_inspected",
        "emails_matched",
        "tracking_numbers_found",
        "keyword_hits",
        "activity_log",
    }
    found = {e.unique_id.removeprefix(prefix) for e in entries if e.unique_id.startswith(prefix)}
    missing = expected_suffixes - found
    assert not missing, f"missing diagnostic sensors: {missing}"


async def test_diagnostic_sensors_share_device(hass, mock_config_entry):
    """DIAG-10: all 6 diagnostic sensors share the same Shop2Parcel device."""
    await _setup_integration(hass, mock_config_entry)
    device_reg = dr.async_get(hass)
    devices = [
        d
        for d in device_reg.devices.values()
        if (DOMAIN, mock_config_entry.entry_id) in d.identifiers
    ]
    assert len(devices) == 1, f"expected exactly 1 device, got {len(devices)}"


async def test_emails_scanned_state_after_poll(hass, mock_config_entry):
    """DIAG-09: EmailsScannedSensor state == emails_returned_total after a poll.

    Setup runs one full poll cycle with a matched shipment, so:
    - emails_returned_total == 1 (sensor native_value)
    - sensor.shop2parcel_emails_scanned.state == "1"
    - extra_state_attributes contains last_poll_returned, last_poll_time, query_used,
      poll_duration_ms (per CONTEXT.md D-12).
    """
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    # async_setup runs async_config_entry_first_refresh which triggers _async_update_data.
    assert coordinator._diagnostics.emails_returned_total == 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_emails_scanned"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "1"
    # D-12 attributes
    assert "last_poll_returned" in state.attributes
    assert "last_poll_time" in state.attributes
    assert "query_used" in state.attributes
    assert "poll_duration_ms" in state.attributes
    assert state.attributes["last_poll_returned"] == 1


async def test_tracking_numbers_found_attributes_after_poll(hass, mock_config_entry):
    """DIAG-09: tracking_numbers_found state and last_poll_found attribute after a poll.

    Compact projection: each surfaced entry has exactly {tracking_number, carrier,
    order_name}. message_id is NOT surfaced (dropped by the presentation-layer trim).
    last_poll_found_count holds the true total.
    """
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    assert coordinator._diagnostics.tracking_numbers_found_total == 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_tracking_numbers_found"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "1"
    assert "last_poll_found" in state.attributes
    last_poll_found = state.attributes["last_poll_found"]
    assert isinstance(last_poll_found, list)
    assert len(last_poll_found) == 1
    # Compact triple — message_id must NOT be present
    entry_keys = set(last_poll_found[0].keys())
    assert entry_keys == {"tracking_number", "carrier", "order_name"}, (
        f"unexpected keys in surfaced entry: {entry_keys}"
    )
    assert last_poll_found[0]["tracking_number"] == "1Z999AA10123456784"
    assert "message_id" not in last_poll_found[0]
    # True total count is always present
    assert state.attributes["last_poll_found_count"] == 1


async def test_tracking_numbers_found_attributes_bounded_payload(hass, mock_config_entry):
    """TrackingNumbersFoundSensor.extra_state_attributes is bounded even with 300 fat entries.

    Verifies:
    - Surfaced list is capped at 10 (not 300).
    - Every surfaced entry's key set is exactly {tracking_number, carrier, order_name}.
    - last_poll_found_count reflects the true total (300).
    - Serialized attributes fit well under the HA recorder 16384-byte limit.
    """
    coordinator = await _setup_integration(hass, mock_config_entry)

    # Build 300 fat entries that mirror the real coordinator append shape
    # (gmail_coordinator.py line 366): tracking_number, carrier, order_name,
    # message_id, candidates (long list), and spread email_meta keys.
    fat_candidates = [f"CANDIDATE_TOKEN_{j:04d}_ABCDEFGHIJKLMNOP" for j in range(50)]
    fat_entries = [
        {
            "tracking_number": f"1Z999AA1012345{i:04d}",
            "carrier": "UPS",
            "order_name": f"#ORDER{i}",
            "message_id": f"msg{i}",
            "candidates": fat_candidates,
            "subject": f"Your order #ORDER{i} has shipped with tracking 1Z999AA1012345{i:04d}",
            "from": "no-reply@shopify.com",
        }
        for i in range(300)
    ]
    coordinator._diagnostics.last_poll_found = fat_entries

    # Refresh entity state
    coordinator.async_set_updated_data(coordinator.data or {})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_tracking_numbers_found"
    entity_entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entity_entry.entity_id)
    assert state is not None

    last_poll_found = state.attributes["last_poll_found"]

    # Must be capped at 10 — not 300
    assert len(last_poll_found) == 10, (
        f"expected 10 surfaced entries (capped), got {len(last_poll_found)}"
    )

    # Every surfaced entry must have exactly the compact triple
    for surfaced in last_poll_found:
        assert set(surfaced.keys()) == {"tracking_number", "carrier", "order_name"}, (
            f"unexpected keys in surfaced entry: {set(surfaced.keys())}"
        )

    # True total preserved
    assert state.attributes["last_poll_found_count"] == 300

    # Serialized payload must be well under the HA recorder 16384-byte limit
    payload_bytes = len(json.dumps(state.attributes).encode("utf-8"))
    assert payload_bytes < 16384, f"attributes exceed 16384 bytes: {payload_bytes}"
    # Tighter sanity bound: 10 compact entries should be tiny
    assert payload_bytes < 8000, f"attributes unexpectedly large: {payload_bytes}"


async def test_new_emails_inspected_sensor_registered(hass, mock_config_entry):
    """NewEmailsInspectedSensor registered at setup; state=0 before any poll."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_new_emails_inspected"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "new_emails_inspected diagnostic sensor not registered"
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"


async def test_new_emails_inspected_state_after_poll(hass, mock_config_entry):
    """NewEmailsInspectedSensor state == emails_scanned_total after a poll."""
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    assert coordinator._diagnostics.emails_scanned_total == 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_new_emails_inspected"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "1"
    assert "last_poll_count" in state.attributes
    assert state.attributes["last_poll_count"] == 1


async def test_keyword_hits_per_keyword_attribute(hass, mock_config_entry):
    """DIAG-09: keyword_hits sensor exposes per_keyword dict with all 3 keys."""
    await _setup_integration(hass, mock_config_entry, with_message=True)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_keyword_hits"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    # HTML strategy success -> all keyword_hits False -> keyword_hits_total stays at 0
    assert state.state == "0"
    assert "per_keyword" in state.attributes
    per_keyword = state.attributes["per_keyword"]
    assert set(per_keyword.keys()) == {"tracking_regex", "order_regex", "carrier_regex"}


async def test_activity_log_sensor_state(hass, mock_config_entry):
    """ActivityLogSensor registered as 6th sensor; state=0 before any poll."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_activity_log"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "activity_log diagnostic sensor not registered"
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"


async def test_activity_log_sensor_state_after_poll(hass, mock_config_entry):
    """ActivityLogSensor native_value == scan_events_total after a poll."""
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    # After a poll that processed 1 message, scan_events_total should be 1
    assert coordinator._diagnostics.scan_events_total >= 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_activity_log"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == str(coordinator._diagnostics.scan_events_total)


async def test_activity_log_sensor_attributes(hass, mock_config_entry):
    """ActivityLogSensor extra_state_attributes contains 'recent_events' key as a list."""
    await _setup_integration(hass, mock_config_entry, with_message=True)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_activity_log"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert "recent_events" in state.attributes
    assert isinstance(state.attributes["recent_events"], list)


async def test_activity_log_sensor_attributes_capped_at_10(hass, mock_config_entry):
    """ActivityLogSensor recent_events is capped at 10 even when scan_events has 15 items."""
    coordinator = await _setup_integration(hass, mock_config_entry)
    # Manually populate scan_events with 15 fake events
    for i in range(15):
        coordinator._diagnostics.scan_events.append(
            {
                "timestamp": f"2026-01-01T00:00:0{i % 10}Z",
                "message_id": f"gmail:msg{i}",
                "subject": f"Shipment {i}",
                "sender": "noreply@shopify.com",
                "strategy": "html_template",
                "tracking_number": f"TRK{i}",
                "outcome": "posted",
            }
        )
    # Trigger a coordinator update to refresh entity state
    coordinator.async_set_updated_data(coordinator.data or {})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_activity_log"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    recent_events = state.attributes["recent_events"]
    assert isinstance(recent_events, list)
    assert len(recent_events) == 10, f"expected 10 events (capped), got {len(recent_events)}"
    # Should be the last 10 (indices 5-14)
    assert recent_events[-1]["message_id"] == "gmail:msg14"


# ---------------------------------------------------------------------------
# Phase 21 Plan 03 — DIAG-01: Stage2Sensor class and registration tests
# ---------------------------------------------------------------------------


async def test_stage2_sensor_registered_in_async_setup_entry(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor is registered via async_setup_entry (unconditionally)."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_stage2_queue"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "stage2_queue diagnostic sensor not registered"


async def test_stage2_sensor_registered_even_when_stage2_disabled(hass, mock_config_entry):
    """DIAG-01 Pitfall 6: Stage2Sensor registered even when stage2_enabled=False."""
    # mock_config_entry has no CONF_OLLAMA_URL in options → stage2_enabled=False.
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_stage2_queue"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "Stage2Sensor must be registered unconditionally"


async def test_stage2_sensor_unique_id_format(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor unique_id format is '{DOMAIN}_{entry_id}_stage2_queue'."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor._attr_unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_stage2_queue"


async def test_stage2_sensor_attr_name(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor name is 'Stage-2 Queue' (via HA entity name property on instance)."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    # HA Entity._attr_name is a property descriptor; test via class dict
    assert (
        Stage2Sensor.__dict__.get("_attr_name") == "Stage-2 Queue" or sensor.name == "Stage-2 Queue"
    )


async def test_stage2_sensor_inherits_diagnostic_entity_category(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor inherits EntityCategory.DIAGNOSTIC from DiagnosticSensor base."""
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


async def test_stage2_sensor_inherits_state_class_measurement(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor inherits SensorStateClass.MEASUREMENT from DiagnosticSensor base."""
    from homeassistant.components.sensor import SensorStateClass

    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor.state_class == SensorStateClass.MEASUREMENT


async def test_stage2_sensor_native_value_zero_when_nothing_inflight(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor.native_value returns 0 (not None) when nothing is in-flight.

    Phase 32 cutover: native_value reads stage2_queue_depth, which is hub-derived
    (hub.inflight_count) — the retired per-entry _stage2_queue no longer exists
    to assert None on.
    """
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor.native_value == 0


async def test_stage2_sensor_native_value_reads_qsize_when_queue_active(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor.native_value returns the hub's per-account in-flight count.

    Phase 32 cutover: stage2_queue_depth (which native_value reads) is now hub-derived
    (hub.inflight_count) — seed the account's hub in-flight set via hub.enqueue()
    instead of the retired per-entry _stage2_queue.
    """
    from custom_components.shop2parcel.coordinator import Stage2Job
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    shipment = _make_shipment()
    for i in range(3):
        job = Stage2Job(
            storage_key=f"key{i}",
            normalized_tn=f"TN{i}",
            shipment=shipment,
            html_body="<html/>",
            message_id=f"msg{i}",
            meta={},
            entry_id=mock_config_entry.entry_id,
        )
        coordinator._hub.enqueue(job)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor.native_value == 3


async def test_stage2_sensor_extra_state_attributes_contains_exactly_6_keys(
    hass, mock_config_entry
):
    """DIAG-01: Stage2Sensor.extra_state_attributes has exactly 7 keys (6 counters + description)."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    attrs = sensor.extra_state_attributes
    expected_keys = {
        "description",
        "enqueued_total",
        "succeeded_total",
        "failed_total",
        "dropped_backpressure_total",
        "schema_error_total",
        "conflict_total",
    }
    assert set(attrs.keys()) == expected_keys, f"unexpected keys: {set(attrs.keys())}"


async def test_stage2_sensor_extra_state_attributes_reads_pollstats_counters(
    hass, mock_config_entry
):
    """DIAG-01: Stage2Sensor.extra_state_attributes reads from coordinator.diagnostics."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    # Manually set counter values on diagnostics.
    coordinator._diagnostics.stage2_enqueued_total = 5
    coordinator._diagnostics.stage2_succeeded_total = 3
    coordinator._diagnostics.stage2_failed_total = 1
    coordinator._diagnostics.stage2_dropped_backpressure_total = 2
    coordinator._diagnostics.stage2_schema_error_total = 1
    coordinator._diagnostics.stage2_conflict_total = 0

    sensor = Stage2Sensor(coordinator, mock_config_entry)
    attrs = sensor.extra_state_attributes
    assert attrs == {
        "description": "Current items waiting for AI (Ollama) analysis. Zero is normal at rest. See attributes for lifetime counters.",
        "enqueued_total": 5,
        "succeeded_total": 3,
        "failed_total": 1,
        "dropped_backpressure_total": 2,
        "schema_error_total": 1,
        "conflict_total": 0,
    }


async def test_stage2_sensor_device_info_matches_other_diagnostic_sensors(hass, mock_config_entry):
    """DIAG-01: Stage2Sensor._attr_device_info has same identifiers as other diagnostic sensors."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor._attr_device_info is not None
    assert (DOMAIN, mock_config_entry.entry_id) in sensor._attr_device_info["identifiers"]


# ---------------------------------------------------------------------------
# Phase 34 Plan 03 (DIAG-02): GlobalQueueSensor — hub-owned global sensor
# ---------------------------------------------------------------------------


def _make_stage2_job(i: int, entry_id: str):
    from custom_components.shop2parcel.coordinator import Stage2Job

    shipment = _make_shipment(f"msg{i}")
    return Stage2Job(
        storage_key=f"key{i}",
        normalized_tn=f"TN{i}",
        shipment=shipment,
        html_body="<html/>",
        message_id=f"msg{i}",
        meta={},
        entry_id=entry_id,
    )


async def test_global_queue_sensor_native_value_empty(hass, mock_config_entry):
    """DIAG-02: native_value == 0 for an empty hub (nothing queued, nothing in-flight)."""
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    sensor = GlobalQueueSensor(coordinator, hub)
    assert sensor.native_value == 0


async def test_global_queue_sensor_native_value_enqueued_no_drain(hass, mock_config_entry):
    """DIAG-02: native_value == 3 after 3 enqueues with no drain — pending == in-flight."""
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    entry_id = mock_config_entry.entry_id
    for i in range(3):
        hub.enqueue(_make_stage2_job(i, entry_id))
    sensor = GlobalQueueSensor(coordinator, hub)
    assert sensor.native_value == 3
    assert sensor.extra_state_attributes["pending"] == 3
    assert sensor.extra_state_attributes["processing"] == 0


async def test_global_queue_sensor_pending_plus_processing_mid_drain(hass, mock_config_entry):
    """DIAG-02: worker pulls 1 mid-process -> pending=2, processing=1, native_value stays 3."""
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    entry_id = mock_config_entry.entry_id
    for i in range(3):
        hub.enqueue(_make_stage2_job(i, entry_id))

    # Simulate the worker dequeuing one job without yet releasing its in-flight
    # slot — release only happens in the worker's `finally` on job completion.
    hub._queue.get_nowait()

    sensor = GlobalQueueSensor(coordinator, hub)
    assert sensor.native_value == 3
    attrs = sensor.extra_state_attributes
    assert attrs["pending"] == 2
    assert attrs["processing"] == 1


async def test_global_queue_sensor_native_value_zero_after_full_drain(hass, mock_config_entry):
    """DIAG-02: native_value returns to 0 once every job's in-flight slot is released."""
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    entry_id = mock_config_entry.entry_id
    jobs = [_make_stage2_job(i, entry_id) for i in range(3)]
    for job in jobs:
        hub.enqueue(job)
    for job in jobs:
        hub._queue.get_nowait()
        hub._release_inflight(job.entry_id, job.normalized_tn)

    sensor = GlobalQueueSensor(coordinator, hub)
    assert sensor.native_value == 0


async def test_global_queue_sensor_unique_id_device_info_and_category(hass, mock_config_entry):
    """DIAG-02/D-02: hub-scoped unique_id, DIAGNOSTIC category, hub DeviceInfo."""
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    sensor = GlobalQueueSensor(coordinator, hub)

    assert sensor.unique_id == f"{DOMAIN}___shared___hub_stage2_queue"
    assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC
    assert sensor._attr_device_info is not None
    assert sensor._attr_device_info["identifiers"] == {(DOMAIN, "__shared__")}
    assert sensor._attr_device_info["name"] == "Shop2Parcel Hub"


async def test_global_queue_sensor_uid_not_prefixed_by_per_entry_sweep_prefix(
    hass, mock_config_entry
):
    """T-34-07: hub-scoped uid does NOT match the per-entry orphan-sweep prefix.

    _sweep_orphaned_entities (__init__.py) only removes uids starting with
    f"{DOMAIN}_{entry_id}_" — the hub uid inserts an extra "__shared__"
    scope segment ("shop2parcel___shared___...") that cannot equal any real
    entry_id-based prefix, so the sweep structurally cannot delete it.
    """
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    hub = coordinator._hub
    sensor = GlobalQueueSensor(coordinator, hub)

    per_entry_sweep_prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    assert not sensor.unique_id.startswith(per_entry_sweep_prefix)


async def test_stage2_sensor_still_present_and_unmodified_diag02(hass, mock_config_entry):
    """DIAG-02/D-01: the per-account Stage2Sensor is NOT removed (additive)."""
    from custom_components.shop2parcel.diagnostic_sensor import Stage2Sensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = Stage2Sensor(coordinator, mock_config_entry)
    assert sensor._unique_id_suffix == "stage2_queue"


async def test_all_seven_diagnostic_sensors_registered(hass, mock_config_entry):
    """DIAG-01/DIAG-08: all 7 diagnostic sensors (including Stage2Sensor) registered at setup."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    expected_suffixes = {
        "emails_scanned",
        "new_emails_inspected",
        "emails_matched",
        "tracking_numbers_found",
        "keyword_hits",
        "activity_log",
        "stage2_queue",
    }
    found = {e.unique_id.removeprefix(prefix) for e in entries if e.unique_id.startswith(prefix)}
    missing = expected_suffixes - found
    assert not missing, f"missing diagnostic sensors: {missing}"


# ---------------------------------------------------------------------------
# M6A-02: PendingPostsSensor tests
# ---------------------------------------------------------------------------


async def test_pending_posts_sensor_registered(hass, mock_config_entry):
    """M6A-02: PendingPostsSensor registered at setup under the expected unique_id."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_pending_parcelapp_posts"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"pending_parcelapp_posts diagnostic sensor not registered. "
        f"Found suffixes: {[e.unique_id for e in entries]}"
    )


async def test_pending_posts_sensor_native_value_reflects_pending(hass, mock_config_entry):
    """M6A-02: PendingPostsSensor.native_value == len(coordinator._pending_posts)."""
    from custom_components.shop2parcel.diagnostic_sensor import PendingPostsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = PendingPostsSensor(coordinator, mock_config_entry)

    # At rest: zero pending posts
    assert sensor.native_value == 0

    # Seed two pending shipments
    coordinator._pending_posts["key1"] = _make_shipment("msg1")
    coordinator._pending_posts["key2"] = _make_shipment("msg2")
    assert sensor.native_value == 2

    # Remove one — value updates immediately (property reads live dict)
    del coordinator._pending_posts["key1"]
    assert sensor.native_value == 1


async def test_pending_posts_sensor_attributes_capped_at_10(hass, mock_config_entry):
    """M6A-02 / T-m6a-01: pending_tracking_numbers attribute is capped at 10 (recorder DoS guard)."""
    from custom_components.shop2parcel.diagnostic_sensor import PendingPostsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)

    # Seed 15 pending shipments
    for i in range(15):
        shipment = ShipmentData(
            tracking_number=f"TRK{i:04d}",
            carrier_name="UPS",
            order_name=f"#ORDER{i}",
            message_id=f"msg{i}",
            email_date=1700000000,
        )
        coordinator._pending_posts[f"key{i}"] = shipment

    sensor = PendingPostsSensor(coordinator, mock_config_entry)
    attrs = sensor.extra_state_attributes
    assert "pending_tracking_numbers" in attrs
    pending_list = attrs["pending_tracking_numbers"]
    assert isinstance(pending_list, list)
    assert len(pending_list) == 10, f"expected 10 entries (capped), got {len(pending_list)}"
    # Each entry must have exactly the compact triple
    for entry in pending_list:
        assert set(entry.keys()) == {"tracking_number", "carrier", "order_name"}, (
            f"unexpected keys: {set(entry.keys())}"
        )


async def test_pending_posts_sensor_unique_id_and_category(hass, mock_config_entry):
    """M6A-02: PendingPostsSensor unique_id suffix, DIAGNOSTIC category, MEASUREMENT state class."""
    from homeassistant.components.sensor import SensorStateClass
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.shop2parcel.diagnostic_sensor import PendingPostsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = PendingPostsSensor(coordinator, mock_config_entry)

    assert sensor._attr_unique_id == (
        f"{DOMAIN}_{mock_config_entry.entry_id}_pending_parcelapp_posts"
    )
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# Phase 28 Plan 05 — R4: CarrierFormatRejectionsSensor tests
# ---------------------------------------------------------------------------


async def test_carrier_format_rejections_sensor_registered(hass, mock_config_entry):
    """R4: CarrierFormatRejectionsSensor registered at setup under the expected unique_id."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_carrier_format_rejections"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"carrier_format_rejections diagnostic sensor not registered. "
        f"Found suffixes: {[e.unique_id for e in entries]}"
    )


async def test_carrier_format_rejections_included_in_all_sensors_registered(
    hass, mock_config_entry
):
    """R4/D-09: 'carrier_format_rejections' suffix present in all registered diagnostic sensors."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    expected_suffixes = {
        "emails_scanned",
        "new_emails_inspected",
        "emails_matched",
        "tracking_numbers_found",
        "keyword_hits",
        "activity_log",
        "stage2_queue",
        "carrier_format_rejections",
    }
    found = {e.unique_id.removeprefix(prefix) for e in entries if e.unique_id.startswith(prefix)}
    missing = expected_suffixes - found
    assert not missing, f"missing diagnostic sensors: {missing}"


async def test_carrier_format_rejections_sensor_state_after_n_rejections(hass, mock_config_entry):
    """R4 acceptance: state == N after N record_carrier_format_rejection() calls.

    Call record_carrier_format_rejection() twice, then assert:
    - sensor state == 2
    - last_rejected_value attribute == the rejected (cleaned) value
    - last_rejected_reason attribute == the rejection reason
    """
    from custom_components.shop2parcel.diagnostic_sensor import CarrierFormatRejectionsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)

    # Simulate 2 gate rejections using the PollStats method (mirrors drain/merge paths)
    coordinator._diagnostics.record_carrier_format_rejection("ORDER12345", "no_carrier_match")
    coordinator._diagnostics.record_carrier_format_rejection("ORDER12345", "no_carrier_match")

    sensor = CarrierFormatRejectionsSensor(coordinator, mock_config_entry)

    # State: native_value == rejection count
    assert sensor.native_value == 2, (
        f"expected native_value=2 after 2 rejections, got {sensor.native_value}"
    )

    # Attributes: last rejected value and reason
    attrs = sensor.extra_state_attributes
    assert attrs["last_rejected_value"] == "ORDER12345", (
        f"expected last_rejected_value='ORDER12345', got {attrs.get('last_rejected_value')}"
    )
    assert attrs["last_rejected_reason"] == "no_carrier_match", (
        f"expected last_rejected_reason='no_carrier_match', got {attrs.get('last_rejected_reason')}"
    )
    assert "description" in attrs, "expected 'description' key in extra_state_attributes"


async def test_carrier_format_rejections_sensor_unique_id_and_category(hass, mock_config_entry):
    """R4: CarrierFormatRejectionsSensor unique_id suffix, DIAGNOSTIC category, MEASUREMENT class."""
    from homeassistant.components.sensor import SensorStateClass
    from homeassistant.helpers.entity import EntityCategory

    from custom_components.shop2parcel.diagnostic_sensor import CarrierFormatRejectionsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = CarrierFormatRejectionsSensor(coordinator, mock_config_entry)

    assert sensor._attr_unique_id == (
        f"{DOMAIN}_{mock_config_entry.entry_id}_carrier_format_rejections"
    )
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.state_class == SensorStateClass.MEASUREMENT


async def test_carrier_format_rejections_sensor_initial_state_zero(hass, mock_config_entry):
    """R4: CarrierFormatRejectionsSensor state == 0 before any rejections."""
    from custom_components.shop2parcel.diagnostic_sensor import CarrierFormatRejectionsSensor

    coordinator = await _setup_integration(hass, mock_config_entry)
    sensor = CarrierFormatRejectionsSensor(coordinator, mock_config_entry)

    assert sensor.native_value == 0, (
        f"expected native_value=0 before any rejections, got {sensor.native_value}"
    )
    attrs = sensor.extra_state_attributes
    assert attrs["last_rejected_value"] is None
    assert attrs["last_rejected_reason"] is None
