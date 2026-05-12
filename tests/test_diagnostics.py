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
    """Returned dict has exactly the top-level keys config, poll_stats, activity_log, recent_shipments."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert set(result.keys()) == {"config", "poll_stats", "activity_log", "recent_shipments"}


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
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
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


@pytest.mark.asyncio
async def test_diagnostics_activity_log_key(hass, mock_config_entry):
    """activity_log top-level key is a list (may be empty)."""
    await setup_coordinator_with_data(hass, mock_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert "activity_log" in result
    assert isinstance(result["activity_log"], list)


@pytest.mark.asyncio
async def test_diagnostics_poll_stats_scan_events_json_safe(hass, mock_config_entry):
    """poll_stats["scan_events"] is a list, not a deque — json.dumps does not raise TypeError."""
    import json

    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    # Pre-populate scan_events with a sample event to ensure the field is non-empty
    coordinator._diagnostics.scan_events.append(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "message_id": "gmail:test123",
            "subject": "Your order has shipped",
            "sender": "noreply@shopify.com",
            "strategy": "html_template",
            "tracking_number": "1Z999AA10123456784",
            "outcome": "posted",
        }
    )
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    # This must not raise TypeError: "Object of type deque is not JSON serializable"
    serialized = json.dumps(result["poll_stats"])
    assert '"scan_events"' in serialized
    # scan_events in poll_stats must be a list (not a deque)
    assert isinstance(result["poll_stats"]["scan_events"], list)


@pytest.mark.asyncio
async def test_diagnostics_activity_log_contains_events(hass, mock_config_entry):
    """activity_log contains the scan_events from the coordinator as a list of dicts."""
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    event = {
        "timestamp": "2026-01-01T00:00:00Z",
        "message_id": "gmail:abc123",
        "subject": "Your order shipped",
        "sender": "noreply@shopify.com",
        "strategy": "html_template",
        "tracking_number": "TRK001",
        "outcome": "posted",
    }
    coordinator._diagnostics.scan_events.append(event)
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert len(result["activity_log"]) == 1
    assert result["activity_log"][0]["message_id"] == "gmail:abc123"
    assert result["activity_log"][0]["outcome"] == "posted"
