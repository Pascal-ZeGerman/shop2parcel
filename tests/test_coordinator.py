"""Tests for Shop2Parcel coordinator — covers EMAIL-05, FWRD-01..FWRD-05."""

from __future__ import annotations

import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from custom_components.shop2parcel.const import (
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
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
    """FWRD-01: New parsed shipment triggers ParcelAppClient.async_add_delivery."""
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
        data = await coord._async_update_data()
        assert "msg1" in data
        assert "msg1" in coord._forwarded_ids
        mock_parcel_cls.return_value.async_add_delivery.assert_called_once()


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
        await coord.async_load_store()
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
        await coord.async_load_store()
        assert coord._forwarded_ids == {"msg1", "msg2"}
        assert coord._quota_exhausted_until is None


# -------- FWRD-03: Store load/save semantics ----------------------------


async def test_store_loaded_before_first_poll(hass, mock_config_entry):
    """FWRD-03: async_load_store called before _async_update_data on setup."""
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
        mock_parser_cls.return_value.parse.return_value = _make_shipment("sentinel_msg")
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        # Load store first — this is the contract
        await coord.async_load_store()
        assert "sentinel_msg" in coord._forwarded_ids
        # Now run the poll — sentinel_msg should be skipped because it was in store
        await coord._async_update_data()
        mock_gmail_cls.return_value.async_get_message.assert_not_called()
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()


async def test_store_saved_after_post(hass, mock_config_entry):
    """FWRD-03: Store.async_save called immediately after each successful POST."""
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
            _make_shipment("msg1"),
            _make_shipment("msg2"),
        ]
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
        await coord._async_update_data()
        assert save_mock.await_count >= 2  # one save per successful POST


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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=1234567890)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=None)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota", reset_at=9999)
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=[{"id": "new_msg"}]
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("new_msg")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg_recover")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("network error")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailTransientError("network error")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("token revoked")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()


async def test_invalid_tracking_not_deduped(hass, mock_config_entry):
    """FWRD-05: ParcelAppInvalidTrackingError logged; message_id NOT added to forwarded_ids."""
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
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        save_mock = AsyncMock()
        mock_store_cls.return_value.async_save = save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[{"id": "msg1"}])
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_shipment("msg1")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppInvalidTrackingError("bad tracking")
        )
        coord = Shop2ParcelCoordinator(hass, mock_config_entry)
        await coord.async_load_store()
        await coord._async_update_data()
        # msg1 NOT in forwarded_ids — invalid tracking is not a forwarding success.
        # This is the canonical assertion: forwarded_ids is the source of truth for
        # whether a message will be re-POSTed.  Do not assert save_mock.assert_not_called()
        # here — that would over-constrain the test and break if future coordinator logic
        # saves for other events (e.g., updating last_email_timestamp in the store).
        assert "msg1" not in coord._forwarded_ids


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
