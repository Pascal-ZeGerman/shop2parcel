"""Tests for Shop2Parcel __init__.py — async_setup_entry and async_unload_entry."""
from __future__ import annotations

from aioresponses import aioresponses

from homeassistant.config_entries import ConfigEntryState

from custom_components.shop2parcel.api.parcelapp import VIEW_DELIVERIES_URL
from custom_components.shop2parcel.const import DOMAIN


async def test_setup_entry_success(hass, mock_config_entry):
    """Successful setup stores placeholder in hass.data[DOMAIN][entry_id]."""
    mock_config_entry.add_to_hass(hass)
    with aioresponses() as mock:
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=recent",
            payload={"deliveries": []},
            status=200,
        )
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_setup_entry_auth_failure(hass, mock_config_entry):
    """ParcelApp 401 → HA absorbs ConfigEntryAuthFailed, sets state SETUP_ERROR."""
    mock_config_entry.add_to_hass(hass)
    with aioresponses() as mock:
        mock.get(VIEW_DELIVERIES_URL + "?filter_mode=recent", status=401)
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_transient_failure(hass, mock_config_entry):
    """ParcelApp 503 → HA absorbs ConfigEntryNotReady, sets state SETUP_RETRY."""
    mock_config_entry.add_to_hass(hass)
    with aioresponses() as mock:
        mock.get(VIEW_DELIVERIES_URL + "?filter_mode=recent", status=503)
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry_removes_data(hass, mock_config_entry):
    """async_unload_entry removes the entry from hass.data[DOMAIN]."""
    mock_config_entry.add_to_hass(hass)
    with aioresponses() as mock:
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=recent",
            payload={"deliveries": []},
            status=200,
        )
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})
