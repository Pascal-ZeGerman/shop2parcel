"""Tests for Shop2Parcel __init__.py — Phase 5 coordinator wiring.

Verifies async_setup_entry instantiates Shop2ParcelCoordinator, hydrates Store
before first refresh, and that hass.data[DOMAIN][entry_id] holds a dict with
"coordinator" key (Phase 5 dict shape). cancel_cleanup is registered via
entry.async_on_unload rather than stored in hass.data (WR-03 fix).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.shop2parcel.api.exceptions import GmailAuthError
from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator


async def test_setup_entry_wires_coordinator(hass, mock_config_entry):
    """Phase 5 setup stores dict with coordinator in hass.data.

    cancel_cleanup is registered via entry.async_on_unload (WR-03) and is
    therefore NOT stored in hass.data — HA calls it automatically on unload.
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    entry_data = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert isinstance(entry_data, dict)
    assert isinstance(entry_data["coordinator"], Shop2ParcelCoordinator)
    # cancel_cleanup is registered via entry.async_on_unload, not stored in hass.data
    assert "cancel_cleanup" not in entry_data


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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("fake auth fail")
        )
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry_removes_coordinator(hass, mock_config_entry):
    """async_unload_entry calls async_unload_platforms with PLATFORMS then drops hass.data."""
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert mock_config_entry.entry_id in hass.data[DOMAIN]
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_entry_forwards_to_sensor_platforms(hass, mock_config_entry):
    """CONTEXT.md D-09: PLATFORMS = ['sensor', 'binary_sensor'] in Phase 5."""
    from custom_components.shop2parcel import PLATFORMS

    assert PLATFORMS == ["sensor", "binary_sensor"]


async def test_setup_entry_registers_cleanup_task_with_24h_interval(hass, mock_config_entry):
    """D-08: async_track_time_interval is registered with timedelta(hours=24)."""
    from datetime import timedelta

    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch("custom_components.shop2parcel.async_track_time_interval") as mock_track,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        cancel_cb = MagicMock()
        mock_track.return_value = cancel_cb
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    # Assert async_track_time_interval was called with the 24h timedelta
    assert mock_track.called
    call_args = mock_track.call_args
    # Positional args: (hass, callback, interval) — interval is positional in HA's signature
    interval_arg = (
        call_args.args[2] if len(call_args.args) >= 3 else call_args.kwargs.get("interval")
    )
    assert interval_arg == timedelta(hours=24)


async def test_unload_entry_cancels_cleanup_task(hass, mock_config_entry):
    """D-10: async_unload_entry must invoke the cancel callback returned by async_track_time_interval."""
    mock_config_entry.add_to_hass(hass)
    cancel_cb = MagicMock()
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch("custom_components.shop2parcel.async_track_time_interval", return_value=cancel_cb),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        cancel_cb.assert_not_called()  # Setup does NOT call cancel
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
    cancel_cb.assert_called_once()
