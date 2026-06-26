"""Tests for Shop2Parcel sensor.py — Phase 26 Plan 03 operational-health sensors.

Wave 0 scaffolds: new operational sensors do not yet exist, so these tests will
fail until the sensor platform is updated (ShipmentSensor removed, new sensors added).

Coverage: P26-ENT-01, P26-ENT-02, P26-ENT-03, P26-REMOVE-01.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN
from tests.conftest import setup_coordinator_with_data as _setup_with_data


def _make_shipment(message_id: str, tracking: str, order: str = "#1234") -> ShipmentData:
    return ShipmentData(
        tracking_number=tracking,
        carrier_name="UPS",
        order_name=order,
        message_id=message_id,
        email_date=1745452800,
    )


# ---------------------------------------------------------------------------
# Phase 26 Plan 03: operational sensor tests
# ---------------------------------------------------------------------------


async def test_shipments_forwarded_sensor_initial_state(hass, mock_config_entry):
    """P26-ENT-01: ShipmentsForwardedSensor registers with TOTAL_INCREASING, initial value 0."""
    from homeassistant.components.sensor import SensorStateClass

    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_shipments_forwarded"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"ShipmentsForwardedSensor {uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"
    # state_class must be TOTAL_INCREASING
    assert state.attributes.get("state_class") == SensorStateClass.TOTAL_INCREASING
    # currently_tracked attribute must exist
    assert "currently_tracked" in state.attributes


async def test_last_forwarded_sensor_none_until_first_post(hass, mock_config_entry):
    """P26-ENT-02: LastForwardedSensor returns None before first forward; datetime after."""
    from datetime import UTC, datetime

    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_last_forwarded"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"LastForwardedSensor {uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    # Before first forward, state should be 'unknown' or 'unavailable'
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state in ("unknown", "unavailable")

    # Simulate first forward by setting coordinator._last_forwarded_ts
    test_epoch = 1750000000
    coordinator._last_forwarded_ts = test_epoch
    coordinator.async_set_updated_data(coordinator.data or {})
    await hass.async_block_till_done()

    state = hass.states.get(entry.entity_id)
    assert state is not None
    # HA stores datetime sensors as ISO8601 strings
    expected_dt = datetime.fromtimestamp(test_epoch, tz=UTC)
    assert state.state != "unknown"
    assert state.state != "unavailable"
    # The state should match the expected datetime; compare as datetime
    parsed = datetime.fromisoformat(state.state.replace("Z", "+00:00"))
    assert parsed == expected_dt


async def test_parcelapp_quota_sensor_estimate(hass, mock_config_entry):
    """P26-ENT-03: ParcelAppQuotaSensor shows max(0, 20-used_today); attributes include daily_limit/used_today/exhausted."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_parcelapp_quota"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"ParcelAppQuotaSensor {uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )

    # Initial state: used_today=0, quota=20
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "20"
    assert state.attributes.get("daily_limit") == 20
    assert state.attributes.get("used_today") == 0
    assert "exhausted" in state.attributes

    # Simulate used_today=3
    coordinator._used_today = 3
    coordinator.async_set_updated_data(coordinator.data or {})
    await hass.async_block_till_done()
    state = hass.states.get(entry.entity_id)
    assert state.state == "17"
    assert state.attributes.get("used_today") == 3

    # Simulate over-limit: used_today=25 -> native_value clamped to 0
    coordinator._used_today = 25
    coordinator.async_set_updated_data(coordinator.data or {})
    await hass.async_block_till_done()
    state = hass.states.get(entry.entity_id)
    assert state.state == "0", f"Expected clamped value 0 when used_today=25; got {state.state}"


async def test_no_shipment_sensor_registered(hass, mock_config_entry):
    """P26-REMOVE-01: After setup with coordinator.data containing two shipments,
    NO per-shipment sensor entity registers — only diagnostic + 3 operational sensors."""
    # Known operational/diagnostic suffixes that should exist
    known_suffixes = {
        # 13 diagnostic
        "emails_scanned",
        "new_emails_inspected",
        "emails_matched",
        "tracking_numbers_found",
        "keyword_hits",
        "activity_log",
        "stage2_queue",
        "ollama_latency",
        "ollama_parse_retries",
        "stage2_consecutive_failures",
        "emails_sent_to_llm",
        "emails_parsed_by_llm",
        "pending_parcelapp_posts",
        # 3 operational sensors
        "shipments_forwarded",
        "last_forwarded",
        "parcelapp_quota",
        # 2 binary sensors (diagnostic + operational)
        "email_processing_active",
        "problem",
    }
    data = {
        "msg_a": _make_shipment("msg_a", "1Z999AA10123456784"),
        "msg_b": _make_shipment("msg_b", "1Z999AA10123456785", order="#1235"),
    }
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    unknown_suffixes = []
    for e in entries:
        if e.unique_id.startswith(prefix):
            suffix = e.unique_id[len(prefix) :]
            if suffix not in known_suffixes:
                unknown_suffixes.append(suffix)
    assert unknown_suffixes == [], (
        f"Found per-shipment or unknown sensor suffixes: {unknown_suffixes}. "
        "ShipmentSensor must not register after Phase 26 Plan 03."
    )


# ---------------------------------------------------------------------------
# Phase 5 tests retained — not ShipmentSensor-specific
# ---------------------------------------------------------------------------


async def test_device_grouping(hass, mock_config_entry):
    """D-06: All entities share DeviceInfo identifiers={(DOMAIN, entry_id)}."""
    from homeassistant.helpers import device_registry as dr

    await _setup_with_data(hass, mock_config_entry, {})
    device_reg = dr.async_get(hass)
    devices = [
        d
        for d in device_reg.devices.values()
        if (DOMAIN, mock_config_entry.entry_id) in d.identifiers
    ]
    assert len(devices) == 1, f"Expected exactly one Shop2Parcel device, found {len(devices)}"
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    device_ids = {e.device_id for e in entries if e.device_id is not None}
    assert devices[0].id in device_ids


async def test_parcelapp_post_never_includes_custom_field_keys(hass, mock_config_entry):
    """FLD-03 POST guard: async_add_delivery is called with only the 3 contract kwargs.

    Constructs a Stage2Job where the extractor returns custom={"estimated_delivery": "2026-06-20"},
    mocks parcel_client.async_add_delivery, calls _async_process_stage2_job, and asserts
    that only tracking_number, carrier_code, description reach parcelapp.net.
    """
    from custom_components.shop2parcel.coordinator import Stage2Job
    from custom_components.shop2parcel.extractors.types import Stage2Result

    coordinator = await _setup_with_data(hass, mock_config_entry, {})

    shipment = ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id="msg_pg",
        email_date=1745452800,
    )
    job = Stage2Job(
        storage_key="msg_pg",
        normalized_tn="1Z999AA10123456784",
        shipment=shipment,
        html_body="<html>test</html>",
        message_id="msg_pg",
        meta={"subject": "Your order has shipped", "from": "no-reply@shopify.com"},
    )

    stage2_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "#1234",
        },
        custom={"estimated_delivery": "2026-06-20"},
        passes_used=1,
        latency_ms=10.0,
    )

    mock_add_delivery = AsyncMock()

    with (
        patch.object(coordinator, "_extractor") as mock_extractor,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_extractor.async_extract = AsyncMock(return_value=stage2_result)
        mock_parcel_cls.return_value.async_add_delivery = mock_add_delivery

        coordinator._stage2_posts_this_poll = 0
        coordinator._stage2_cap_notified_this_poll = False
        coordinator._stage2_enqueued_keys = set()

        await coordinator._async_process_stage2_job(job)

    assert mock_add_delivery.called, "async_add_delivery must have been called"
    call_kwargs = set(mock_add_delivery.call_args.kwargs.keys())
    assert call_kwargs == {"tracking_number", "carrier_code", "description"}, (
        f"async_add_delivery must receive only 3 contract kwargs; got {call_kwargs}"
    )
    assert "estimated_delivery" not in call_kwargs, (
        "Custom field 'estimated_delivery' must NOT reach async_add_delivery (FLD-03 POST guard)"
    )
