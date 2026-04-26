"""Tests for Shop2Parcel __init__.py — Phase 4 coordinator wiring.

Verifies async_setup_entry instantiates Shop2ParcelCoordinator, hydrates Store
before first refresh, and that hass.data[DOMAIN][entry_id] holds the coordinator
instance (replacing Phase 3's empty-dict placeholder).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.shop2parcel.api.exceptions import GmailAuthError
from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator


async def test_setup_entry_wires_coordinator(hass, mock_config_entry):
    """Phase 4 setup stores Shop2ParcelCoordinator (NOT a placeholder dict) in hass.data."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert isinstance(hass.data[DOMAIN][mock_config_entry.entry_id], Shop2ParcelCoordinator)


async def test_setup_entry_calls_load_store_before_first_refresh(hass, mock_config_entry):
    """RESEARCH.md Pitfall 1: Store.async_load MUST run before _async_update_data.

    Otherwise forwarded_ids is empty on first poll and every prior shipment is re-POSTed.
    """
    mock_config_entry.add_to_hass(hass)
    parent = MagicMock()
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        # Track call order via parent mock
        store_load = AsyncMock(return_value=None)
        list_messages = AsyncMock(return_value=[])
        parent.attach_mock(store_load, "async_load")
        parent.attach_mock(list_messages, "async_list_messages")
        mock_store_cls.return_value.async_load = store_load
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = list_messages
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    # Assert async_load was called before async_list_messages
    call_names = [c[0] for c in parent.mock_calls if c[0] in ("async_load", "async_list_messages")]
    assert call_names.index("async_load") < call_names.index("async_list_messages")


async def test_setup_entry_gmail_auth_failure_sets_setup_error(hass, mock_config_entry):
    """Gmail auth error -> coordinator raises ConfigEntryAuthFailed -> HA SETUP_ERROR state."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("fake auth fail")
        )
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry_removes_coordinator(hass, mock_config_entry):
    """async_unload_entry calls async_unload_platforms with PLATFORMS=[] then drops hass.data."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert mock_config_entry.entry_id in hass.data[DOMAIN]
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_entry_forwards_to_empty_platforms(hass, mock_config_entry):
    """CONTEXT.md D-09: PLATFORMS=[] in Phase 4; Phase 5 adds entities."""
    from custom_components.shop2parcel import PLATFORMS
    assert PLATFORMS == []
