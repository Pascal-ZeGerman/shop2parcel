"""Tests for Shop2Parcel diagnostic_sensor.py — Phase 7 DIAG-08, DIAG-09, DIAG-10.

These tests assume Plan 03 has landed:
- custom_components/shop2parcel/diagnostic_sensor.py exists with 4 sensor classes.
- Diagnostic sensors are registered via sensor.py::async_setup_entry (not via a
  "diagnostic_sensor" platform — HA only supports built-in platform domains).
- coordinator._diagnostics is a PollStats instance (Plan 02).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.const import DOMAIN


def _make_shipment(message_id: str = "msg1") -> ShipmentData:
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1700000000,
    )


def _make_parse_result(shipment: ShipmentData) -> ParseResult:
    return ParseResult(
        shipment=shipment,
        skip_reason=None,
        strategy_used="html_template",
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


async def _setup_integration(hass, mock_config_entry, *, with_message: bool = False):
    """Set up the integration with mocked Gmail/parcelapp/parser/Store/oauth.

    When with_message=True, Gmail returns one message that produces a shipment so
    coordinator._diagnostics accumulates non-zero values for state/attribute tests.
    """
    mock_config_entry.add_to_hass(hass)
    gmail_messages = [{"id": "msg1"}] if with_message else []
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>" if with_message else "",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(
            return_value=MagicMock()
        )
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=gmail_messages
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("msg1")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        await hass.async_block_till_done()
        return coordinator


async def test_emails_scanned_sensor_registered(hass, mock_config_entry):
    """DIAG-08 / DIAG-09: sensor.shop2parcel_emails_scanned registered at setup; state=0.

    Pitfall 5: native_value is int 0 before any poll runs — never None.
    """
    await _setup_integration(hass, mock_config_entry)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_emails_scanned"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, "emails_scanned diagnostic sensor not registered"
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "0"


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
    found = {
        e.unique_id.removeprefix(prefix)
        for e in entries
        if e.unique_id.startswith(prefix)
    }
    missing = expected_suffixes - found
    assert not missing, f"missing diagnostic sensors: {missing}"


async def test_diagnostic_sensors_share_device(hass, mock_config_entry):
    """DIAG-10: all 4 diagnostic sensors share the same Shop2Parcel device."""
    await _setup_integration(hass, mock_config_entry)
    device_reg = dr.async_get(hass)
    devices = [
        d
        for d in device_reg.devices.values()
        if (DOMAIN, mock_config_entry.entry_id) in d.identifiers
    ]
    assert len(devices) == 1, f"expected exactly 1 device, got {len(devices)}"


async def test_emails_scanned_state_after_poll(hass, mock_config_entry):
    """DIAG-09: sensor state == coordinator._diagnostics.emails_scanned_total after a poll.

    Setup runs one full poll cycle with a matched shipment, so:
    - emails_scanned_total == 1
    - sensor.shop2parcel_emails_scanned.state == "1"
    - extra_state_attributes contains last_poll_count, last_poll_time, query_used,
      poll_duration_ms (per CONTEXT.md D-12).
    """
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    # async_setup runs async_config_entry_first_refresh which triggers _async_update_data.
    assert coordinator._diagnostics.emails_scanned_total == 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_emails_scanned"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "1"
    # D-12 attributes
    assert "last_poll_count" in state.attributes
    assert "last_poll_time" in state.attributes
    assert "query_used" in state.attributes
    assert "poll_duration_ms" in state.attributes
    assert state.attributes["last_poll_count"] == 1


async def test_tracking_numbers_found_attributes_after_poll(hass, mock_config_entry):
    """DIAG-09: tracking_numbers_found state and last_poll_found attribute after a poll."""
    coordinator = await _setup_integration(hass, mock_config_entry, with_message=True)
    assert coordinator._diagnostics.tracking_numbers_found_total == 1
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_tracking_numbers_found"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "1"
    assert "last_poll_found" in state.attributes
    last_poll_found = state.attributes["last_poll_found"]
    assert isinstance(last_poll_found, list)
    assert len(last_poll_found) == 1
    assert last_poll_found[0]["message_id"] == "msg1"
    assert last_poll_found[0]["tracking_number"] == "1Z999AA10123456784"


async def test_keyword_hits_per_keyword_attribute(hass, mock_config_entry):
    """DIAG-09: keyword_hits sensor exposes per_keyword dict with all 3 keys."""
    await _setup_integration(hass, mock_config_entry, with_message=True)
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_keyword_hits"
    entry = next(e for e in entries if e.unique_id == uid)
    state = hass.states.get(entry.entity_id)
    assert state is not None
    # HTML strategy success -> all keyword_hits False -> keyword_hits_total stays at 0
    assert state.state == "0"
    assert "per_keyword" in state.attributes
    per_keyword = state.attributes["per_keyword"]
    assert set(per_keyword.keys()) == {"tracking_regex", "order_regex", "carrier_regex"}
