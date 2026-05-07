"""Tests for Shop2Parcel button platform — ResetEmailCacheButton."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components.shop2parcel.const import DOMAIN


async def _setup_integration(hass, mock_config_entry):
    """Set up the integration with all coordinator deps mocked."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        await hass.async_block_till_done()
        return coordinator


async def test_reset_button_registered(hass, mock_config_entry):
    """ResetEmailCacheButton registered at setup with entity_category=CONFIG."""
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_reset_email_cache"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "reset_email_cache button not registered"
    assert entry.entity_category == EntityCategory.CONFIG


async def test_reset_button_press_clears_forwarded_ids(hass, mock_config_entry):
    """Pressing the button clears forwarded_ids and resets timestamps."""
    coordinator = await _setup_integration(hass, mock_config_entry)

    # Pre-seed some state to verify it gets cleared.
    coordinator._forwarded_ids = {"msg1", "msg2"}
    coordinator._last_email_timestamp = 1700000000
    coordinator._last_imap_uid = 99

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_reset_email_cache"
    reg_entry = next(e for e in entries if e.unique_id == uid)

    # Patch async_request_refresh so it doesn't trigger a real poll.
    with patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as mock_refresh:
        # Press the button via HA's button press service.
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": reg_entry.entity_id},
            blocking=True,
        )

    assert coordinator._forwarded_ids == set()
    assert coordinator._last_email_timestamp is None
    assert coordinator._last_imap_uid is None
    mock_refresh.assert_called_once()


async def test_reset_button_press_saves_store(hass, mock_config_entry):
    """Pressing the button persists the cleared state to Store."""
    coordinator = await _setup_integration(hass, mock_config_entry)
    coordinator._forwarded_ids = {"msg1"}

    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_reset_email_cache"
    reg_entry = next(e for e in entries if e.unique_id == uid)

    with (
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()),
        patch.object(coordinator._store, "async_save", new=AsyncMock()) as mock_save,
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": reg_entry.entity_id},
            blocking=True,
        )

    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert saved["forwarded_ids"] == []
    assert saved["last_email_timestamp"] is None
    assert saved["last_imap_uid"] is None
