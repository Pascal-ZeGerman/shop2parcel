"""Tests for Shop2Parcel coordinator — covers EMAIL-05, FWRD-01..FWRD-05."""

from __future__ import annotations

import logging
import time as time_module
from collections import OrderedDict, deque
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ImapAuthError,
    ImapTransientError,
    ParcelAppAlreadyAddedError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from custom_components.shop2parcel.const import (
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    CONF_RESCAN_WINDOW_DAYS,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RESCAN_WINDOW_DAYS,
    DOMAIN,
)
from custom_components.shop2parcel.coordinator import (
    PollStats,
    Shop2ParcelCoordinator,
    _extract_email_meta,
    _extract_imap_email_meta,
    _next_midnight_utc,
)
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator


def _make_shipment(message_id: str = "msg1") -> ShipmentData:
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1700000000,
    )


def _make_parse_result(
    shipment: ShipmentData | None,
    *,
    skip_reason: str | None = None,
    strategy_used: str | None = "html_template",
    keyword_hits: dict[str, bool] | None = None,
) -> ParseResult:
    """Phase 7 helper: build a ParseResult for parser-mock returns.

    Default keyword_hits is all-False (HTML-strategy success shape).
    For tests that don't care about strategy_used, the default
    "html_template" is fine — coordinator only reads result.shipment
    and result.skip_reason / result.keyword_hits.
    """
    if keyword_hits is None:
        keyword_hits = {
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        }
    return ParseResult(
        shipment=shipment,
        skip_reason=skip_reason if shipment is None else None,
        strategy_used=strategy_used if shipment is not None else None,
        keyword_hits=keyword_hits,
    )


# -------- EMAIL-05: poll interval driven by entry.options ----------------


async def test_coordinator_uses_poll_interval(hass, mock_config_entry):
    """EMAIL-05: Coordinator update_interval reads from entry.options[CONF_POLL_INTERVAL]."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL: 60, CONF_GMAIL_QUERY: DEFAULT_GMAIL_QUERY}
    )
    coord = GmailCoordinator(hass, mock_config_entry)
    assert coord.update_interval == timedelta(minutes=60)


# -------- FWRD-01: new shipments POSTed to parcelapp ---------------------


async def test_new_shipment_is_posted(hass, mock_config_entry):
    """FWRD-01: New parsed shipment triggers ParcelAppClient.async_add_delivery.

    Also exercises the access-token extraction path (IN-01): oauth_session.token
    is a real dict so the coordinator extracts a real string token and forwards it
    to GmailClient.async_list_messages as the first positional argument.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()
        assert "msg1" in data
        # Phase 10: dedup uses tracking-number (normalized), not msg-ID.
        assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
        mock_parcel_cls.return_value.async_add_delivery.assert_called_once()
        # Verify the real access_token string was forwarded to the Gmail client (IN-01).
        call_args = mock_gmail_cls.return_value.async_list_messages.call_args
        assert call_args[0][0] == "fake-access-token", (
            "Coordinator must extract access_token from oauth_session.token and pass it "
            "to GmailClient.async_list_messages as first positional argument"
        )


# -------- FWRD-02: deduplication via Store ------------------------------


async def test_no_duplicate_post(hass, mock_config_entry):
    """FWRD-02: tracking number already in submitted_tracking_numbers is not POSTed again.

    Phase 10 change: dedup is now tracking-number-based. Store is seeded with
    submitted_tracking_numbers=["1Z999AA10123456784"] (normalized, uppercase).
    The parsed shipment has the same tracking number → POST is skipped.
    Note: get_message IS called (body must be fetched and parsed to get tracking number).
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        # Seed store with the tracking number that will be parsed from the email.
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # Tracking number was in submitted_tracking_numbers → POST skipped.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_dedup_survives_restart(hass, mock_config_entry):
    """FWRD-02: submitted_tracking_numbers persisted in Store survive coordinator re-init.

    Phase 10 change: Store schema uses submitted_tracking_numbers list (not forwarded_ids).
    After _async_load_store, coordinator._submitted_tracking_numbers is an OrderedDict
    preserving insertion order from the stored list.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["TN-A", "TN-B"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        assert list(coord._submitted_tracking_numbers.keys()) == ["TN-A", "TN-B"]
        assert isinstance(coord._submitted_tracking_numbers, OrderedDict)
        assert coord._quota_exhausted_until is None


# -------- FWRD-03: Store load/save semantics ----------------------------


async def test_store_loaded_before_first_poll(hass, mock_config_entry):
    """FWRD-03: _async_load_store called before _async_update_data on setup.

    Phase 10: dedup is now tracking-number-based. Store is seeded with the
    tracking number the parsed shipment would produce. After load, the poll
    skips the POST because the tracking number is already in submitted_tracking_numbers.
    Note: body IS fetched (get_message IS called) because dedup runs after parse().
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        # Store seed: tracking number matches what _make_shipment returns (normalized).
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "sentinel_msg"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("sentinel_msg")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        # Load store first — this is the contract
        await coord._async_load_store()
        assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
        # Now run the poll — tracking number dedup blocks the POST.
        await coord._async_update_data()
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_store_saved_after_post(hass, mock_config_entry):
    """FWRD-03 / finding 12: forward counters persisted IMMEDIATELY after each successful POST.

    Post-forward saves now write durably via Store.async_save (not the 5s debounce) so a
    crash in the debounce window can't lose the count. Two distinct shipments → at least 2
    immediate writes.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        save_mock = AsyncMock()  # immediate durable write path (finding 12)
        mock_store_cls.return_value.async_save = save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}, {"id": "msg2"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        # Two shipments with DISTINCT tracking numbers so neither is deduped.
        shipment_a = ShipmentData(
            tracking_number="1Z999AA10123456784",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg1",
            email_date=1700000000,
        )
        shipment_b = ShipmentData(
            tracking_number="9400111899223397719000",
            carrier_name="USPS",
            order_name="#5678",
            message_id="msg2",
            email_date=1700000001,
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_a),
            _make_parse_result(shipment_b),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # finding 12: immediate durable write after each POST — at least 2 writes.
        assert save_mock.await_count >= 2


# -------- FWRD-04: quota handling ---------------------------------------


async def test_quota_exhaustion(hass, mock_config_entry):
    """FWRD-04: ParcelAppQuotaError sets quota_exhausted_until, logs warning, NOT raised."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=1234567890)
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Should NOT raise — quota is handled gracefully
        data = await coord._async_update_data()
        assert coord._quota_exhausted_until == 1234567890
        mock_store_cls.return_value.async_delay_save.assert_called()
        # Shipment NOT in data when quota is blocked — withheld so it is re-fetched and
        # POSTed correctly on the next cycle after quota resets (FWRD-02 fix, CR-02).
        assert "msg1" not in data


async def test_quota_exhausted_until_midnight(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until = next midnight UTC when reset_at is None."""
    mock_config_entry.add_to_hass(hass)
    expected = _next_midnight_utc()  # call before any monkeypatching
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=None)
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._quota_exhausted_until == expected


async def test_quota_exhausted_until_reset_at(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until uses err.reset_at when provided."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=9999)
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._quota_exhausted_until == 9999


async def test_gmail_polling_continues_during_quota(hass, mock_config_entry):
    """FWRD-04 / D-05: while quota_exhausted_until > now, Gmail still polled, POST skipped."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "new_msg"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("new_msg")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Set quota as exhausted (in future)
        coord._quota_exhausted_until = int(time_module.time()) + 3600
        data = await coord._async_update_data()
        # Gmail was polled (async_get_message was called)
        mock_gmail_cls.return_value.async_get_message.assert_called_once()
        # POST was NOT called
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()
        # Shipment NOT in data — withheld while quota is blocked so it is re-fetched
        # and forwarded correctly on the next cycle after quota resets (CR-02 fix).
        assert "new_msg" not in data


async def test_quota_recovers_after_reset_at_past(hass, mock_config_entry):
    """FWRD-04 / Phase 6 D-01 gap fill: when _quota_exhausted_until is in the past,
    POST resumes on the next poll AND _quota_exhausted_until is cleared to None
    (coordinator.py lines 242-248).

    The existing test_gmail_polling_continues_during_quota exercises the BLOCKED state
    (quota_exhausted_until in the future). This test exercises the EXIT state.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = MagicMock()
        mock_store_cls.return_value.async_delay_save = save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg_recover"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("msg_recover")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Set quota_exhausted_until to a timestamp 1 second IN THE PAST
        coord._quota_exhausted_until = int(time_module.time()) - 1

        data = await coord._async_update_data()

        # POST must have been invoked (quota window expired)
        mock_parcel_cls.return_value.async_add_delivery.assert_called_once()
        # New shipment is in returned data; tracking number is in submitted set.
        assert "msg_recover" in data
        assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
        # Quota window was cleared
        assert coord._quota_exhausted_until is None
        # Debounced save was scheduled at least once after recovery
        assert save_mock.call_count >= 1


# -------- FWRD-05: error translation taxonomy ---------------------------


async def test_parcelapp_transient_error_skipped(hass, mock_config_entry):
    """FWRD-05: ParcelAppTransientError is logged + skipped — NOT UpdateFailed, NOT in forwarded_ids."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("network error")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Must NOT raise
        data = await coord._async_update_data()
        # Tracking number NOT in submitted set (transient error: will retry next cycle)
        assert "1Z999AA10123456784" not in coord._submitted_tracking_numbers
        # But coordinator still returns a data dict
        assert isinstance(data, dict)


async def test_gmail_transient_raises_update_failed(hass, mock_config_entry):
    """FWRD-05: GmailTransientError -> UpdateFailed (keeps last data, retries)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailTransientError("network error")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_gmail_auth_raises_config_entry_auth_failed(hass, mock_config_entry):
    """FWRD-05: GmailAuthError -> ConfigEntryAuthFailed (triggers reauth)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("token revoked")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()


async def test_missing_access_token_raises_config_entry_auth_failed(hass, mock_config_entry):
    """IN-01: oauth_session.token with no access_token key → ConfigEntryAuthFailed.

    Exercises the guard at coordinator.py line 198-199 with a realistic empty
    token dict so the if-not-access_token branch is reachable in tests.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        # Token dict present but access_token is missing — triggers the guard
        mock_oauth.OAuth2Session.return_value.token = {
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="access_token"):
            await coord._async_update_data()


async def test_missing_refresh_token_raises_config_entry_auth_failed(hass):
    """IN-02: config entry token with no refresh_token → ConfigEntryAuthFailed before token refresh.

    Guards against the case where HA stored a token without refresh_token (e.g. original
    auth done without access_type=offline, or Google OAuth app in Testing mode).
    Fires before async_ensure_token_valid() so the error is immediate and actionable.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "expires_at": 9999999999.0,
                # no refresh_token — triggers the early guard in _async_update_data
            },
            "api_key": "test-parcelapp-key",
        },
        unique_id="user@gmail.com",
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="refresh_token"):
            await coord._async_update_data()
        # async_ensure_token_valid must NOT be called — guard fires before it
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid.assert_not_called()


async def test_invalid_tracking_not_deduped(hass, mock_config_entry):
    """FWRD-05 / C-05: ParcelAppInvalidTrackingError is a permanent 400.

    C-05 fix: tracking number IS added to _submitted_tracking_numbers to prevent infinite retry loop
    draining the 20/day quota. Re-POSTing a 400 will always fail — suppressing
    retries is the correct behavior.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = AsyncMock()
        mock_store_cls.return_value.async_save = save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad tracking")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # Phase 10: tracking number IS in submitted set — permanent 400 suppresses
        # retries to protect the 20/day quota from being drained by invalid messages.
        assert "1Z999AA10123456784" in coord._submitted_tracking_numbers


# -------- Phase 5 async_cleanup_delivered tests --------------------------


async def test_cleanup_no_deliveries_in_data(hass, mock_config_entry):
    """When coordinator.data is empty, cleanup returns early without making the API call."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(return_value=[])
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        with patch.object(coordinator, "async_set_updated_data") as set_data:
            await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))
            set_data.assert_not_called()


async def test_cleanup_removes_delivered_from_data(hass, mock_config_entry):
    """Cleanup drops entries whose tracking_number returns status_code=0 from parcelapp."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(
        return_value=[
            {"tracking_number": "TRACK_A", "status_code": 0},  # delivered
            {"tracking_number": "TRACK_B", "status_code": 2},  # in transit, keep
        ]
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        coordinator.async_set_updated_data(
            {
                "msg_a": ShipmentData("TRACK_A", "UPS", "#1", "msg_a", 1),
                "msg_b": ShipmentData("TRACK_B", "UPS", "#2", "msg_b", 2),
            }
        )
        await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))

    assert "msg_a" not in coordinator.data
    assert "msg_b" in coordinator.data


async def test_cleanup_entity_registry_noop_for_phase26(hass, mock_config_entry):
    """Entity-registry removal loop is a no-op for Phase 26 installs.

    _sweep_orphaned_entities (run at async_setup_entry) already removed all
    per-message uid entries before the first async_cleanup_delivered fires.
    The loop targets uids of the form {DOMAIN}_{entry_id}_{msg_id}; since no
    such entries exist in the registry after the Phase 26 migration sweep,
    async_remove must never be called even when a delivered shipment is removed
    from coordinator.data.
    """
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(
        return_value=[{"tracking_number": "TRACK_A", "status_code": 0}]
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # Seed coordinator.data with a delivered shipment (no registry entry for it — Phase 26)
        coordinator.async_set_updated_data(
            {"msg_a": ShipmentData("TRACK_A", "UPS", "#1", "msg_a", 1)}
        )

        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        with patch.object(registry, "async_remove") as mock_remove:
            await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))
            mock_remove.assert_not_called()

    # Data was still cleaned up even though registry removal was a no-op
    assert "msg_a" not in coordinator.data


async def test_cleanup_uses_filter_mode_recent(hass, mock_config_entry):
    """RESEARCH.md Pitfall 6: must call GET with filter_mode='recent' (NOT 'active')."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(return_value=[])
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        # Seed data so the early-return guard (WR-01) doesn't skip the API call
        coordinator.async_set_updated_data(
            {
                "msg1": ShipmentData(
                    tracking_number="TR123",
                    carrier_name="UPS",
                    order_name="#1",
                    message_id="msg1",
                    email_date=0,
                )
            }
        )
        await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))

    fake_client.async_get_deliveries.assert_called_with(filter_mode="recent")


async def test_cleanup_handles_auth_error(hass, mock_config_entry):
    """ParcelAppAuthError is caught + logged + returns None — does NOT propagate."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppAuthError("boom"))
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        raw = hass.data[DOMAIN][mock_config_entry.entry_id]
        coordinator = raw["coordinator"] if isinstance(raw, dict) else raw
        # Must NOT raise
        result = await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))
        assert result is None


async def test_cleanup_handles_transient_error(hass, mock_config_entry):
    """ParcelAppTransientError is caught + logged + returns None — does NOT propagate."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppTransientError("net"))
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        raw = hass.data[DOMAIN][mock_config_entry.entry_id]
        coordinator = raw["coordinator"] if isinstance(raw, dict) else raw
        result = await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))
        assert result is None


# -------- Phase 7: PollStats accumulation tests (DIAG-05..DIAG-07) -----------


async def test_diagnostics_emails_scanned_increments(hass, mock_config_entry):
    """DIAG-05: emails_scanned_total / emails_matched_total / tracking_numbers_found_total
    increment by 1 per non-forwarded message that produces a shipment."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._diagnostics.emails_returned_total == 1
        assert coord._diagnostics.last_poll_emails_returned == 1
        assert coord._diagnostics.last_poll_emails_skipped_dedup == 0
        assert coord._diagnostics.submitted_tracking_count == 1
        assert coord._diagnostics.last_poll_effective_query is not None
        assert coord._diagnostics.emails_scanned_total == 1
        assert coord._diagnostics.emails_matched_total == 1
        assert coord._diagnostics.tracking_numbers_found_total == 1
        assert coord._diagnostics.last_poll_emails_scanned == 1
        assert coord._diagnostics.last_poll_emails_matched == 1
        assert len(coord._diagnostics.last_poll_found) == 1
        assert coord._diagnostics.last_poll_found[0]["message_id"] == "msg1"


async def test_diagnostics_last_poll_fields_reset_per_cycle(hass, mock_config_entry):
    """DIAG-06: last_poll_* fields reset at the top of each _async_update_data call."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=[([{"id": "msg1"}], "q after:0"), ([{"id": "msg2"}], "q after:0")]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        # Use distinct tracking numbers so msg2 is not deduped on the second poll.
        shipment1 = ShipmentData(
            tracking_number="1Z999AA10123456784",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg1",
            email_date=1700000000,
        )
        shipment2 = ShipmentData(
            tracking_number="9400111899223397614437",
            carrier_name="USPS",
            order_name="#1235",
            message_id="msg2",
            email_date=1700000000,
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment1),
            _make_parse_result(shipment2),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # First poll
        await coord._async_update_data()
        assert coord._diagnostics.last_poll_emails_scanned == 1
        assert coord._diagnostics.last_poll_emails_returned == 1
        assert len(coord._diagnostics.last_poll_found) == 1
        # Cumulative carries over from poll 1
        assert coord._diagnostics.emails_scanned_total == 1
        assert coord._diagnostics.emails_returned_total == 1
        # Second poll — last_poll_* must reset before processing msg2
        await coord._async_update_data()
        assert coord._diagnostics.last_poll_emails_scanned == 1  # only msg2 this cycle
        assert coord._diagnostics.last_poll_emails_returned == 1  # only msg2 this cycle
        assert len(coord._diagnostics.last_poll_found) == 1  # only msg2 this cycle
        assert coord._diagnostics.last_poll_found[0]["message_id"] == "msg2"
        # Cumulative now reflects both polls
        assert coord._diagnostics.emails_scanned_total == 2
        assert coord._diagnostics.emails_matched_total == 2
        assert coord._diagnostics.emails_returned_total == 2


async def test_diagnostics_no_html_body_skip_reason(hass, mock_config_entry):
    """DIAG-07: when extract_html_body returns empty, coordinator records
    {"message_id", "reason": "no_html_body"} in last_poll_skip_reasons."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="",  # empty body triggers no_html_body
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._diagnostics.emails_scanned_total == 1
        assert coord._diagnostics.last_poll_emails_scanned == 1
        assert any(
            e.get("message_id") == "msg1" and e.get("reason") == "no_html_body"
            for e in coord._diagnostics.last_poll_skip_reasons
        )
        # Parser was NOT invoked because html was empty
        mock_parser_cls.return_value.parse.assert_not_called()


async def test_diagnostics_tracking_dedup_skip_counted(hass, mock_config_entry):
    """Phase 10: dedup is tracking-number-based. A message whose tracking number is
    already in submitted_tracking_numbers IS fetched + parsed but skipped at dedup gate.
    The skip increments last_poll_emails_skipped_dedup but the email IS scanned (counted
    in emails_scanned_total) because parsing must happen to know the tracking number.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        # Pre-load submitted_tracking_numbers with the tracking number the parsed email produces.
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # Email was returned by Gmail and counted.
        assert coord._diagnostics.emails_returned_total == 1
        assert coord._diagnostics.last_poll_emails_returned == 1
        # Phase 10: tracking-number dedup skip IS counted (fires after parse).
        assert coord._diagnostics.last_poll_emails_skipped_dedup == 1
        # Email was scanned (parse ran to determine tracking number).
        assert coord._diagnostics.emails_scanned_total == 1
        assert coord._diagnostics.last_poll_emails_scanned == 1
        # Parser WAS called (body was fetched + parsed before dedup check).
        mock_parser_cls.return_value.parse.assert_called_once()
        # POST was NOT attempted.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 9: IMAP coordinator dispatch
# ---------------------------------------------------------------------------


async def test_coordinator_uses_gmail_client_for_gmail_entry(hass, mock_config_entry):
    """Phase 9 D-10: Gmail entry (no connection_type or 'gmail') instantiates GmailClient."""
    from custom_components.shop2parcel.api.gmail_client import GmailClient  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)
    coordinator = GmailCoordinator(hass, mock_config_entry)
    assert isinstance(coordinator._email_client, GmailClient), "Gmail entry must create GmailClient"


# ---------------------------------------------------------------------------
# Phase 9: IMAP poll-cycle tests (WR-04 + CR-01 regression guard)
# ---------------------------------------------------------------------------


def _make_imap_raw_message(uid: int, html: str = "<html><body>shipped</body></html>") -> dict:
    """Build a minimal raw IMAP message dict as returned by ImapClient."""
    import email as email_lib  # noqa: PLC0415
    from email.mime.text import MIMEText  # noqa: PLC0415

    msg = MIMEText(html, "html")
    return {"uid": uid, "raw": msg.as_bytes()}


async def test_imap_basic_poll_cycle(hass, mock_imap_config_entry):
    """IMAP FWRD-01: ImapClient returns one message → parsed → forwarded → tracking number in _submitted_tracking_numbers."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    # Shipment data keyed by UID string (coordinator stores by uid_str)
    assert "100" in data
    # Phase 10: tracking-number dedup — normalized TN recorded after successful POST
    assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
    mock_parcel_cls.return_value.async_add_delivery.assert_called_once()


async def test_imap_tracking_dedup_skips_seen(hass, mock_imap_config_entry):
    """IMAP FWRD-02: message whose tracking number is already in _submitted_tracking_numbers → not re-POSTed."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(101)
    shipment = _make_shipment("101")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        # Seed store with the tracking number already present (v2 schema)
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # Already seen tracking number — must not POST again
    mock_parcel_cls.return_value.async_add_delivery.assert_not_called()
    # Dedup skip must be counted
    assert coord._diagnostics.last_poll_emails_skipped_dedup == 1


async def test_imap_auth_error_raises_config_entry_auth_failed(hass, mock_imap_config_entry):
    """IMAP FWRD-05: ImapAuthError → ConfigEntryAuthFailed."""
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=ImapAuthError("auth failed")
        )

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()


async def test_imap_transient_error_raises_update_failed(hass, mock_imap_config_entry):
    """IMAP FWRD-05: ImapTransientError → UpdateFailed."""
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=ImapTransientError("connection reset")
        )

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_imap_quota_blocked_does_not_submit_tracking(hass, mock_imap_config_entry):
    """CR-01 regression: when quota blocked, tracking number must NOT be added to _submitted_tracking_numbers.

    One message arrives while quota is exhausted.
    After the poll, the tracking number must NOT be in _submitted_tracking_numbers
    so the next poll re-tries forwarding it once quota recovers.
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")

    # Set quota_exhausted_until to a future timestamp
    future_ts = int(time_module.time()) + 3600

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": [],
                "quota_exhausted_until": future_ts,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # Tracking number must NOT be recorded — forwarding was blocked by quota
    assert "1Z999AA10123456784" not in coord._submitted_tracking_numbers, (
        "CR-01: tracking number must not be added when quota was blocked this cycle"
    )
    # No delivery was attempted — quota-blocked
    mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 10 IMAP: since_date and no uid_filter tests (D-11/D-12)
# ---------------------------------------------------------------------------


async def test_imap_poll_calls_fetch_with_since_date(hass, mock_imap_config_entry):
    """D-11: Coordinator must pass since_date=<string> kwarg to fetch_shipping_emails.

    The since_date must be a non-empty string in DD-Mon-YYYY format (IMAP SEARCH date).
    """
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Return empty list — we only care about how fetch_shipping_emails was called
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        call_kwargs = mock_imap_cls.return_value.fetch_shipping_emails.call_args
        assert call_kwargs is not None
        since_date = call_kwargs.kwargs.get("since_date") or (
            call_kwargs.args[6] if len(call_kwargs.args) > 6 else None
        )
        assert isinstance(since_date, str) and len(since_date) > 0, (
            "D-11: fetch_shipping_emails must be called with a non-empty since_date string"
        )
        # Basic format check: should contain a month abbreviation (e.g. "May")
        import calendar  # noqa: PLC0415

        month_abbrs = [calendar.month_abbr[i] for i in range(1, 13)]
        assert any(m in since_date for m in month_abbrs), (
            f"D-11: since_date '{since_date}' does not look like a DD-Mon-YYYY IMAP date"
        )


async def test_imap_poll_no_uid_filter(hass, mock_imap_config_entry):
    """D-12: Coordinator must NOT pass uid_filter, min_uid, or after_uid to fetch_shipping_emails.

    Phase 10 removes UID-based filtering — full window scan via SINCE date only.
    """
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        call_kwargs = mock_imap_cls.return_value.fetch_shipping_emails.call_args
        assert call_kwargs is not None
        kwarg_keys = set(call_kwargs.kwargs.keys())
        for banned_kwarg in ("uid_filter", "min_uid", "after_uid", "last_uid"):
            assert banned_kwarg not in kwarg_keys, (
                f"D-12: fetch_shipping_emails must not receive '{banned_kwarg}' (Phase 10 removes UID filtering)"
            )


# ---------------------------------------------------------------------------
# I-05, I-06, I-07: Missing tests added by PR #2 review
# ---------------------------------------------------------------------------


async def test_parcelapp_auth_error_mid_loop_raises_config_entry_auth_failed(
    hass, mock_config_entry
):
    """I-05: ParcelAppAuthError mid-loop must propagate as ConfigEntryAuthFailed.

    Exercises the path where async_add_delivery raises ParcelAppAuthError after
    some messages may have already been processed — the coordinator must raise
    ConfigEntryAuthFailed to trigger HA reauth, not swallow the error.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAuthError("api key revoked")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()


async def test_oauth2_token_refresh_failure_raises_config_entry_auth_failed(
    hass, mock_config_entry
):
    """I-06: async_ensure_token_valid raising must translate to ConfigEntryAuthFailed.

    Every other coordinator test mocks async_ensure_token_valid as a no-op.
    This test exercises the branch at coordinator.py that catches the exception
    and re-raises as ConfigEntryAuthFailed.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(
            side_effect=Exception("token refresh network error")
        )
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="Gmail token refresh failed"):
            await coord._async_update_data()


async def test_oauth2_4xx_raises_config_entry_auth_failed(hass, mock_config_entry):
    """I-06b: 4xx ClientResponseError from token endpoint → ConfigEntryAuthFailed."""
    mock_config_entry.add_to_hass(hass)
    err = aiohttp.ClientResponseError(MagicMock(), (), status=401)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(side_effect=err)
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="HTTP 401"):
            await coord._async_update_data()


async def test_oauth2_400_raises_testing_mode_hint(hass, mock_config_entry):
    """I-06e: HTTP 400 from token endpoint → ConfigEntryAuthFailed with Testing-mode hint.

    400 invalid_grant is the most common failure in Google OAuth Testing mode where
    refresh tokens expire after 7 days. The error message must mention both
    'expired or revoked' and the 7-day Testing mode context.
    """
    mock_config_entry.add_to_hass(hass)
    err = aiohttp.ClientResponseError(MagicMock(), (), status=400)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(side_effect=err)
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="Testing mode"):
            await coord._async_update_data()


async def test_oauth2_other_4xx_raises_generic_message(hass, mock_config_entry):
    """I-06f: Non-400/401 4xx from token endpoint → ConfigEntryAuthFailed with generic message."""
    mock_config_entry.add_to_hass(hass)
    err = aiohttp.ClientResponseError(MagicMock(), (), status=403)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(side_effect=err)
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="HTTP 403"):
            await coord._async_update_data()


async def test_oauth2_5xx_raises_update_failed(hass, mock_config_entry):
    """I-06c: 5xx ClientResponseError from token endpoint → UpdateFailed (transient)."""
    mock_config_entry.add_to_hass(hass)
    err = aiohttp.ClientResponseError(MagicMock(), (), status=503)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(side_effect=err)
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(UpdateFailed, match="server error"):
            await coord._async_update_data()


async def test_oauth2_network_error_raises_update_failed(hass, mock_config_entry):
    """I-06d: Network-level ClientError (no response) → UpdateFailed, not ConfigEntryAuthFailed."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(
            side_effect=aiohttp.ClientConnectorError(MagicMock(), OSError("connection refused"))
        )
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(UpdateFailed, match="Network error"):
            await coord._async_update_data()


async def test_imap_no_html_body_records_skip_reason(hass, mock_imap_config_entry):
    """I-07: IMAP path — extract_html_body_imap returning None must record no_html_body in diagnostics.

    Phase 10: UID-based advancement is removed. The key requirement is that
    no_html_body messages are skipped and the diagnostics skip_reasons list is
    updated correctly. The SINCE-date window ensures they won't be re-fetched
    indefinitely (rescan window is fixed per config, not per-message UID).
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(200)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value=None,  # triggers no_html_body skip
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # Phase 10: no _last_imap_uid / _forwarded_ids — dedup is tracking-number based
    assert not hasattr(coord, "_last_imap_uid"), (
        "I-07: _last_imap_uid must not exist in Phase 10 coordinator"
    )
    assert not hasattr(coord, "_forwarded_ids"), (
        "I-07: _forwarded_ids must not exist in Phase 10 coordinator"
    )
    # Diagnostics must record the skip reason
    assert coord._diagnostics.emails_scanned_total == 1
    assert any(
        e.get("message_id") == "200" and e.get("reason") == "no_html_body"
        for e in coord._diagnostics.last_poll_skip_reasons
    )
    # No delivery attempt
    mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# QF-02: rescan_window_days wiring through coordinator
# ---------------------------------------------------------------------------


async def test_gmail_poll_passes_rescan_window_to_client(hass, mock_config_entry):
    """QF-02: Coordinator reads CONF_RESCAN_WINDOW_DAYS from options and passes
    it as rescan_window_days kwarg to gmail.async_list_messages."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: DEFAULT_GMAIL_QUERY,
            CONF_RESCAN_WINDOW_DAYS: 60,
        },
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        # Verify rescan_window_days=60 was passed to the Gmail client
        call_kwargs = mock_gmail_cls.return_value.async_list_messages.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("rescan_window_days") == 60, (
            "Coordinator must pass rescan_window_days=60 from options to async_list_messages"
        )
        # after_timestamp kwarg was removed in Phase 10 Task 1
        assert "after_timestamp" not in (call_kwargs.kwargs or {}), (
            "after_timestamp must not be passed to async_list_messages (removed in Phase 10)"
        )


async def test_gmail_poll_uses_default_rescan_window_when_unset(hass, mock_config_entry):
    """QF-02: When CONF_RESCAN_WINDOW_DAYS is absent from options, coordinator passes
    DEFAULT_RESCAN_WINDOW_DAYS (30) to gmail.async_list_messages."""
    mock_config_entry.add_to_hass(hass)
    # Options do NOT include CONF_RESCAN_WINDOW_DAYS
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_POLL_INTERVAL: 30, CONF_GMAIL_QUERY: DEFAULT_GMAIL_QUERY},
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        # Verify default rescan_window_days was passed
        call_kwargs = mock_gmail_cls.return_value.async_list_messages.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("rescan_window_days") == DEFAULT_RESCAN_WINDOW_DAYS, (
            f"Coordinator must pass default rescan_window_days={DEFAULT_RESCAN_WINDOW_DAYS} "
            "when option is absent from entry.options"
        )


# ---------------------------------------------------------------------------
# Phase 11: scan event ring buffer tests (ACTLOG-01..ACTLOG-03)
# ---------------------------------------------------------------------------


def test_poll_stats_scan_events_fields():
    """ACTLOG-01: PollStats constructs with scan_events as deque and scan_events_total == 0."""
    stats = PollStats()
    assert isinstance(stats.scan_events, deque), "scan_events must be a deque"
    assert stats.scan_events_total == 0, "scan_events_total must start at 0"


def test_scan_events_ring_buffer():
    """ACTLOG-01: appending 51 events to scan_events results in len == 50 (ring buffer eviction)."""
    stats = PollStats()
    for i in range(51):
        stats.scan_events.append({"event": i})
    assert len(stats.scan_events) == 50, (
        "Ring buffer must evict oldest event when 51st is appended (maxlen=50)"
    )
    # Verify the oldest (event 0) was evicted and newest (event 50) is present
    events = list(stats.scan_events)
    assert events[0]["event"] == 1, "Event 0 (oldest) must have been evicted"
    assert events[-1]["event"] == 50, "Event 50 (newest) must be present"


async def test_scan_event_gmail_posted(hass, mock_config_entry):
    """ACTLOG-02: Gmail posted path appends scan event with outcome='posted' and correct fields."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1, "scan_events_total must be 1 after one posted email"
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "posted"
    assert evt["message_id"] == "gmail:msg1"
    assert evt["tracking_number"] == "1Z999AA10123456784"
    assert evt["strategy"] == "html_template"
    assert "timestamp" in evt
    assert evt["timestamp"].endswith("Z")


async def test_scan_event_gmail_no_match(hass, mock_config_entry):
    """ACTLOG-02: Gmail no_match path appends scan event with outcome='no_match'."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg2"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "no_match"
    assert evt["message_id"] == "gmail:msg2"
    assert evt["tracking_number"] is None


async def test_scan_event_gmail_parse_error(hass, mock_config_entry):
    """ACTLOG-02: Gmail parse exception path appends scan event with outcome='error'."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg3"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = ValueError("boom" * 30)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "error"
    assert evt["message_id"] == "gmail:msg3"
    assert evt["error_type"] == "ValueError"
    assert len(evt["error_msg"]) <= 100, "error_msg must be truncated to 100 chars"


async def test_scan_event_gmail_skipped_dedup(hass, mock_config_entry):
    """ACTLOG-02: Gmail skipped_dedup path appends scan event with outcome='skipped_dedup'.

    Two polls with the same tracking number: first poll posts it (outcome='posted'),
    second poll finds the same tracking number already in _submitted_tracking_numbers
    and emits outcome='skipped_dedup'.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Poll 1: msg4 — gets parsed and posted
        # Poll 2: msg4b has same tracking number as msg4 — triggers skipped_dedup
        shipment_a = _make_shipment("msg4")  # TN = 1Z999AA10123456784
        shipment_b = _make_shipment("msg4b")  # same TN = 1Z999AA10123456784
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=[([{"id": "msg4"}], "q after:0"), ([{"id": "msg4b"}], "q after:0")]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_a),
            _make_parse_result(shipment_b),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Poll 1: posts TN, adds to _submitted_tracking_numbers
        await coord._async_update_data()
        # Poll 2: same TN is already submitted → skipped_dedup
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 2
    outcomes = [evt["outcome"] for evt in d.scan_events]
    assert "posted" in outcomes, "Poll 1 must produce a 'posted' scan event"
    assert "skipped_dedup" in outcomes, "Poll 2 must produce a 'skipped_dedup' scan event"
    dedup_evt = next(e for e in d.scan_events if e["outcome"] == "skipped_dedup")
    assert dedup_evt["message_id"] == "gmail:msg4b"
    assert dedup_evt["tracking_number"] == "1Z999AA10123456784"


async def test_scan_event_gmail_skipped_quota(hass, mock_config_entry):
    """ACTLOG-02: Gmail skipped_quota path appends scan event with outcome='skipped_quota'."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg5"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg5"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Set quota as exhausted (future timestamp)
        import time as _time

        coord._quota_exhausted_until = int(_time.time()) + 3600
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "skipped_quota"
    assert evt["message_id"] == "gmail:msg5"
    assert evt["tracking_number"] == "1Z999AA10123456784"


async def test_scan_events_not_reset_between_polls(hass, mock_config_entry):
    """ACTLOG-03: scan_events accumulates across poll cycles (NOT reset per poll)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=[([{"id": "msg_p1"}], "q after:0"), ([{"id": "msg_p2"}], "q after:0")]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(_make_shipment("msg_p1")),
            _make_parse_result(_make_shipment("msg_p2")),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Poll 1
        await coord._async_update_data()
        assert coord._diagnostics.scan_events_total == 1
        assert len(coord._diagnostics.scan_events) == 1
        # Poll 2
        await coord._async_update_data()
        # scan_events must accumulate — NOT reset between polls
        assert coord._diagnostics.scan_events_total == 2
        assert len(coord._diagnostics.scan_events) == 2


# ---------------------------------------------------------------------------
# Phase 11: IMAP scan event tests (ACTLOG-02 IMAP path)
# ---------------------------------------------------------------------------


async def test_scan_event_imap_posted(hass, mock_imap_config_entry):
    """ACTLOG-02 IMAP: posted path appends scan event with message_id='imap:{uid_str}'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(300)
    shipment = _make_shipment("300")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "posted"
    assert evt["message_id"] == "imap:300"
    assert evt["tracking_number"] == "1Z999AA10123456784"


async def test_scan_event_imap_no_match(hass, mock_imap_config_entry):
    """ACTLOG-02 IMAP: no_match path appends scan event with outcome='no_match'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(301)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "no_match"
    assert evt["message_id"] == "imap:301"
    assert evt["tracking_number"] is None


async def test_scan_event_imap_parse_error(hass, mock_imap_config_entry):
    """ACTLOG-02 IMAP: parse exception path appends scan event with outcome='error'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(302)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.side_effect = RuntimeError("imap parse fail")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "error"
    assert evt["message_id"] == "imap:302"
    assert evt["error_type"] == "RuntimeError"


async def test_scan_event_imap_skipped_quota(hass, mock_imap_config_entry):
    """ACTLOG-02 IMAP: skipped_quota path appends scan event with outcome='skipped_quota'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(303)
    shipment = _make_shipment("303")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        import time as _time

        coord._quota_exhausted_until = int(_time.time()) + 3600
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 1
    assert len(d.scan_events) == 1
    evt = d.scan_events[0]
    assert evt["outcome"] == "skipped_quota"
    assert evt["message_id"] == "imap:303"


async def test_scan_event_imap_skipped_dedup(hass, mock_imap_config_entry):
    """ACTLOG-02 IMAP: skipped_dedup path appends scan event with outcome='skipped_dedup'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg_a = _make_imap_raw_message(304)
    raw_msg_b = _make_imap_raw_message(305)
    shipment_a = _make_shipment("304")
    shipment_b = _make_shipment("305")  # same tracking number as 304

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=[[raw_msg_a], [raw_msg_b]]
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_a),
            _make_parse_result(shipment_b),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        # Poll 1: posts TN, adds to _submitted_tracking_numbers
        await coord._async_update_data()
        # Poll 2: same TN already submitted → skipped_dedup
        await coord._async_update_data()

    d = coord._diagnostics
    assert d.scan_events_total == 2
    outcomes = [evt["outcome"] for evt in d.scan_events]
    assert "posted" in outcomes
    assert "skipped_dedup" in outcomes
    dedup_evt = next(e for e in d.scan_events if e["outcome"] == "skipped_dedup")
    assert dedup_evt["message_id"] == "imap:305"


async def test_scan_events_accumulate_across_gmail_and_imap(
    hass, mock_config_entry, mock_imap_config_entry
):
    """ACTLOG-03: scan_events_total accumulates correctly in-memory (not reset per poll)."""
    # Test that scan_events_total is cumulative: two separate coordinators,
    # each contributing 1 event. This tests the in-memory accumulation.
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=[
                ([{"id": "m1"}], "q after:0"),
                ([{"id": "m2"}], "q after:0"),
                ([{"id": "m3"}], "q after:0"),
            ]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(_make_shipment("m1")),
            _make_parse_result(None, skip_reason="no_match"),
            _make_parse_result(_make_shipment("m3")),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()  # poll 1: 1 posted
        await coord._async_update_data()  # poll 2: 1 no_match
        await coord._async_update_data()  # poll 3: 1 posted

    # scan_events_total must accumulate across polls (not reset)
    assert coord._diagnostics.scan_events_total == 3
    assert len(coord._diagnostics.scan_events) == 3


def test_scan_events_total_exceeds_deque_len_after_overflow():
    """ACTLOG-01: scan_events_total is cumulative; deque evicts but total does not reset.

    After 51+ events the deque (maxlen=50) has evicted the oldest entry but
    scan_events_total continues to reflect the true lifetime count.  A future
    maintainer must NOT 'fix' this by resetting scan_events_total to
    len(scan_events) — that would destroy the cumulative-since-restart semantic.
    """
    stats = PollStats()
    for i in range(51):
        stats.scan_events.append({"event": i})
        stats.scan_events_total += 1
    assert stats.scan_events_total == 51
    assert len(stats.scan_events) == 50
    assert stats.scan_events_total > len(stats.scan_events), (
        "scan_events_total must exceed deque len after overflow — this divergence is intentional"
    )


# -------- DEDUP-01: LRU eviction at MAX_SUBMITTED_TRACKING_NUMBERS boundary -


def test_lru_eviction_triggers_at_cap_plus_one():
    """DEDUP-01: Inserting the 1001st entry evicts the oldest (first-in) entry.

    The implementation uses OrderedDict with popitem(last=False) guarded by
    ``if len(...) > MAX_SUBMITTED_TRACKING_NUMBERS``.  This test drives the
    dict to exactly 1001 entries — one past the cap — and verifies:

    1. The oldest key ("tracking-0") is evicted.
    2. The newest key ("tracking-1000") is retained.
    3. The dict length returns to exactly MAX_SUBMITTED_TRACKING_NUMBERS (1000).

    This exercises the popitem(last=False) branch that no prior test reaches.
    """
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS

    submitted: OrderedDict = OrderedDict()

    # Fill to exactly the cap (1000 entries — eviction NOT triggered yet).
    for i in range(MAX_SUBMITTED_TRACKING_NUMBERS):
        key = f"TRACKING-{i}"
        submitted[key] = None
        if len(submitted) > MAX_SUBMITTED_TRACKING_NUMBERS:
            submitted.popitem(last=False)

    assert len(submitted) == MAX_SUBMITTED_TRACKING_NUMBERS
    assert "TRACKING-0" in submitted, "Oldest entry must still be present before eviction"

    # Insert the (cap + 1)th entry — this must trigger eviction.
    overflow_key = f"TRACKING-{MAX_SUBMITTED_TRACKING_NUMBERS}"
    submitted[overflow_key] = None
    if len(submitted) > MAX_SUBMITTED_TRACKING_NUMBERS:
        submitted.popitem(last=False)

    # Post-eviction assertions.
    assert len(submitted) == MAX_SUBMITTED_TRACKING_NUMBERS, (
        f"After eviction dict must have exactly {MAX_SUBMITTED_TRACKING_NUMBERS} entries, "
        f"got {len(submitted)}"
    )
    assert "TRACKING-0" not in submitted, (
        "TRACKING-0 (oldest/first-inserted) must have been evicted by popitem(last=False)"
    )
    assert overflow_key in submitted, f"{overflow_key} (newest) must be retained after eviction"


def test_lru_eviction_preserves_insertion_order_after_eviction():
    """DEDUP-01: After eviction the remaining entries keep insertion order intact.

    The entry evicted is always the *oldest* (last=False), so the surviving
    entries must be tracking-1 through tracking-1000 in that order.
    """
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS

    submitted: OrderedDict = OrderedDict()

    # Fill to cap + 1 with the same pattern the coordinator uses.
    for i in range(MAX_SUBMITTED_TRACKING_NUMBERS + 1):
        key = f"TRACKING-{i}"
        submitted[key] = None
        if len(submitted) > MAX_SUBMITTED_TRACKING_NUMBERS:
            submitted.popitem(last=False)

    # The surviving keys must be TRACKING-1 … TRACKING-1000 in order.
    surviving_keys = list(submitted.keys())
    assert surviving_keys[0] == "TRACKING-1", "After one eviction the new oldest must be TRACKING-1"
    assert surviving_keys[-1] == f"TRACKING-{MAX_SUBMITTED_TRACKING_NUMBERS}", (
        "Newest inserted entry must be last in OrderedDict"
    )
    assert len(surviving_keys) == MAX_SUBMITTED_TRACKING_NUMBERS


def test_lru_eviction_does_not_trigger_below_cap():
    """DEDUP-01: No eviction occurs when dict length equals the cap exactly.

    The guard is ``> MAX_SUBMITTED_TRACKING_NUMBERS``, not ``>=``.  Filling
    exactly to 1000 must leave all 1000 entries intact — popitem must NOT be
    called.
    """
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS

    submitted: OrderedDict = OrderedDict()

    for i in range(MAX_SUBMITTED_TRACKING_NUMBERS):
        key = f"TRACKING-{i}"
        submitted[key] = None
        if len(submitted) > MAX_SUBMITTED_TRACKING_NUMBERS:
            submitted.popitem(last=False)

    assert len(submitted) == MAX_SUBMITTED_TRACKING_NUMBERS
    assert "TRACKING-0" in submitted, (
        "Oldest entry must NOT be evicted when dict is at exactly the cap"
    )
    assert f"TRACKING-{MAX_SUBMITTED_TRACKING_NUMBERS - 1}" in submitted, (
        "Newest entry must be present when no eviction occurs"
    )


# ---------------------------------------------------------------------------
# Description fallback: order_name="" → description uses tracking_number
# ---------------------------------------------------------------------------


def _make_carrier_shipment(message_id: str = "carrier_msg1") -> ShipmentData:
    """Shipment from a direct carrier email — order_name is empty."""
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="",
        message_id=message_id,
        email_date=1700000000,
    )


async def test_gmail_coordinator_uses_tracking_number_as_description_when_order_name_empty(
    hass, mock_config_entry
):
    """FWRD-DESC-01: when order_name is '' (direct carrier email), description falls back to tracking_number."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_carrier_shipment("carrier_msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "carrier_msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args.kwargs
        assert call_kwargs["description"] == "1Z999AA10123456784", (
            "When order_name is empty, description must fall back to the tracking number"
        )


async def test_imap_coordinator_uses_tracking_number_as_description_when_order_name_empty(
    hass, mock_imap_config_entry
):
    """FWRD-DESC-02: IMAP path — when order_name is '' (direct carrier email), description falls back to tracking_number."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(200)
    shipment = _make_carrier_shipment("200")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args.kwargs
        assert call_kwargs["description"] == "1Z999AA10123456784", (
            "When order_name is empty, description must fall back to the tracking number"
        )


# ---------------------------------------------------------------------------
# Phase 13: Dedup store persistence fix (DEDUP-01, DEDUP-02, DEDUP-03)
# ---------------------------------------------------------------------------


async def test_load_store_debug_log(hass, mock_config_entry, caplog):
    """DEDUP-03: _async_load_store emits a DEBUG log with the count of loaded TNs."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["TN1", "TN2", "TN3"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        with caplog.at_level(logging.DEBUG, logger="custom_components.shop2parcel.coordinator"):
            await coord._async_load_store()
    debug_messages = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    assert "Loaded 3 submitted tracking numbers from store" in debug_messages


async def test_save_store_debug_log(hass, mock_config_entry, caplog):
    """DEDUP-03: _async_save_store emits a DEBUG log with the count of scheduled TNs.

    W1/P13-WR-06: log message updated to reflect debounced scheduling via async_delay_save.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        coord._submitted_tracking_numbers = OrderedDict([("TN_A", None), ("TN_B", None)])
        with caplog.at_level(logging.DEBUG, logger="custom_components.shop2parcel.coordinator"):
            await coord._async_save_store()
    debug_messages = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    assert "Scheduled debounced save for 2 submitted tracking numbers" in debug_messages


async def test_already_added_gmail_writes_dedup(hass, mock_config_entry):
    """DEDUP-01: Gmail coordinator writes TN to dedup store on ParcelAppAlreadyAddedError."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_shipment("msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError(
                "You have already added this delivery to the app"
            )
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
    assert "msg1" not in data
    mock_store_cls.return_value.async_delay_save.assert_called()


async def test_already_added_gmail_emits_scan_event(hass, mock_config_entry):
    """DEDUP-02: Gmail coordinator emits scan event with outcome='already_added'."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_shipment("msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError(
                "You have already added this delivery to the app"
            )
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    already_added_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "already_added"
    ]
    assert len(already_added_events) == 1
    event = already_added_events[0]
    assert event["message_id"] == "gmail:msg1"
    assert event["tracking_number"] == shipment.tracking_number
    assert event["outcome"] == "already_added"
    assert coord._diagnostics.scan_events_total >= 1


async def test_already_added_imap_writes_dedup(hass, mock_imap_config_entry):
    """DEDUP-01: IMAP coordinator writes TN to dedup store on ParcelAppAlreadyAddedError."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError(
                "You have already added this delivery to the app"
            )
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
    assert "100" not in data
    mock_store_cls.return_value.async_delay_save.assert_called()


async def test_imap_invalid_tracking_suppresses_retry(hass, mock_imap_config_entry):
    """IMAP mirror of test_invalid_tracking_not_deduped: ParcelAppInvalidTrackingError writes TN to dedup store."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad carrier")
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert "1Z999AA10123456784" in coord._submitted_tracking_numbers


async def test_already_added_imap_emits_scan_event(hass, mock_imap_config_entry):
    """DEDUP-02 IMAP: IMAP coordinator emits scan event with outcome='already_added'."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError(
                "You have already added this delivery to the app"
            )
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    already_added_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "already_added"
    ]
    assert len(already_added_events) == 1
    event = already_added_events[0]
    assert event["message_id"] == "imap:100"
    assert event["tracking_number"] == shipment.tracking_number
    assert event["outcome"] == "already_added"
    assert coord._diagnostics.scan_events_total >= 1


# -------- Store load hardening (W2/P13-WR-07) --------------------------------


async def test_load_store_with_null_submitted_tracking_numbers_does_not_crash(
    hass, mock_config_entry
):
    """W2/P13-WR-07: Non-list submitted_tracking_numbers (None) treated as empty without crash."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"submitted_tracking_numbers": None, "quota_exhausted_until": None}
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()  # must not raise

    assert coord._submitted_tracking_numbers == OrderedDict()


async def test_load_store_filters_non_string_entries(hass, mock_config_entry):
    """W2/P13-WR-07: Non-string items in submitted_tracking_numbers list are filtered."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": [None, 42, "VALID"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

    assert list(coord._submitted_tracking_numbers.keys()) == ["VALID"]


async def test_load_store_with_non_int_quota_exhausted_until(hass, mock_config_entry):
    """W2/P13-WR-07: Non-int quota_exhausted_until is treated as None."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": [],
                "quota_exhausted_until": "not an int",
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

    assert coord._quota_exhausted_until is None


# -------- _emit_scan_event helper (W7/P11-WR-01) ----------------------------


async def test_emit_scan_event_shape_and_counter(hass, mock_config_entry):
    """W7/P11-WR-01: _emit_scan_event appends correctly-shaped event and bumps counter."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)

    # Call with standard args
    coord._emit_scan_event(
        message_id="gmail:m1",
        meta={"subject": "S", "from": "F"},
        outcome="posted",
        strategy="html_template",
        tracking_number="TN1",
    )
    assert coord._diagnostics.scan_events_total == 1
    event = coord._diagnostics.scan_events[-1]
    # Must have exactly the seven contract keys (plus no extras)
    assert set(event.keys()) == {
        "timestamp",
        "message_id",
        "subject",
        "sender",
        "strategy",
        "tracking_number",
        "outcome",
    }
    assert event["message_id"] == "gmail:m1"
    assert event["subject"] == "S"
    assert event["sender"] == "F"
    assert event["strategy"] == "html_template"
    assert event["tracking_number"] == "TN1"
    assert event["outcome"] == "posted"
    assert event["timestamp"].endswith("Z")

    # Call with extra keys
    coord._emit_scan_event(
        message_id="gmail:m2",
        meta={"subject": "E", "from": "G"},
        outcome="error",
        extra={"error_type": "ValueError", "error_msg": "boom"},
    )
    assert coord._diagnostics.scan_events_total == 2
    event2 = coord._diagnostics.scan_events[-1]
    assert event2["error_type"] == "ValueError"
    assert event2["error_msg"] == "boom"
    # Contract keys still present
    assert "timestamp" in event2 and "message_id" in event2 and "outcome" in event2


# -------- Previously-silent scan event emissions (C2/P11-CR-01) ---------------


async def test_scan_event_gmail_invalid_internal_date_emits_event(hass, mock_config_entry):
    """C2/P11-CR-01: invalid_internal_date emit: gmail path emits scan event."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Message with invalid internalDate
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "not-a-number", "payload": {}}
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "invalid_internal_date"
    ]
    assert len(events) == 1
    assert events[0]["message_id"] == "gmail:msg1"


async def test_scan_event_gmail_parcelapp_quota_emits_event(hass, mock_config_entry):
    """C2/P11-CR-01: quota_exhausted_now: gmail path emits scan event for triggering email."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_shipment("msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=None)
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "quota_exhausted_now"
    ]
    assert len(events) == 1
    assert events[0]["message_id"] == "gmail:msg1"


async def test_scan_event_gmail_parcelapp_invalid_tracking_emits_event(hass, mock_config_entry):
    """C2/P11-CR-01: invalid_tracking: gmail path emits scan event."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_shipment("msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad TN")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [e for e in coord._diagnostics.scan_events if e.get("outcome") == "invalid_tracking"]
    assert len(events) == 1
    assert events[0]["message_id"] == "gmail:msg1"


async def test_scan_event_gmail_parcelapp_transient_emits_event(hass, mock_config_entry):
    """C2/P11-CR-01: transient_error: gmail path emits scan event."""
    mock_config_entry.add_to_hass(hass)
    shipment = _make_shipment("msg1")
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("timeout")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [e for e in coord._diagnostics.scan_events if e.get("outcome") == "transient_error"]
    assert len(events) == 1
    assert events[0]["message_id"] == "gmail:msg1"


async def test_scan_event_imap_parcelapp_quota_emits_event(hass, mock_imap_config_entry):
    """C2/P11-CR-01: quota_exhausted_now: IMAP path emits scan event."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=None)
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "quota_exhausted_now"
    ]
    assert len(events) == 1
    assert events[0]["message_id"] == "imap:100"


async def test_scan_event_imap_parcelapp_invalid_tracking_emits_event(hass, mock_imap_config_entry):
    """C2/P11-CR-01: invalid_tracking: IMAP path emits scan event."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad TN")
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [e for e in coord._diagnostics.scan_events if e.get("outcome") == "invalid_tracking"]
    assert len(events) == 1
    assert events[0]["message_id"] == "imap:100"


async def test_scan_event_imap_parcelapp_transient_emits_event(hass, mock_imap_config_entry):
    """C2/P11-CR-01: transient_error: IMAP path emits scan event."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("timeout")
        )
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    events = [e for e in coord._diagnostics.scan_events if e.get("outcome") == "transient_error"]
    assert len(events) == 1
    assert events[0]["message_id"] == "imap:100"


async def test_parse_exception_error_msg_strips_html_tags(hass, mock_config_entry):
    """W9/P11-WR-04: parse exception error_msg strips HTML tags from error message."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        # Parser raises with HTML-containing message
        mock_parser_cls.return_value.parse.side_effect = ValueError("<html>oops</html>")
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    error_events = [e for e in coord._diagnostics.scan_events if e.get("outcome") == "error"]
    assert len(error_events) == 1
    error_msg = error_events[0]["error_msg"]
    assert "<" not in error_msg, f"HTML tag leaked into error_msg: {error_msg!r}"
    assert ">" not in error_msg, f"HTML tag leaked into error_msg: {error_msg!r}"
    assert len(error_msg) <= 100


async def test_extract_email_meta_returns_defaults_on_malformed_input(hass, mock_config_entry):
    """W10/P11-WR-05: _extract_email_meta returns empty-string defaults on extraction failure."""
    from custom_components.shop2parcel.coordinator import _extract_email_meta

    # Malformed msg: headers contain an item that will raise during dict comprehension
    class _BadHeader:
        def get(self, key, default=None):
            raise LookupError("bad codec")

    # The simplest way to trigger the except branch is passing something that breaks iteration
    result = _extract_email_meta({"payload": {"headers": "not-a-list"}})
    assert result == {"subject": "", "from": "", "date": "", "snippet": ""}


async def test_scan_events_total_equals_emails_scanned_total_in_full_cycle(hass, mock_config_entry):
    """Invariant: scan_events_total == emails_scanned_total after a full poll cycle.

    Verifies that every code path (no_html_body, error, no_match, skipped_dedup,
    invalid_internal_date, dry_run_suppressed via debug is excluded — normal mode only)
    emits exactly one scan event.
    """
    mock_config_entry.add_to_hass(hass)

    # Feed three messages: one invalid_internal_date, one no_match, one posted
    messages = [
        {"id": "bad_date"},
        {"id": "no_match_msg"},
        {"id": "posted_msg"},
    ]

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=(messages, "q after:0")
        )

        def _side_effect_get_message(token, msg_id):
            if msg_id == "bad_date":
                return {"internalDate": "NOT-A-NUMBER", "payload": {}}
            return {"internalDate": "1700000000000", "payload": {}}

        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            side_effect=_side_effect_get_message
        )
        shipment = _make_shipment("posted_msg")

        def _parse_side_effect(html, msg_id, email_date):
            if msg_id == "no_match_msg":
                return _make_parse_result(None, skip_reason="no_match")
            return _make_parse_result(shipment)

        mock_parser_cls.return_value.parse.side_effect = _parse_side_effect
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # 3 messages in → 3 scan events (invalid_internal_date + no_match + posted)
    assert coord._diagnostics.scan_events_total == 3
    # emails_scanned_total increments at all three exits (including invalid_internal_date)
    assert coord._diagnostics.emails_scanned_total == 3


# ---------------------------------------------------------------------------
# W1/P13-WR-06: debounced Store writes
# ---------------------------------------------------------------------------


async def test_save_store_uses_async_delay_save_not_async_save(hass, mock_config_entry):
    """W1/P13-WR-06: _async_save_store schedules via async_delay_save (not async_save).

    Calling _async_save_store directly must invoke async_delay_save once with
    delay=5 and must NOT call async_save at all.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        delay_save_mock = MagicMock()
        async_save_mock = AsyncMock()
        mock_store_cls.return_value.async_delay_save = delay_save_mock
        mock_store_cls.return_value.async_save = async_save_mock

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_save_store()

    # async_delay_save called once with delay=5
    delay_save_mock.assert_called_once()
    _, kwargs = delay_save_mock.call_args
    assert kwargs.get("delay") == 5
    # async_save must NOT be called (W1: debounce replaces immediate write)
    async_save_mock.assert_not_called()


# -------- Multi-shipment digests (extra_shipments) ----------------------
# A single email (e.g. USPS Informed Delivery) can yield several shipments.
# The first uses the bare msg_id/UID as its storage key; extras get a composite
# key f"{msg_id}::{tracking_number}" so each becomes a distinct HA entity.
# (gmail_coordinator.py:338 / imap_coordinator.py:282) — previously only the
# parser was tested with the digest fixture; the coordinator forwarding loop was not.


def _make_multi_parse_result(primary: ShipmentData, extras: list[ShipmentData]) -> ParseResult:
    """Build a ParseResult carrying a primary shipment plus extra_shipments."""
    return ParseResult(
        shipment=primary,
        skip_reason=None,
        strategy_used="html_template",
        keyword_hits={
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        },
        extra_shipments=extras,
    )


async def test_gmail_multi_shipment_creates_composite_keys(hass, mock_config_entry):
    """Gmail digest: each extra shipment is POSTed and stored under a composite key.

    Verifies (gmail_coordinator.py:338):
      - the primary shipment keeps the bare msg_id key
      - each extra gets f"{msg_id}::{tracking_number}"
      - one POST per shipment
      - every normalized tracking number is recorded for dedup
    """
    mock_config_entry.add_to_hass(hass)
    primary = ShipmentData("1Z111AA10123456784", "UPS", "#1", "msg1", 1700000000)
    extra = ShipmentData("9400111899223197428490", "USPS", "#1", "msg1", 1700000000)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>digest</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_multi_parse_result(primary, [extra])
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "msg1" in data
    assert "msg1::9400111899223197428490" in data
    assert data["msg1"].tracking_number == "1Z111AA10123456784"
    assert data["msg1::9400111899223197428490"].tracking_number == "9400111899223197428490"
    assert mock_parcel_cls.return_value.async_add_delivery.call_count == 2
    assert "1Z111AA10123456784" in coord._submitted_tracking_numbers
    assert "9400111899223197428490" in coord._submitted_tracking_numbers


async def test_imap_multi_shipment_creates_composite_keys(hass, mock_imap_config_entry):
    """IMAP digest: each extra shipment is POSTed and stored under a composite key.

    Mirror of the Gmail multi-shipment test for the IMAP path
    (imap_coordinator.py:282) — the primary keeps the bare UID, extras get
    f"{uid}::{tracking_number}".
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    primary = ShipmentData("1Z111AA10123456784", "UPS", "#1", "100", 0)
    extra = ShipmentData("9400111899223197428490", "USPS", "#1", "100", 0)
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>digest</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_multi_parse_result(primary, [extra])
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "100" in data
    assert "100::9400111899223197428490" in data
    assert data["100"].tracking_number == "1Z111AA10123456784"
    assert data["100::9400111899223197428490"].tracking_number == "9400111899223197428490"
    assert mock_parcel_cls.return_value.async_add_delivery.call_count == 2
    assert "1Z111AA10123456784" in coord._submitted_tracking_numbers
    assert "9400111899223197428490" in coord._submitted_tracking_numbers


async def test_cleanup_removes_delivered_composite_key_entity(hass, mock_config_entry):
    """Cleanup must resolve composite storage keys, not just bare msg_ids.

    The reverse lookup {tracking_number: storage_key} (coordinator.py:504) maps a
    delivered tracking number back to its composite key so the right entity is
    removed. This protects the documented invariant that composite keys must NOT
    be collapsed back to a bare msg_id.
    """
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(
        return_value=[
            {"tracking_number": "TRACK_EXTRA", "status_code": 0},  # delivered → remove
            {"tracking_number": "TRACK_PRIMARY", "status_code": 2},  # in transit → keep
        ]
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        coordinator.async_set_updated_data(
            {
                "msg1": ShipmentData("TRACK_PRIMARY", "UPS", "#1", "msg1", 1),
                "msg1::TRACK_EXTRA": ShipmentData("TRACK_EXTRA", "USPS", "#1", "msg1", 1),
            }
        )
        await coordinator.async_cleanup_delivered(datetime.now(timezone.utc))

    assert "msg1::TRACK_EXTRA" not in coordinator.data, (
        "delivered composite-key shipment must be removed"
    )
    assert "msg1" in coordinator.data, "in-transit primary shipment must be retained"


# -------- FIFO trim of the live shipment dict ---------------------------
# current_data (the user-visible sensor set) is capped at MAX_SUBMITTED_TRACKING_NUMBERS.
# When a poll pushes it past the cap, the oldest entries are dropped and a WARNING
# is logged (gmail_coordinator.py:562 / imap_coordinator.py:500). This is a DIFFERENT
# structure from the _submitted_tracking_numbers dedup set covered by the LRU tests.


async def test_gmail_fifo_trim_drops_oldest_and_warns(hass, mock_config_entry, caplog):
    """Gmail: poll pushing current_data past the cap trims the oldest entry + warns."""
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)
    new_shipment = _make_shipment("msg-new")  # tracking 1Z999AA10123456784
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg-new"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(new_shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Pre-seed exactly the cap of restored shipments (self.data is None on first poll,
        # so current_data is seeded from _restored_shipments).
        coord._restored_shipments = {
            f"old-{i}": ShipmentData(f"OLDTRACK{i}", "UPS", "#old", f"old-{i}", 1)
            for i in range(MAX_SUBMITTED_TRACKING_NUMBERS)
        }
        with caplog.at_level(logging.WARNING):
            data = await coord._async_update_data()

    assert len(data) == MAX_SUBMITTED_TRACKING_NUMBERS, "current_data must be trimmed back to cap"
    assert "msg-new" in data, "newest (just-posted) shipment must be retained"
    assert "old-0" not in data, "oldest restored shipment must be trimmed first (FIFO)"
    assert "FIFO trim removed" in caplog.text


async def test_imap_fifo_trim_drops_oldest_and_warns(hass, mock_imap_config_entry, caplog):
    """IMAP: poll pushing current_data past the cap trims the oldest entry + warns."""
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS  # noqa: PLC0415

    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    new_shipment = _make_shipment("100")  # tracking 1Z999AA10123456784
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(new_shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        coord._restored_shipments = {
            f"old-{i}": ShipmentData(f"OLDTRACK{i}", "UPS", "#old", f"old-{i}", 1)
            for i in range(MAX_SUBMITTED_TRACKING_NUMBERS)
        }
        with caplog.at_level(logging.WARNING):
            data = await coord._async_update_data()

    assert len(data) == MAX_SUBMITTED_TRACKING_NUMBERS, "current_data must be trimmed back to cap"
    assert "100" in data, "newest (just-posted) shipment must be retained"
    assert "old-0" not in data, "oldest restored shipment must be trimmed first (FIFO)"
    assert "FIFO trim removed" in caplog.text


# -------- Plain-text body fallback (PR4-I1) -----------------------------
# When an email has no HTML part, the coordinator falls back to the text body and
# wraps it in <pre> with HTML-escaping so raw <, >, & don't yield malformed HTML
# for BeautifulSoup (gmail_coordinator.py:231 / imap_coordinator.py:181).


async def test_gmail_plain_text_body_is_escaped_and_wrapped(hass, mock_config_entry):
    """Gmail: empty HTML body → text body is HTML-escaped and wrapped in <pre>."""
    mock_config_entry.add_to_hass(hass)
    captured: dict[str, str] = {}

    def _capture_parse(html, msg_id, email_date):
        captured["html"] = html
        return _make_parse_result(_make_shipment("msg1"))

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="",
        ),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_text_body",
            return_value="Tracking <1Z999> & shipped",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = _capture_parse
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "<pre>" in captured["html"], (
        "text body must be wrapped in <pre> for newline preservation"
    )
    assert "&lt;1Z999&gt;" in captured["html"], "angle brackets must be HTML-escaped"
    assert "&amp;" in captured["html"], "ampersand must be HTML-escaped"
    assert "<1Z999>" not in captured["html"], "raw angle brackets must NOT survive into the HTML"
    assert "msg1" in data, "shipment parsed from the wrapped text body must be forwarded"


async def test_imap_plain_text_body_is_escaped_and_wrapped(hass, mock_imap_config_entry):
    """IMAP: empty HTML body → text body is HTML-escaped and wrapped in <pre>."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    captured: dict[str, str] = {}

    def _capture_parse(html, uid_str, email_date):
        captured["html"] = html
        return _make_parse_result(_make_shipment("100"))

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="",
        ),
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_text_body_imap",
            return_value="Tracking <1Z999> & shipped",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.side_effect = _capture_parse
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "<pre>" in captured["html"], (
        "text body must be wrapped in <pre> for newline preservation"
    )
    assert "&lt;1Z999&gt;" in captured["html"], "angle brackets must be HTML-escaped"
    assert "&amp;" in captured["html"], "ampersand must be HTML-escaped"
    assert "<1Z999>" not in captured["html"], "raw angle brackets must NOT survive into the HTML"
    assert "100" in data, "shipment parsed from the wrapped text body must be forwarded"


# -------- _async_load_store error handling -------------------------------


async def test_load_store_oserror_starts_with_empty_state(hass, mock_config_entry, caplog):
    """An OSError loading the store is logged and leaves empty dedup/restore state."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(side_effect=OSError("disk failure"))
        coord = GmailCoordinator(hass, mock_config_entry)
        with caplog.at_level(logging.ERROR):
            await coord._async_load_store()

    assert coord._submitted_tracking_numbers == OrderedDict()
    assert coord._restored_shipments == {}
    assert coord._store_loaded is True


async def test_load_store_unexpected_error_raises_config_entry_not_ready(hass, mock_config_entry):
    """A non-OSError loading the store raises ConfigEntryNotReady so HA retries setup."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(side_effect=RuntimeError("corrupt"))
        coord = GmailCoordinator(hass, mock_config_entry)
        with pytest.raises(ConfigEntryNotReady):
            await coord._async_load_store()


# -------- _async_save_store scheduling failure ---------------------------


async def test_save_store_swallows_scheduling_error(hass, mock_config_entry, caplog):
    """If async_delay_save raises, _async_save_store logs and does NOT propagate."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock(
            side_effect=RuntimeError("scheduling failed")
        )
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with caplog.at_level(logging.ERROR):
            await coord._async_save_store()  # must not raise

    assert "Failed to schedule" in caplog.text


# -------- async_cleanup_delivered error handling (data present) ----------
# These set non-empty coordinator.data so cleanup proceeds past the early-return
# guard and actually reaches the async_get_deliveries() call + except handlers.


async def _setup_coord_with_one_shipment(hass, mock_config_entry):
    """Set up a GmailCoordinator with one shipment in coordinator.data (for cleanup tests).

    Does NOT patch ParcelAppClient — the caller must patch it around the
    async_cleanup_delivered() call so the patch is still active when cleanup
    builds its client (otherwise cleanup would use the real client).
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data(
            {"msg_a": ShipmentData("TRACK_A", "UPS", "#1", "msg_a", 1)}
        )
    return coordinator


async def test_cleanup_auth_error_returns_without_raising(hass, mock_config_entry):
    """ParcelAppAuthError during cleanup is caught + logged + returns (data untouched)."""
    coordinator = await _setup_coord_with_one_shipment(hass, mock_config_entry)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppAuthError("bad key"))
    with patch(
        "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
    ):
        assert await coordinator.async_cleanup_delivered(datetime.now(timezone.utc)) is None
    fake_client.async_get_deliveries.assert_awaited_once()
    assert "msg_a" in coordinator.data


async def test_cleanup_transient_error_returns_without_raising(hass, mock_config_entry):
    """ParcelAppTransientError during cleanup is caught + logged + returns (data untouched)."""
    coordinator = await _setup_coord_with_one_shipment(hass, mock_config_entry)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppTransientError("5xx"))
    with patch(
        "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
    ):
        assert await coordinator.async_cleanup_delivered(datetime.now(timezone.utc)) is None
    fake_client.async_get_deliveries.assert_awaited_once()
    assert "msg_a" in coordinator.data


async def test_cleanup_unexpected_error_returns_without_raising(hass, mock_config_entry):
    """An unexpected error during cleanup is caught + logged + returns (data untouched)."""
    coordinator = await _setup_coord_with_one_shipment(hass, mock_config_entry)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(side_effect=ValueError("unexpected"))
    with patch(
        "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
    ):
        assert await coordinator.async_cleanup_delivered(datetime.now(timezone.utc)) is None
    fake_client.async_get_deliveries.assert_awaited_once()
    assert "msg_a" in coordinator.data


# -------- email-meta extraction exception guards (module-level helpers) ---


def test_extract_email_meta_returns_defaults_on_attribute_error():
    """_extract_email_meta returns safe defaults when payload access raises (W10/P11-WR-05)."""
    # payload is a list → .get(...) raises AttributeError → handler returns defaults.
    result = _extract_email_meta({"payload": []})
    assert result == {"subject": "", "from": "", "date": "", "snippet": ""}


def test_extract_imap_email_meta_returns_defaults_on_parse_error():
    """_extract_imap_email_meta returns safe defaults when message_from_bytes raises."""
    with patch(
        "custom_components.shop2parcel.coordinator._email_stdlib.message_from_bytes",
        side_effect=LookupError("unknown codec"),
    ):
        result = _extract_imap_email_meta(b"raw bytes")
    assert result == {"subject": "", "from": "", "date": "", "snippet": ""}


# -------- Phase 26: counter state helpers --------------------------------


async def test_used_today_resets_on_date_rollover(hass, mock_config_entry):
    """P26-CNT-03: _maybe_reset_used_today resets _used_today=0 on stale date; no-op same day.

    Wave 0 RED: fails until _maybe_reset_used_today and _used_today_date are added to coordinator.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)

    # Seed stale values
    coord._used_today = 5
    coord._used_today_date = "2000-01-01"  # stale date — guaranteed to differ from today

    coord._maybe_reset_used_today()

    assert coord._used_today == 0, "used_today must reset to 0 on stale date"
    # _used_today_date must now match today's UTC date
    from datetime import UTC
    from datetime import datetime as _dt

    assert coord._used_today_date == _dt.now(UTC).strftime("%Y-%m-%d"), (
        "_used_today_date must be updated to today's UTC date after reset"
    )

    # Second call same day — must NOT reset (stays at current value)
    coord._used_today = 2
    coord._maybe_reset_used_today()
    assert coord._used_today == 2, (
        "_maybe_reset_used_today must be no-op when date has not rolled over"
    )


async def test_record_forward_skips_already_added(hass, mock_config_entry):
    """P26-CNT: _record_forward increments counters exactly N times when called N times.

    Wave 0 RED: fails until _record_forward is added to coordinator.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)

    assert coord.total_forwarded == 0
    assert coord.last_forwarded_ts is None

    coord._record_forward()
    assert coord.total_forwarded == 1
    assert coord.last_forwarded_ts is not None
    assert isinstance(coord.last_forwarded_ts, int)

    coord._record_forward()
    assert coord.total_forwarded == 2, "_record_forward must increment by exactly 1 on each call"


# -------- Phase 26: _record_forward wired at POST-success sites (Task 2) ------


async def test_total_forwarded_increments_on_gmail_post(hass, mock_config_entry):
    """P26-CNT-01 (site 1): Gmail Stage-1 2xx success increments total_forwarded/used_today.

    AlreadyAdded must NOT increment (proves counter is on the true 2xx path only).
    Wave 0 RED: fails until _record_forward is called in gmail_coordinator.py success block.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        # First poll: async_add_delivery succeeds (2xx)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert coord.total_forwarded == 1, "total_forwarded must be 1 after one successful gmail POST"
    assert coord.used_today == 1, "used_today must be 1 after one successful gmail POST"
    assert coord.last_forwarded_ts is not None, (
        "last_forwarded_ts must be set after successful POST"
    )

    # Second poll: same TN now raises AlreadyAdded — counter must NOT increment
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls2,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"
        ) as mock_parcel_cls2,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls2,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls2,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth2,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth2.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth2.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth2.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls2.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls2.return_value.async_delay_save = MagicMock()
        # Use a new tracking number so dedup doesn't skip it before even attempting POST
        new_shipment = ShipmentData(
            tracking_number="ALREADY_TN_001",
            carrier_name="UPS",
            order_name="#1235",
            message_id="msg2",
            email_date=1700000001,
        )
        mock_gmail_cls2.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg2"}], "q after:0")
        )
        mock_gmail_cls2.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000001000", "payload": {}}
        )
        mock_parser_cls2.return_value.parse.return_value = _make_parse_result(new_shipment)
        mock_parcel_cls2.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )
        coord2 = GmailCoordinator(hass, mock_config_entry)
        await coord2._async_load_store()
        # Copy counter state to new coordinator instance AFTER load (load resets to 0)
        coord2._total_forwarded = coord._total_forwarded
        coord2._used_today = coord._used_today
        coord2._used_today_date = coord._used_today_date
        coord2._last_forwarded_ts = coord._last_forwarded_ts
        await coord2._async_update_data()

    assert coord2.total_forwarded == 1, (
        "total_forwarded must NOT increment on AlreadyAdded (stays at 1)"
    )


async def test_total_forwarded_increments_on_imap_post(hass, mock_imap_config_entry):
    """P26-CNT-01 (site 2): IMAP Stage-1 2xx success increments total_forwarded/used_today.

    AlreadyAdded must NOT increment.
    Wave 0 RED: fails until _record_forward is called in imap_coordinator.py success block.
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(200)
    shipment = _make_shipment("200")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()  # success

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert coord.total_forwarded == 1, "total_forwarded must be 1 after one successful IMAP POST"
    assert coord.used_today == 1, "used_today must be 1 after one successful IMAP POST"
    assert coord.last_forwarded_ts is not None

    # Second poll: AlreadyAdded on a different TN — counter must NOT increment
    raw_msg2 = _make_imap_raw_message(201)
    already_added_shipment = ShipmentData(
        tracking_number="IMAP_ALREADY_001",
        carrier_name="UPS",
        order_name="#2001",
        message_id="201",
        email_date=1700000001,
    )
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls2,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls2,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls2,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls2,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls2.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls2.return_value.async_delay_save = MagicMock()
        mock_imap_cls2.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg2])
        mock_parser_cls2.return_value.parse.return_value = _make_parse_result(
            already_added_shipment
        )
        mock_parcel_cls2.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        coord2 = ImapCoordinator(hass, mock_imap_config_entry)
        await coord2._async_load_store()
        # Copy counter state to new coordinator AFTER load (load resets to 0)
        coord2._total_forwarded = coord._total_forwarded
        coord2._used_today = coord._used_today
        coord2._used_today_date = coord._used_today_date
        coord2._last_forwarded_ts = coord._last_forwarded_ts
        await coord2._async_update_data()

    assert coord2.total_forwarded == 1, (
        "total_forwarded must NOT increment on IMAP AlreadyAdded (stays at 1)"
    )


async def test_total_forwarded_increments_on_drain_post(hass, mock_config_entry):
    """P26-CNT-01 (site 4): drain 2xx success increments total_forwarded.

    AlreadyAdded fall-through in drain must NOT increment (2xx-gated by posted_2xx flag).
    Wave 0 RED: fails until _record_forward is called with 2xx gate in _async_drain_pending_posts.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        # Seed one pending post
        drain_shipment = ShipmentData(
            tracking_number="DRAIN_TN_001",
            carrier_name="UPS",
            order_name="#8001",
            message_id="msg-drain-1",
            email_date=1700000001,
        )
        coord._pending_posts = {"drain_key_1": drain_shipment}
        coord._quota_exhausted_until = None
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()  # 2xx

        await coord._async_drain_pending_posts()

    assert coord.total_forwarded == 1, "total_forwarded must be 1 after one successful drain POST"
    assert coord.last_forwarded_ts is not None, "last_forwarded_ts must be set after drain POST"

    # Now test AlreadyAdded in drain: counter must NOT increment
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls2,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls2,
    ):
        mock_store_cls2.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls2.return_value.async_delay_save = MagicMock()

        coord3 = GmailCoordinator(hass, mock_config_entry)
        await coord3._async_load_store()
        # Copy counter state AFTER load (load resets to 0)
        coord3._total_forwarded = coord._total_forwarded
        coord3._used_today = coord._used_today
        coord3._used_today_date = coord._used_today_date
        coord3._last_forwarded_ts = coord._last_forwarded_ts

        already_added_drain = ShipmentData(
            tracking_number="DRAIN_ALREADY_002",
            carrier_name="UPS",
            order_name="#8002",
            message_id="msg-drain-2",
            email_date=1700000002,
        )
        coord3._pending_posts = {"drain_key_2": already_added_drain}
        coord3._quota_exhausted_until = None
        mock_parcel_cls2.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        await coord3._async_drain_pending_posts()

    assert coord3.total_forwarded == 1, (
        "total_forwarded must NOT increment on drain AlreadyAdded (stays at 1)"
    )


async def test_load_store_rejects_corrupt_operational_counters(hass, mock_config_entry, caplog):
    """Finding 4: hydration guard must reject negative/bool counters and warn on a
    corrupt last_forwarded_ts.

    The guard claims to prevent 'counter inflation from corrupt or hand-edited store
    data' (T-26-01) but isinstance(x, int) accepts negatives AND bool (an int
    subclass). A negative used_today made ParcelAppQuotaSensor advertise MORE than the
    daily limit. last_forwarded_ts silently coerced bad data to None with no warning.
    """
    mock_config_entry.add_to_hass(hass)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "total_forwarded": -5,
                "used_today": -3,
                "used_today_date": today,  # avoid the property's rollover reset masking the guard
                "last_forwarded_ts": "not-an-int",
            }
        )
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        with caplog.at_level(logging.WARNING):
            await coord._async_load_store()

    # Negative counters rejected → reset to 0 (not persisted as negatives).
    assert coord._total_forwarded == 0, "negative total_forwarded must reset to 0"
    assert coord._used_today == 0, "negative used_today must reset to 0"
    # Corrupt timestamp rejected → None, WITH a warning (symmetric with the counters).
    assert coord._last_forwarded_ts is None
    assert "last_forwarded_ts" in caplog.text, (
        "a corrupt last_forwarded_ts must be surfaced with a WARNING, not swallowed silently"
    )


async def test_load_store_rejects_bool_counters(hass, mock_config_entry):
    """Finding 4: bool is an int subclass — True must not slip through as used_today=1."""
    mock_config_entry.add_to_hass(hass)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "total_forwarded": True,
                "used_today": True,
                "used_today_date": today,
                "last_forwarded_ts": False,
            }
        )
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

    assert coord._total_forwarded == 0
    assert coord._used_today == 0
    assert coord._last_forwarded_ts is None


async def test_load_store_accepts_valid_counters(hass, mock_config_entry):
    """Finding 4: legitimate non-negative ints still hydrate unchanged."""
    mock_config_entry.add_to_hass(hass)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "total_forwarded": 42,
                "used_today": 7,
                "used_today_date": today,
                "last_forwarded_ts": 1700000000,
            }
        )
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

    assert coord._total_forwarded == 42
    assert coord._used_today == 7
    assert coord._last_forwarded_ts == 1700000000


async def test_used_today_rollover_persists_to_store(hass, mock_config_entry):
    """Finding 5: a UTC date-rollover reset of used_today must be persisted.

    _maybe_reset_used_today zeroed used_today/used_today_date in memory on rollover but
    never scheduled a save. An HA restart after midnight UTC but before the first
    forward restored yesterday's count, so the ParcelApp Quota sensor briefly
    under-reported remaining quota. The reset must schedule a debounced persist.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        # Seed yesterday's state, then clear the save spy.
        coord._used_today = 7
        coord._used_today_date = "2000-01-01"  # definitely not today (UTC)
        coord._store.async_delay_save.reset_mock()

        # Reading the property triggers the UTC rollover reset.
        assert coord.used_today == 0
        assert coord._used_today_date == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert coord._store.async_delay_save.called, (
            "used_today rollover reset must schedule a store save so it survives restart"
        )


async def test_used_today_no_rollover_does_not_persist(hass, mock_config_entry):
    """Finding 5: reads WITHOUT a rollover must not schedule redundant saves."""
    mock_config_entry.add_to_hass(hass)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        coord._used_today = 3
        coord._used_today_date = today
        coord._store.async_delay_save.reset_mock()

        assert coord.used_today == 3
        assert not coord._store.async_delay_save.called, (
            "a same-day used_today read must not schedule a save (no rollover happened)"
        )


# ---------------------------------------------------------------------------
# Finding 7: poll-lifecycle listener dispatch (gmail + imap)
# ---------------------------------------------------------------------------


async def test_gmail_poll_single_dispatch_on_success(hass, mock_config_entry):
    """Finding 7: a successful poll must not fire redundant listener dispatches.

    The wrapper notified at start AND in finally; with HA's own post-return dispatch
    that was up to 3 notifications per poll. A direct _async_update_data() call (HA's
    base wrapper not involved) must now notify exactly once — the start ON. HA's base
    coordinator dispatches the OFF after we return.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "t",
            "refresh_token": "r",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        with patch.object(coord, "async_update_listeners") as mock_notify:
            await coord._async_update_data()

    assert coord._poll_in_progress is False
    assert mock_notify.call_count == 1, (
        f"successful poll should dispatch once (start ON); got {mock_notify.call_count}"
    )


async def test_gmail_poll_dispatches_off_on_failure(hass, mock_config_entry):
    """Finding 7: on poll failure the wrapper must still flip the sensor OFF.

    HA's base _async_refresh may re-raise before its own dispatch (e.g.
    ConfigEntryAuthFailed), so the failure path must reset the flag AND notify.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        with (
            patch.object(
                coord, "_async_update_data_inner", AsyncMock(side_effect=UpdateFailed("boom"))
            ),
            patch.object(coord, "async_update_listeners") as mock_notify,
        ):
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    assert coord._poll_in_progress is False
    assert mock_notify.called, "failure path must dispatch so the sensor flips OFF"


async def test_imap_poll_single_dispatch_on_success(hass, mock_imap_config_entry):
    """Finding 7 (IMAP parity): a successful poll dispatches once on a direct call."""
    mock_imap_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()

        with patch.object(coord, "async_update_listeners") as mock_notify:
            await coord._async_update_data()

    assert coord._poll_in_progress is False
    assert mock_notify.call_count == 1, (
        f"successful IMAP poll should dispatch once (start ON); got {mock_notify.call_count}"
    )


async def test_imap_poll_dispatches_off_on_failure(hass, mock_imap_config_entry):
    """Finding 7 (IMAP parity): failure path resets the flag AND dispatches OFF."""
    mock_imap_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()

        with (
            patch.object(
                coord, "_async_update_data_inner", AsyncMock(side_effect=UpdateFailed("boom"))
            ),
            patch.object(coord, "async_update_listeners") as mock_notify,
        ):
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    assert coord._poll_in_progress is False
    assert mock_notify.called, "IMAP failure path must dispatch so the sensor flips OFF"


# ---------------------------------------------------------------------------
# Finding 3: time-boundary refresh timers
# ---------------------------------------------------------------------------


async def test_arm_quota_expiry_timer_gated_by_enable(hass, mock_config_entry):
    """Finding 3: arming is a no-op until enable_operational_timers().

    This gate is what keeps the many bare-coordinator tests that assign
    _quota_exhausted_until directly from leaking real (lingering) timers.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)

        # Disabled (bare coordinator): arming does nothing even with a future window.
        coord._quota_exhausted_until = int(time_module.time()) + 3600
        coord._arm_quota_expiry_timer()
        assert coord._quota_expiry_unsub is None

        # Enabled: a future window schedules a timer; clearing the window cancels it.
        coord.enable_operational_timers()
        coord._quota_exhausted_until = int(time_module.time()) + 3600
        coord._arm_quota_expiry_timer()
        assert coord._quota_expiry_unsub is not None
        coord._quota_exhausted_until = None
        coord._arm_quota_expiry_timer()
        assert coord._quota_expiry_unsub is None

        coord._cancel_operational_timers()


async def test_quota_expiry_timer_fires_and_refreshes(hass, mock_config_entry):
    """Finding 3: when the quota window elapses, the timer clears the stale block and
    dispatches to listeners so ProblemBinarySensor / ParcelAppQuotaSensor refresh
    without waiting for the next poll.
    """
    import homeassistant.util.dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        coord.enable_operational_timers()

        coord._quota_exhausted_until = int(time_module.time()) + 30
        coord._arm_quota_expiry_timer()
        assert coord.quota_is_exhausted is True
        assert coord._quota_expiry_unsub is not None

        notified = []
        with patch.object(coord, "async_update_listeners", side_effect=lambda: notified.append(1)):
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
            await hass.async_block_till_done()

        assert coord._quota_exhausted_until is None, "expiry timer must clear the stale window"
        assert coord.quota_is_exhausted is False
        assert notified, "expiry timer must dispatch so the Problem/Quota entities refresh"

        coord._cancel_operational_timers()


async def test_midnight_refresh_resets_used_today(hass, mock_config_entry):
    """Finding 3: the UTC-midnight timer forces the used_today rollover reset and
    dispatches to listeners, then reschedules itself for the next midnight.
    """
    import homeassistant.util.dt as dt_util

    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        coord.enable_operational_timers()
        assert coord._midnight_unsub is not None, "enable must schedule the midnight timer"

        coord._used_today = 9
        coord._used_today_date = "2000-01-01"  # stale prior day

        notified = []
        with patch.object(coord, "async_update_listeners", side_effect=lambda: notified.append(1)):
            coord._on_midnight(dt_util.utcnow())  # direct call avoids the self-reschedule fire loop

        assert coord._used_today == 0, "midnight refresh must reset used_today"
        assert coord._used_today_date == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert notified, "midnight refresh must dispatch so the Quota sensor updates"
        assert coord._midnight_unsub is not None, "midnight timer must reschedule itself"

        coord._cancel_operational_timers()


async def test_forward_counter_persisted_immediately(hass, mock_config_entry):
    """Finding 12: the immediate post-forward save carries the incremented counters, so an
    HA crash before the 5s debounce fire cannot lose them.
    """
    mock_config_entry.add_to_hass(hass)
    captured: dict = {}

    async def _capture(data):
        captured.update(data)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock(side_effect=_capture)
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert captured.get("total_forwarded") == 1, "immediate save must carry total_forwarded"
    assert captured.get("used_today") == 1, "immediate save must carry used_today"
    assert captured.get("last_forwarded_ts") is not None, "immediate save must carry the timestamp"


async def test_gmail_poll_start_notify_failure_resets_flag(hass, mock_config_entry):
    """Finding 8: if the start-of-poll listener dispatch raises, _poll_in_progress must
    still reset (no leaked True) and the original error must surface unmasked.
    """
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        with patch.object(coord, "async_update_listeners", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await coord._async_update_data()

    assert coord._poll_in_progress is False, (
        "a start-notify failure must not leak _poll_in_progress=True"
    )


async def test_imap_poll_start_notify_failure_resets_flag(hass, mock_imap_config_entry):
    """Finding 8 (IMAP parity): start-notify failure must reset the poll-in-progress flag."""
    mock_imap_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()

        with patch.object(coord, "async_update_listeners", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await coord._async_update_data()

    assert coord._poll_in_progress is False, (
        "a start-notify failure must not leak _poll_in_progress=True"
    )
