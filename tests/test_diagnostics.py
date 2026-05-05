"""Tests for the Shop2Parcel HA diagnostics platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import setup_coordinator_with_data


def _make_shipment(msg_id: str) -> ShipmentData:
    """Create a minimal ShipmentData for testing."""
    return ShipmentData(
        tracking_number=f"TRK{msg_id}",
        carrier_name="UPS",
        order_name=f"#100{msg_id}",
        message_id=msg_id,
        email_date=1700000000,
    )


@pytest.mark.asyncio
async def test_diagnostics_output_shape(hass, mock_config_entry):
    """Returned dict has exactly the top-level keys config, poll_stats, recent_shipments."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert set(result.keys()) == {"config", "poll_stats", "recent_shipments"}


@pytest.mark.asyncio
async def test_diagnostics_config_redaction(hass, mock_config_entry):
    """Gmail credentials must not appear anywhere in the diagnostic output."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    # Check against the fixture's actual credential values so the test stays correct
    # if the fixture values change.
    for secret in (
        mock_config_entry.data.get("api_key", ""),
        mock_config_entry.data.get("token", {}).get("access_token", ""),
        mock_config_entry.data.get("token", {}).get("refresh_token", ""),
    ):
        assert secret not in str(result), f"Secret value leaked into diagnostics: {secret!r}"


@pytest.mark.asyncio
async def test_diagnostics_imap_redaction(hass, mock_imap_config_entry):
    """IMAP credentials (imap_password, api_key) must not appear in diagnostic output."""
    mock_imap_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([], None))
        await hass.config_entries.async_setup(mock_imap_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data({})
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_imap_config_entry)
    for secret in (
        mock_imap_config_entry.data.get("imap_password", ""),
        mock_imap_config_entry.data.get("api_key", ""),
    ):
        assert secret not in str(result), f"Secret value leaked into diagnostics: {secret!r}"


@pytest.mark.asyncio
async def test_diagnostics_config_gmail(hass, mock_config_entry):
    """Gmail entries report connection_type='gmail' and account=entry.unique_id."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result["config"]["connection_type"] == "gmail"
    assert result["config"]["account"] == mock_config_entry.unique_id


@pytest.mark.asyncio
async def test_diagnostics_config_imap(hass, mock_imap_config_entry):
    """IMAP entries report connection_type='imap' and account=imap_username."""
    mock_imap_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([], None))
        await hass.config_entries.async_setup(mock_imap_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data({})
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_imap_config_entry)
    assert result["config"]["connection_type"] == "imap"
    assert result["config"]["account"] == "user@example.com"


@pytest.mark.asyncio
async def test_diagnostics_recent_shipments_capped(hass, mock_config_entry):
    """When coordinator.data has 15 entries, recent_shipments is capped at 10."""
    data = {str(i): _make_shipment(str(i)) for i in range(15)}
    await setup_coordinator_with_data(hass, mock_config_entry, data)
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert len(result["recent_shipments"]) == 10


@pytest.mark.asyncio
async def test_diagnostics_recent_shipments_empty(hass, mock_config_entry):
    """When coordinator.data is empty, recent_shipments is an empty list."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result["recent_shipments"] == []


@pytest.mark.asyncio
async def test_diagnostics_none_coordinator_data(hass, mock_config_entry):
    """coordinator.data=None (pre-first-poll state) returns empty recent_shipments without crashing."""
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    coordinator.data = None
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert result["recent_shipments"] == []


@pytest.mark.asyncio
async def test_diagnostics_poll_stats_present(hass, mock_config_entry):
    """poll_stats includes the emails_scanned_total counter from PollStats."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert "emails_scanned_total" in result["poll_stats"]
