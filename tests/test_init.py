"""Tests for Shop2Parcel __init__.py — async_setup_entry and async_unload_entry."""
from __future__ import annotations

import pytest
from aioresponses import aioresponses

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.shop2parcel.api.parcelapp import VIEW_DELIVERIES_URL
from custom_components.shop2parcel.const import DOMAIN


@pytest.mark.xfail(strict=False, reason="__init__.py async_setup_entry not yet written")
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


@pytest.mark.xfail(strict=False, reason="__init__.py async_setup_entry not yet written")
async def test_setup_entry_auth_failure(hass, mock_config_entry):
    """ParcelApp 401 raises ConfigEntryAuthFailed (not UpdateFailed)."""
    mock_config_entry.add_to_hass(hass)
    with (
        aioresponses() as mock,
        pytest.raises(ConfigEntryAuthFailed),
    ):
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=recent",
            status=401,
        )
        await hass.config_entries.async_setup(mock_config_entry.entry_id)


@pytest.mark.xfail(strict=False, reason="__init__.py async_setup_entry not yet written")
async def test_setup_entry_transient_failure(hass, mock_config_entry):
    """ParcelApp 503 raises ConfigEntryNotReady (retry later)."""
    mock_config_entry.add_to_hass(hass)
    with (
        aioresponses() as mock,
        pytest.raises(ConfigEntryNotReady),
    ):
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=recent",
            status=503,
        )
        await hass.config_entries.async_setup(mock_config_entry.entry_id)


@pytest.mark.xfail(strict=False, reason="__init__.py async_unload_entry not yet written")
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
