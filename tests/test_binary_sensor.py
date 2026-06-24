"""Tests for Shop2Parcel binary_sensor.py.

Coverage:
- Phase 5 ENTT-03: HasActiveShipmentsBinarySensor (D-07: is_on = len(coordinator.data) > 0).
- M6A-01: EmailProcessingActiveBinarySensor (DIAGNOSTIC category; reads email_processing_active).
"""

from __future__ import annotations

import asyncio

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.binary_sensor import EmailProcessingActiveBinarySensor
from custom_components.shop2parcel.const import DOMAIN
from tests.conftest import setup_coordinator_with_data as _setup_with_data


def _make_shipment(message_id: str, tracking: str) -> ShipmentData:
    return ShipmentData(
        tracking_number=tracking,
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1745452800,
    )


async def test_binary_sensor_on_when_data_non_empty(hass, mock_config_entry):
    """ENTT-03 / D-07: is_on True when at least one shipment in coordinator.data."""
    data = {"msg_a": _make_shipment("msg_a", "1Z999AA10123456784")}
    await _setup_with_data(hass, mock_config_entry, data)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    bs_uid = f"{DOMAIN}_{mock_config_entry.entry_id}_has_active_shipments"
    bs_entry = next(
        (e for e in entries if e.unique_id == bs_uid),
        None,
    )
    assert bs_entry is not None, (
        f"Binary sensor {bs_uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    state = hass.states.get(bs_entry.entity_id)
    assert state is not None
    assert state.state == "on"


async def test_binary_sensor_off_when_data_empty(hass, mock_config_entry):
    """ENTT-03 / D-07: is_on False when coordinator.data is empty."""
    await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    bs_uid = f"{DOMAIN}_{mock_config_entry.entry_id}_has_active_shipments"
    bs_entry = next(
        (e for e in entries if e.unique_id == bs_uid),
        None,
    )
    assert bs_entry is not None, (
        f"Binary sensor {bs_uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    state = hass.states.get(bs_entry.entity_id)
    assert state is not None
    assert state.state == "off"


# ---------------------------------------------------------------------------
# M6A-01: EmailProcessingActiveBinarySensor tests
# ---------------------------------------------------------------------------


async def test_email_processing_sensor_registered_and_off_at_rest(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor registered at setup; off when idle."""
    await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_email_processing_active"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"email_processing_active sensor {uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "off"


async def test_email_processing_sensor_on_when_active(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor.is_on tracks coordinator.email_processing_active."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)

    # At rest: both sources off
    assert coordinator._poll_in_progress is False
    assert coordinator.stage2_queue_depth == 0
    assert sensor.is_on is False

    # _poll_in_progress True → sensor on
    coordinator._poll_in_progress = True
    assert sensor.is_on is True

    # Reset flag but seed Stage-2 queue → sensor stays on
    coordinator._poll_in_progress = False
    coordinator._stage2_queue = asyncio.Queue(maxsize=16)
    coordinator._stage2_queue.put_nowait.__func__  # just touch queue so it's non-empty
    # Put a sentinel item on the queue directly
    await coordinator._stage2_queue.put("sentinel")
    assert coordinator.stage2_queue_depth == 1
    assert sensor.is_on is True

    # Drain queue and reset flag → sensor off
    coordinator._stage2_queue.get_nowait()
    assert coordinator.stage2_queue_depth == 0
    assert sensor.is_on is False


async def test_email_processing_sensor_is_diagnostic_category(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor entity_category is DIAGNOSTIC."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


async def test_email_processing_sensor_unique_id_format(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor unique_id uses the standard suffix."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)
    assert (
        sensor._attr_unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_email_processing_active"
    )
