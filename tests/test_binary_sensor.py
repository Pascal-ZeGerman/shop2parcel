"""Tests for Shop2Parcel binary_sensor.py — Phase 5 HasActiveShipmentsBinarySensor.

Wave 0 scaffolds: binary_sensor.py does not yet exist, so this module's tests
will ImportError until Plan 02 lands the implementation.

Coverage: ENTT-03 (D-07: is_on = len(coordinator.data) > 0).
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.api.email_parser import ShipmentData
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
