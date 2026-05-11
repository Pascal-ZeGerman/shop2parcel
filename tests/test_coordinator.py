"""Tests for Shop2Parcel coordinator — covers EMAIL-05, FWRD-01..FWRD-05."""

from __future__ import annotations

import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ImapAuthError,
    ImapTransientError,
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
from collections import OrderedDict

from custom_components.shop2parcel.coordinator import (
    Shop2ParcelCoordinator,
    _next_midnight_utc,
)


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
    coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("msg1")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        # Load store first — this is the contract
        await coord._async_load_store()
        assert "1Z999AA10123456784" in coord._submitted_tracking_numbers
        # Now run the poll — tracking number dedup blocks the POST.
        await coord._async_update_data()
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_store_saved_after_post(hass, mock_config_entry):
    """FWRD-03: Store.async_save called immediately after each successful POST.

    Phase 10 change: saves are now per-POST (not deferred to end of loop).
    Two distinct shipments → at least 2 save calls.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = AsyncMock()
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
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # Phase 10: immediate save after each POST — at least 2 saves for 2 distinct shipments.
        assert save_mock.await_count >= 2


# -------- FWRD-04: quota handling ---------------------------------------


async def test_quota_exhaustion(hass, mock_config_entry):
    """FWRD-04: ParcelAppQuotaError sets quota_exhausted_until, logs warning, NOT raised."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=1234567890)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Should NOT raise — quota is handled gracefully
        data = await coord._async_update_data()
        assert coord._quota_exhausted_until == 1234567890
        mock_store_cls.return_value.async_save.assert_called()
        # Shipment NOT in data when quota is blocked — withheld so it is re-fetched and
        # POSTed correctly on the next cycle after quota resets (FWRD-02 fix, CR-02).
        assert "msg1" not in data


async def test_quota_exhausted_until_midnight(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until = next midnight UTC when reset_at is None."""
    mock_config_entry.add_to_hass(hass)
    expected = _next_midnight_utc()  # call before any monkeypatching
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=None)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._quota_exhausted_until == expected


async def test_quota_exhausted_until_reset_at(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until uses err.reset_at when provided."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=9999)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        assert coord._quota_exhausted_until == 9999


async def test_gmail_polling_continues_during_quota(hass, mock_config_entry):
    """FWRD-04 / D-05: while quota_exhausted_until > now, Gmail still polled, POST skipped."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = AsyncMock()
        mock_store_cls.return_value.async_save = save_mock
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

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        # Save was called at least once after recovery
        assert save_mock.await_count >= 1


# -------- FWRD-05: error translation taxonomy ---------------------------


async def test_parcelapp_transient_error_skipped(hass, mock_config_entry):
    """FWRD-05: ParcelAppTransientError is logged + skipped — NOT UpdateFailed, NOT in forwarded_ids."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("network error")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailTransientError("network error")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()


async def test_gmail_auth_raises_config_entry_auth_failed(hass, mock_config_entry):
    """FWRD-05: GmailAuthError -> ConfigEntryAuthFailed (triggers reauth)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("token revoked")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        # Token dict present but access_token is missing — triggers the guard
        mock_oauth.OAuth2Session.return_value.token = {"expires_at": 9999999999.0}
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="access_token"):
            await coord._async_update_data()


async def test_invalid_tracking_not_deduped(hass, mock_config_entry):
    """FWRD-05 / C-05: ParcelAppInvalidTrackingError is a permanent 400.

    C-05 fix: tracking number IS added to _submitted_tracking_numbers to prevent infinite retry loop
    draining the 20/day quota. Re-POSTing a 400 will always fail — suppressing
    retries is the correct behavior.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = AsyncMock()
        mock_store_cls.return_value.async_save = save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad tracking")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
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


async def test_cleanup_uses_filter_mode_recent(hass, mock_config_entry):
    """RESEARCH.md Pitfall 6: must call GET with filter_mode='recent' (NOT 'active')."""
    mock_config_entry.add_to_hass(hass)
    fake_client = MagicMock()
    fake_client.async_get_deliveries = AsyncMock(return_value=[])
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch(
            "custom_components.shop2parcel.coordinator.ParcelAppClient", return_value=fake_client
        ),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(_make_shipment("msg1")),
            _make_parse_result(_make_shipment("msg2")),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="",  # empty body triggers no_html_body
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
    coordinator = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
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

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=ImapAuthError("auth failed")
        )

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()


async def test_imap_transient_error_raises_update_failed(hass, mock_imap_config_entry):
    """IMAP FWRD-05: ImapTransientError → UpdateFailed."""
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=ImapTransientError("connection reset")
        )

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
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

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Return empty list — we only care about how fetch_shipping_emails was called
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html/>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([{"id": "msg1"}], "q after:0"))
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAuthError("api key revoked")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock(
            side_effect=Exception("token refresh network error")
        )
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        with pytest.raises(ConfigEntryAuthFailed, match="token refresh"):
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value=None,  # triggers no_html_body skip
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Phase 10: fetch_shipping_emails returns list[dict], not a tuple
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
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

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        # Verify default rescan_window_days was passed
        call_kwargs = mock_gmail_cls.return_value.async_list_messages.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("rescan_window_days") == DEFAULT_RESCAN_WINDOW_DAYS, (
            f"Coordinator must pass default rescan_window_days={DEFAULT_RESCAN_WINDOW_DAYS} "
            "when option is absent from entry.options"
        )
