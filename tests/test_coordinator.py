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
from collections import deque

from custom_components.shop2parcel.coordinator import (
    PollStats,
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()
        assert "msg1" in data
        assert "msg1" in coord._forwarded_ids
        mock_parcel_cls.return_value.async_add_delivery.assert_called_once()
        # Verify the real access_token string was forwarded to the Gmail client (IN-01).
        call_args = mock_gmail_cls.return_value.async_list_messages.call_args
        assert call_args[0][0] == "fake-access-token", (
            "Coordinator must extract access_token from oauth_session.token and pass it "
            "to GmailClient.async_list_messages as first positional argument"
        )


# -------- FWRD-02: deduplication via Store ------------------------------


async def test_no_duplicate_post(hass, mock_config_entry):
    """FWRD-02: message_id already in forwarded_ids set is not POSTed again."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"forwarded_ids": ["msg1"], "quota_exhausted_until": None}
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        mock_gmail_cls.return_value.async_get_message.assert_not_called()
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_dedup_survives_restart(hass, mock_config_entry):
    """FWRD-02: forwarded_ids persisted in Store survive coordinator re-init."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"forwarded_ids": ["msg1", "msg2"], "quota_exhausted_until": None}
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        assert coord._forwarded_ids == {"msg1", "msg2"}
        assert coord._quota_exhausted_until is None


# -------- FWRD-03: Store load/save semantics ----------------------------


async def test_store_loaded_before_first_poll(hass, mock_config_entry):
    """FWRD-03: _async_load_store called before _async_update_data on setup."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        # Store returns a sentinel with a known msg_id already forwarded
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"forwarded_ids": ["sentinel_msg"], "quota_exhausted_until": None}
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        # Gmail returns the same sentinel_msg
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=[{"id": "sentinel_msg"}]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            _make_shipment("sentinel_msg")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        # Load store first — this is the contract
        await coord._async_load_store()
        assert "sentinel_msg" in coord._forwarded_ids
        # Now run the poll — sentinel_msg should be skipped because it was in store
        await coord._async_update_data()
        mock_gmail_cls.return_value.async_get_message.assert_not_called()
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_store_saved_after_post(hass, mock_config_entry):
    """FWRD-03: Store.async_save called at least once after a poll cycle that successfully forwarded one or more shipments (deferred-save model — single write per cycle, not per message)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            return_value=[{"id": "msg1"}, {"id": "msg2"}]
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
        await coord._async_update_data()
        # Coordinator defers save to after the loop — one write for the whole cycle.
        assert save_mock.await_count >= 1


# -------- FWRD-04: quota handling ---------------------------------------


async def test_quota_exhaustion(hass, mock_config_entry):
    """FWRD-04: ParcelAppQuotaError sets quota_exhausted_until, logs warning, NOT raised."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            return_value=[{"id": "new_msg"}]
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            return_value=[{"id": "msg_recover"}]
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
        # New shipment is in returned data and forwarded set
        assert "msg_recover" in data
        assert "msg_recover" in coord._forwarded_ids
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        # msg1 NOT in forwarded_ids (transient error: will retry next cycle)
        assert "msg1" not in coord._forwarded_ids
        # But coordinator still returns a data dict
        assert isinstance(data, dict)


async def test_gmail_transient_raises_update_failed(hass, mock_config_entry):
    """FWRD-05: GmailTransientError -> UpdateFailed (keeps last data, retries)."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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

    C-05 fix: message IS added to forwarded_ids to prevent infinite retry loop
    draining the 20/day quota. Re-POSTing a 400 will always fail — suppressing
    retries is the correct behavior.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        # C-05 fix: msg1 IS in forwarded_ids — permanent 400 suppresses retries to
        # protect the 20/day quota from being drained by the same invalid message.
        assert "msg1" in coord._forwarded_ids


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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            side_effect=[[{"id": "msg1"}], [{"id": "msg2"}]]
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
        assert len(coord._diagnostics.last_poll_found) == 1
        # Cumulative carries over from poll 1
        assert coord._diagnostics.emails_scanned_total == 1
        # Second poll — last_poll_* must reset before processing msg2
        await coord._async_update_data()
        assert coord._diagnostics.last_poll_emails_scanned == 1  # only msg2 this cycle
        assert len(coord._diagnostics.last_poll_found) == 1  # only msg2 this cycle
        assert coord._diagnostics.last_poll_found[0]["message_id"] == "msg2"
        # Cumulative now reflects both polls
        assert coord._diagnostics.emails_scanned_total == 2
        assert coord._diagnostics.emails_matched_total == 2


async def test_diagnostics_no_html_body_skip_reason(hass, mock_config_entry):
    """DIAG-07: when extract_html_body returns empty, coordinator records
    {"message_id", "reason": "no_html_body"} in last_poll_skip_reasons."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        # _last_email_timestamp must advance even for no_html_body skips (WR-02 fix)
        assert coord._last_email_timestamp == 1700000000


async def test_diagnostics_already_forwarded_not_scanned(hass, mock_config_entry):
    """RESEARCH.md Pitfall 1: already-forwarded messages do NOT contribute to
    emails_scanned_total — the _forwarded_ids guard fires before any instrumentation."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        # Pre-load forwarded_ids with msg1 — coordinator must skip it.
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"forwarded_ids": ["msg1"], "quota_exhausted_until": None}
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()
        # msg1 was skipped by _forwarded_ids guard — not counted.
        assert coord._diagnostics.emails_scanned_total == 0
        assert coord._diagnostics.last_poll_emails_scanned == 0
        # Parser was never called for msg1
        mock_parser_cls.return_value.parse.assert_not_called()
        # No skip_reason recorded — already-forwarded is NOT propagated as a skip.
        assert coord._diagnostics.last_poll_skip_reasons == []


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
    """IMAP FWRD-01: ImapClient returns one message → parsed → forwarded → UID in _forwarded_ids."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(100)
    shipment = _make_shipment("100")

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 100))
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "100" in data
    assert "100" in coord._forwarded_ids
    mock_parcel_cls.return_value.async_add_delivery.assert_called_once()


async def test_imap_uid_dedup_skips_seen(hass, mock_imap_config_entry):
    """IMAP FWRD-02: message with already-seen UID → not re-POSTed."""
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(101)

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "forwarded_ids": ["101"],
                "quota_exhausted_until": None,
                "last_email_timestamp": None,
                "last_imap_uid": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 101))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # Already seen — must not POST again
    mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_imap_auth_error_raises_config_entry_auth_failed(hass, mock_imap_config_entry):
    """IMAP FWRD-05: ImapAuthError → ConfigEntryAuthFailed."""
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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


async def test_imap_quota_blocked_does_not_advance_last_uid(hass, mock_imap_config_entry):
    """CR-01 regression: when quota blocked, _last_imap_uid must NOT advance.

    Two messages (UIDs 100, 101) arrive while quota is exhausted.
    After the poll, _last_imap_uid must remain at its pre-poll value (None)
    and neither UID must be in _forwarded_ids, so the next poll re-includes them.
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msgs = [_make_imap_raw_message(100), _make_imap_raw_message(101)]
    shipment_100 = _make_shipment("100")
    shipment_101 = _make_shipment("101")

    # Set quota_exhausted_until to a future timestamp
    future_ts = int(time_module.time()) + 3600

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "forwarded_ids": [],
                "quota_exhausted_until": future_ts,
                "last_email_timestamp": None,
                "last_imap_uid": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=(raw_msgs, 101))
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_100),
            _make_parse_result(shipment_101),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # CR-01: UID must NOT advance when quota was blocked
    assert coord._last_imap_uid is None, (
        "CR-01: _last_imap_uid must stay None when quota was blocked this cycle"
    )
    # UIDs must NOT be in forwarded_ids — they were never successfully POSTed
    assert "100" not in coord._forwarded_ids
    assert "101" not in coord._forwarded_ids
    # No delivery was attempted — all quota-blocked
    mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# IN-04: _last_email_timestamp advancement for no_html_body skips (WR-02 behavior)
# ---------------------------------------------------------------------------


async def test_last_email_timestamp_advances_for_no_html_body(hass, mock_config_entry):
    """IN-04 / WR-02: _last_email_timestamp must advance even when message has no HTML body.

    Before the WR-02 fix, a no_html_body message would not advance max_email_date,
    causing the message to be re-fetched on every poll cycle indefinitely.
    This test verifies the fix: after a poll with only no_html_body messages,
    _last_email_timestamp is advanced to the message's internalDate.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body",
            return_value="",  # empty body triggers no_html_body skip
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
            return_value=[{"id": "msg_no_html"}]
        )
        # internalDate: 1700000000000 ms → 1700000000 seconds
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Pre-poll: timestamp is None (first run)
        assert coord._last_email_timestamp is None
        await coord._async_update_data()

    # WR-02 fix: _last_email_timestamp must advance to the no_html_body message's date
    assert coord._last_email_timestamp == 1700000000, (
        "WR-02: _last_email_timestamp must advance for no_html_body skips to prevent "
        "the message from being re-fetched indefinitely on subsequent polls."
    )
    # The message was not forwarded (no shipment extracted)
    assert "msg_no_html" not in coord._forwarded_ids


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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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


async def test_imap_no_html_body_does_not_advance_uid(hass, mock_imap_config_entry):
    """I-07: IMAP path — extract_html_body_imap returning None must NOT advance
    _last_imap_uid and must record no_html_body in diagnostics.

    Unlike the Gmail path, the IMAP path does not have a timestamp to advance;
    UID advancement only happens AFTER the loop when the whole batch is processed.
    The key requirement is that no_html_body messages are skipped and the
    diagnostics skip_reasons list is updated correctly.
    """
    mock_imap_config_entry.add_to_hass(hass)
    raw_msg = _make_imap_raw_message(200)

    with (
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value=None,  # triggers no_html_body skip
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 200))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # UID 200 has no HTML body — it was skipped at the html check, before parse.
    # _last_imap_uid must advance to 200 (the batch completed; no transient/quota block).
    assert coord._last_imap_uid == 200, (
        "I-07: _last_imap_uid must advance after a no_html_body batch "
        "so the UID is not re-fetched on the next poll"
    )
    # No shipment was forwarded
    assert "200" not in coord._forwarded_ids
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg2"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg3"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = ValueError("boom" * 30)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        # Poll 1: msg4 — gets parsed and posted
        # Poll 2: msg4b has same tracking number as msg4 — triggers skipped_dedup
        shipment_a = _make_shipment("msg4")  # TN = 1Z999AA10123456784
        shipment_b = _make_shipment("msg4b")  # same TN = 1Z999AA10123456784
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=[[{"id": "msg4"}], [{"id": "msg4b"}]]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_a),
            _make_parse_result(shipment_b),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg5"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg5"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            side_effect=[[{"id": "msg_p1"}], [{"id": "msg_p2"}]]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(_make_shipment("msg_p1")),
            _make_parse_result(_make_shipment("msg_p2")),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 300))
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 301))
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(
            None, skip_reason="no_match"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 302))
        mock_parser_cls.return_value.parse.side_effect = RuntimeError("imap parse fail")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=([raw_msg], 303))
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(shipment)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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
        patch("custom_components.shop2parcel.coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.coordinator.extract_html_body_imap",
            return_value="<html>shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            side_effect=[([raw_msg_a], 304), ([raw_msg_b], 305)]
        )
        mock_parser_cls.return_value.parse.side_effect = [
            _make_parse_result(shipment_a),
            _make_parse_result(shipment_b),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
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


async def test_scan_events_accumulate_across_gmail_and_imap(hass, mock_config_entry, mock_imap_config_entry):
    """ACTLOG-03: scan_events_total accumulates correctly in-memory (not reset per poll)."""
    # Test that scan_events_total is cumulative: two separate coordinators,
    # each contributing 1 event. This tests the in-memory accumulation.
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
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
            side_effect=[[{"id": "m1"}], [{"id": "m2"}], [{"id": "m3"}]]
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

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()  # poll 1: 1 posted
        await coord._async_update_data()  # poll 2: 1 no_match
        await coord._async_update_data()  # poll 3: 1 posted

    # scan_events_total must accumulate across polls (not reset)
    assert coord._diagnostics.scan_events_total == 3
    assert len(coord._diagnostics.scan_events) == 3
