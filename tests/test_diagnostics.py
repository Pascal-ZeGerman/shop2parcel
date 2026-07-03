"""Tests for the Shop2Parcel HA diagnostics platform."""

from __future__ import annotations

import json

import pytest

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import setup_coordinator_with_data, setup_imap_coordinator_with_data


def _make_shipment(msg_id: str, email_date: int = 1700000000) -> ShipmentData:
    """Create a minimal ShipmentData for testing."""
    return ShipmentData(
        tracking_number=f"TRK{msg_id}",
        carrier_name="UPS",
        order_name=f"#100{msg_id}",
        message_id=msg_id,
        email_date=email_date,
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
    await setup_imap_coordinator_with_data(hass, mock_imap_config_entry, {})
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
    await setup_imap_coordinator_with_data(hass, mock_imap_config_entry, {})
    result = await async_get_config_entry_diagnostics(hass, mock_imap_config_entry)
    assert result["config"]["connection_type"] == "imap"
    assert result["config"]["account"] == "user@example.com"


@pytest.mark.asyncio
async def test_diagnostics_recent_shipments_capped(hass, mock_config_entry):
    """When coordinator.data has 15 entries, recent_shipments is capped at 10 most recent."""
    data = {str(i): _make_shipment(str(i), email_date=1_700_000_000 + i) for i in range(15)}
    await setup_coordinator_with_data(hass, mock_config_entry, data)
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert len(result["recent_shipments"]) == 10
    # W13/P12-WR-03: assert ordering — 10 most recent by email_date (descending)
    returned_ids = [s["message_id"] for s in result["recent_shipments"]]
    assert returned_ids == [str(i) for i in range(14, 4, -1)]


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
async def test_diagnostics_corrupt_shipment_value_skipped_not_crash(hass, mock_config_entry):
    """IN-09: a non-dataclass value in coordinator.data is skipped, not a crash.

    The old code sorted first (key=lambda s: s.email_date) so a corrupt value
    raised AttributeError before the non-dataclass guard could run — the
    diagnostics download 500'd instead of degrading gracefully.
    """
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    coordinator.data = {
        "good1": _make_shipment("good1", email_date=1_700_000_002),
        "corrupt": "not-a-shipment-dataclass",
        "good2": _make_shipment("good2", email_date=1_700_000_001),
    }
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    returned_ids = [s["message_id"] for s in result["recent_shipments"]]
    assert returned_ids == ["good1", "good2"], (
        "Corrupt value must be skipped; valid shipments must survive, sorted by email_date"
    )


@pytest.mark.asyncio
async def test_diagnostics_non_numeric_email_date_does_not_crash_sort(hass, mock_config_entry):
    """IN-09: a shipment with a non-numeric email_date must not crash the sort."""
    good = _make_shipment("good", email_date=1_700_000_000)
    weird = _make_shipment("weird", email_date=1_700_000_005)
    # ShipmentData is frozen — object.__setattr__ bypasses the frozen guard to
    # simulate on-disk corruption.
    object.__setattr__(weird, "email_date", "corrupt-string")
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    coordinator.data = {"good": good, "weird": weird}
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    returned_ids = {s["message_id"] for s in result["recent_shipments"]}
    assert returned_ids == {"good", "weird"}, "Both shipments must survive a corrupt email_date"


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


@pytest.mark.asyncio
async def test_diagnostics_non_dataclass_diagnostics_returns_empty(hass, mock_config_entry):
    """C1/P12-CR-01: non-dataclass coordinator._diagnostics must not crash and returns empty stats."""
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    coordinator._diagnostics = object()  # not a dataclass — triggers the guard branch
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    # Must not raise AttributeError — the guard must swallow gracefully
    assert isinstance(result, dict)
    assert result["poll_stats"] == {}
    assert result["activity_log"] == []


@pytest.mark.asyncio
async def test_diagnostics_activity_log_contains_imap_events(hass, mock_config_entry):
    """activity_log contains IMAP-prefixed scan events — message_id prefix is an observable invariant."""
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, {})
    coordinator._diagnostics.scan_events.append(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "message_id": "imap:uid123",
            "subject": "Your order shipped",
            "sender": "noreply@shopify.com",
            "strategy": "html_template",
            "tracking_number": "TRK001",
            "outcome": "posted",
        }
    )
    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert len(result["activity_log"]) == 1
    assert result["activity_log"][0]["message_id"] == "imap:uid123"
    assert result["activity_log"][0]["outcome"] == "posted"


@pytest.mark.asyncio
async def test_diagnostics_full_payload_json_safe(hass, mock_config_entry):
    """W14/P12-WR-04: the full diagnostics payload is JSON-serialisable (json.dumps does not raise).

    Seeds both coordinator.data with a shipment AND a scan_event into the scan_events ring
    buffer so all top-level sections (config, poll_stats, activity_log, recent_shipments)
    are non-empty.  Validates the complete payload, not just poll_stats["scan_events"].
    """
    coordinator = await setup_coordinator_with_data(
        hass, mock_config_entry, {"msg1": _make_shipment("msg1")}
    )
    coordinator._diagnostics.scan_events.append(
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "message_id": "gmail:msg1",
            "subject": "Your package has shipped",
            "sender": "noreply@shopify.com",
            "strategy": "html_template",
            "tracking_number": "TRKmsg1",
            "outcome": "posted",
        }
    )
    coordinator._diagnostics.scan_events_total = 1

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # All four top-level sections must be present and non-empty
    assert "config" in result
    assert "poll_stats" in result
    assert len(result["activity_log"]) == 1
    assert len(result["recent_shipments"]) == 1

    # The entire payload must be JSON-serialisable (no deque, no non-JSON types)
    serialized = json.dumps(result)
    assert len(serialized) > 0
