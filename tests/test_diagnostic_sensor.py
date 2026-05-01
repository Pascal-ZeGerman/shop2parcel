"""Wave 0 stubs for diagnostic_sensor.py — Plan 03 will fill in.

Phase 7 (DIAG-08, DIAG-09, DIAG-10):
- DIAG-08: 4 diagnostic sensor entities are registered at setup time.
- DIAG-09: sensor.shop2parcel_emails_scanned state == coordinator._diagnostics.emails_scanned_total.
- DIAG-10: All 4 diagnostic sensors share the same Shop2Parcel device as the shipment sensors.

These tests are marked xfail because diagnostic_sensor.py does not yet exist
and the coordinator does not yet expose the _diagnostics attribute. Plan 03
removes the xfail markers as it lands the implementation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.const import DOMAIN


async def _setup_integration(hass, mock_config_entry):
    """Set up the integration with mocked Gmail/parcelapp/parser/Store/oauth.

    Mirrors tests/test_binary_sensor.py::_setup_with_data — the diagnostic
    sensor tests do not need seeded coordinator.data because diagnostic
    sensors are static (always exist, regardless of shipments).
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(
            return_value=MagicMock()
        )
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        await hass.async_block_till_done()
        return coordinator


@pytest.mark.xfail(reason="Wave 0 stub — diagnostic_sensor.py does not yet exist (Plan 03)")
async def test_emails_scanned_sensor_registered(hass, mock_config_entry):
    """DIAG-08 / DIAG-09: sensor.shop2parcel_emails_scanned registered at setup; state=0."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_emails_scanned"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "emails_scanned diagnostic sensor not registered"
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"


@pytest.mark.xfail(reason="Wave 0 stub — diagnostic_sensor.py does not yet exist (Plan 03)")
async def test_all_four_diagnostic_sensors_registered(hass, mock_config_entry):
    """DIAG-08: all 4 diagnostic sensors registered at setup."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    expected_suffixes = {
        "emails_scanned",
        "emails_matched",
        "tracking_numbers_found",
        "keyword_hits",
    }
    found = {e.unique_id.removeprefix(prefix) for e in entries if e.unique_id.startswith(prefix)}
    missing = expected_suffixes - found
    assert not missing, f"missing diagnostic sensors: {missing}"


@pytest.mark.xfail(reason="Wave 0 stub — diagnostic_sensor.py does not yet exist (Plan 03)")
async def test_diagnostic_sensors_share_device(hass, mock_config_entry):
    """DIAG-10: Diagnostic sensors share the same Shop2Parcel device (one per config entry)."""
    await _setup_integration(hass, mock_config_entry)
    device_reg = dr.async_get(hass)
    devices = [
        d
        for d in device_reg.devices.values()
        if (DOMAIN, mock_config_entry.entry_id) in d.identifiers
    ]
    assert len(devices) == 1, f"expected exactly 1 device, got {len(devices)}"
