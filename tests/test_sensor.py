"""Tests for Shop2Parcel sensor.py — Phase 5 ShipmentSensor entity.

Wave 0 scaffolds: sensor.py does not yet exist, so the import line below will
ImportError until Plan 02 lands. That is intentional — these tests fail until
the sensor platform is implemented.

Coverage: ENTT-01, ENTT-02, ENTT-04, ENTT-05, ENTT-06.
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


async def test_sensor_created_for_each_shipment(hass, mock_config_entry):
    """ENTT-01: One sensor.shop2parcel_* entity per coordinator.data entry."""
    data = {
        "msg_a": _make_shipment("msg_a", "1Z999AA10123456784"),
        "msg_b": _make_shipment("msg_b", "1Z999AA10123456785", order="#1235"),
    }
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    sensor_unique_ids = {e.unique_id for e in entries if e.entity_id.startswith("sensor.")}
    assert f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a" in sensor_unique_ids
    assert f"{DOMAIN}_{mock_config_entry.entry_id}_msg_b" in sensor_unique_ids


async def test_sensor_attributes(hass, mock_config_entry):
    """ENTT-02 / D-03: Attributes contain order_name, tracking_number, carrier, email_date."""
    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    sensor_entry = next(
        e for e in entries if e.unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a"
    )
    state = hass.states.get(sensor_entry.entity_id)
    assert state is not None
    assert state.attributes.get("order_name") == "#1234"
    assert state.attributes.get("tracking_number") == "1Z999AA10123456784"
    assert state.attributes.get("carrier") == "UPS"
    assert state.attributes.get("email_date") == 1745452800


async def test_sensor_native_value_is_in_transit(hass, mock_config_entry):
    """D-01: Sensor state is the static literal 'in_transit'."""
    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    sensor_entry = next(
        e for e in entries if e.unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a"
    )
    state = hass.states.get(sensor_entry.entity_id)
    assert state is not None
    assert state.state == "in_transit"


async def test_sensor_unique_id_stable(hass, mock_config_entry):
    """ENTT-04 / D-05: unique_id format is f'{DOMAIN}_{entry_id}_{message_id}'."""
    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    expected_uid = f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a"
    assert any(e.unique_id == expected_uid for e in entries)


async def test_device_grouping(hass, mock_config_entry):
    """ENTT-06 / D-06: All entities share DeviceInfo identifiers={(DOMAIN, entry_id)}."""
    from homeassistant.helpers import device_registry as dr

    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    await _setup_with_data(hass, mock_config_entry, data)
    device_reg = dr.async_get(hass)
    devices = [
        d
        for d in device_reg.devices.values()
        if (DOMAIN, mock_config_entry.entry_id) in d.identifiers
    ]
    assert len(devices) == 1, f"Expected exactly one Shop2Parcel device, found {len(devices)}"
    # Both sensor and binary_sensor must attach to this device
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    device_ids = {e.device_id for e in entries if e.device_id is not None}
    assert devices[0].id in device_ids


async def test_cleanup_removes_entity(hass, mock_config_entry):
    """ENTT-05: After async_cleanup_delivered drops a key, entity is removed from registry.

    Seeds coordinator.data with one shipment, mocks parcelapp GET to return status_code=0
    for that tracking_number, calls async_cleanup_delivered, asserts the registry entry
    is gone (not just unavailable).
    """
    from datetime import datetime, timezone

    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    coordinator = await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    pre_entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    pre_uids = {e.unique_id for e in pre_entries}
    assert f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a" in pre_uids

    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(
        return_value=[
            {"tracking_number": "1Z999AA10123456784", "status_code": 0},
        ]
    )
    with patch(
        "custom_components.shop2parcel.coordinator.ParcelAppClient",
        return_value=fake_client,
    ):
        await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))
    await hass.async_block_till_done()

    post_entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    post_uids = {e.unique_id for e in post_entries}
    assert f"{DOMAIN}_{mock_config_entry.entry_id}_msg_a" not in post_uids
    assert "msg_a" not in coordinator.data


# ---------------------------------------------------------------------------
# FLD-03 sensor attribute tests (Phase 21 Plan 01)
# ---------------------------------------------------------------------------


async def test_shipment_sensor_extra_state_attributes_spreads_custom_attributes(
    hass, mock_config_entry
):
    """FLD-03: ShipmentSensor.extra_state_attributes includes custom_attributes keys."""
    data = {
        "msg_ca": ShipmentData(
            tracking_number="1Z999AA10123456784",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg_ca",
            email_date=1745452800,
            custom_attributes={"estimated_delivery": "2026-06-20", "weight": "1kg"},
        )
    }
    await _setup_with_data(hass, mock_config_entry, data)
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    sensor_entry = next(
        e for e in entries if e.unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_msg_ca"
    )
    state = hass.states.get(sensor_entry.entity_id)
    assert state is not None
    attrs = state.attributes
    # Fixed keys must be present
    assert "order_name" in attrs
    assert "tracking_number" in attrs
    assert "carrier" in attrs
    assert "email_date" in attrs
    # Custom attributes spread in
    assert attrs.get("estimated_delivery") == "2026-06-20", (
        "custom_attributes key 'estimated_delivery' must appear in extra_state_attributes"
    )
    assert attrs.get("weight") == "1kg", (
        "custom_attributes key 'weight' must appear in extra_state_attributes"
    )


async def test_shipment_sensor_extra_state_attributes_empty_custom_attributes_returns_only_fixed_keys(
    hass, mock_config_entry
):
    """FLD-03: Empty custom_attributes returns exactly the 4 fixed keys."""
    data = {
        "msg_empty": ShipmentData(
            tracking_number="1Z999AA10123456784",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg_empty",
            email_date=1745452800,
            custom_attributes={},
        )
    }
    await _setup_with_data(hass, mock_config_entry, data)
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    sensor_entry = next(
        e for e in entries if e.unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_msg_empty"
    )
    state = hass.states.get(sensor_entry.entity_id)
    assert state is not None
    # HA adds "friendly_name" to attributes automatically; filter that out
    sensor_attrs = {k: v for k, v in state.attributes.items() if k != "friendly_name"}
    assert set(sensor_attrs.keys()) == {"order_name", "tracking_number", "carrier", "email_date"}, (
        f"With empty custom_attributes, only 4 fixed keys expected; got {set(sensor_attrs.keys())}"
    )


async def test_parcelapp_post_never_includes_custom_field_keys(hass, mock_config_entry):
    """FLD-03 POST guard: async_add_delivery is called with only the 3 contract kwargs.

    Constructs a Stage2Job where the extractor returns custom={"estimated_delivery": "2026-06-20"},
    mocks parcel_client.async_add_delivery, calls _async_process_stage2_job, and asserts
    that only tracking_number, carrier_code, description reach parcelapp.net.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from custom_components.shop2parcel.coordinator import Stage2Job
    from custom_components.shop2parcel.extractors.types import Stage2Result

    coordinator = await _setup_with_data(hass, mock_config_entry, {})

    # Simulate a stage2 job with a shipment that has a custom field
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

    # Stage-2 extractor returns custom field
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

        # Initialize per-poll counters
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


async def test_sensor_appears_when_data_gains_entry(hass, mock_config_entry):
    """ENTT-01 / Phase 6 D-01 gap fill: sensor entity registered AFTER coordinator.data
    gains a new message_id key mid-run.

    Existing tests (test_sensor_created_for_each_shipment) pre-seed data BEFORE setup.
    This test sets up with EMPTY data, then dispatches a coordinator update with a new
    key and asserts the listener registered a new sensor entity.
    """
    coordinator = await _setup_with_data(hass, mock_config_entry, {})

    registry = er.async_get(hass)
    new_uid = f"{DOMAIN}_{mock_config_entry.entry_id}_msg_new"

    # Pre-condition: no sensor entity for "msg_new" yet
    pre_entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    pre_sensor_uids = {e.unique_id for e in pre_entries if e.entity_id.startswith("sensor.")}
    assert new_uid not in pre_sensor_uids

    # Dispatch coordinator update with a NEW shipment
    coordinator.async_set_updated_data({"msg_new": _make_shipment("msg_new", "1Z999AA10123456784")})
    await hass.async_block_till_done()

    # Post-condition: sensor.shop2parcel_*_msg_new is now registered
    post_entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    post_sensor_uids = {e.unique_id for e in post_entries if e.entity_id.startswith("sensor.")}
    assert new_uid in post_sensor_uids, (
        f"Expected {new_uid} in entity registry after coordinator update; found {post_sensor_uids}"
    )
