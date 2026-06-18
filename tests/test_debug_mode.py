"""Tests for Shop2Parcel debug/dry-run mode — DBG-01 through DBG-06."""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.const import (
    CONF_DEBUG_MODE,
    CONF_GMAIL_QUERY,
    CONF_IMAP_SEARCH,
    CONF_POLL_INTERVAL,
    CONF_RESCAN_WINDOW_DAYS,
    DOMAIN,
    MAX_RESCAN_WINDOW_DAYS,
    debug_mode_notification_id,
)
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator
from custom_components.shop2parcel.options_flow import OptionsFlowHandler

# ---------------------------------------------------------------------------
# Helpers — copied verbatim from test_coordinator.py to keep test_debug_mode.py
# self-contained.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DBG-01: options flow toggle — debug_mode is saved in entry.options
# ---------------------------------------------------------------------------


async def test_dbg01_gmail_toggle(hass):
    """DBG-01 Gmail: CONF_DEBUG_MODE round-trips through the options form (True and False)."""
    handler = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry = MagicMock()
    fake_entry.options = {}
    fake_entry.data = {"connection_type": "gmail"}

    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result_true = await handler.async_step_settings(
            user_input={
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_DEBUG_MODE: True,
                "ollama_url": "",
                "ollama_model": "qwen3.5:2b",
                "ollama_timeout": 60,
                "queue_maxlen": 32,
            }
        )
    assert result_true["type"] == "create_entry"
    assert result_true["data"][CONF_DEBUG_MODE] is True

    # Re-create handler to reset state
    handler2 = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry2 = MagicMock()
    fake_entry2.options = {}
    fake_entry2.data = {"connection_type": "gmail"}

    with patch.object(
        type(handler2), "config_entry", new_callable=PropertyMock, return_value=fake_entry2
    ):
        result_false = await handler2.async_step_settings(
            user_input={
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_DEBUG_MODE: False,
                "ollama_url": "",
                "ollama_model": "qwen3.5:2b",
                "ollama_timeout": 60,
                "queue_maxlen": 32,
            }
        )
    assert result_false["type"] == "create_entry"
    assert result_false["data"][CONF_DEBUG_MODE] is False


async def test_dbg01_imap_toggle(hass):
    """DBG-01 IMAP: CONF_DEBUG_MODE round-trips through the IMAP options form."""
    handler = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry = MagicMock()
    fake_entry.options = {}
    fake_entry.data = {"connection_type": "imap"}

    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(
            user_input={
                CONF_POLL_INTERVAL: 30,
                CONF_IMAP_SEARCH: "SUBJECT shipped",
                CONF_DEBUG_MODE: True,
                "ollama_url": "",
                "ollama_model": "qwen3.5:2b",
                "ollama_timeout": 60,
                "queue_maxlen": 32,
            }
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DEBUG_MODE] is True


# ---------------------------------------------------------------------------
# DBG-02: window override — 365-day rescan window applied when debug_mode=True
# ---------------------------------------------------------------------------


async def test_dbg02_gmail_window_override(hass, mock_config_entry):
    """DBG-02 Gmail: async_list_messages receives rescan_window_days=365 (MAX) in debug mode."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True, CONF_RESCAN_WINDOW_DAYS: 7},
    )

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:X"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    call_kwargs = mock_gmail_cls.return_value.async_list_messages.call_args[1]
    assert call_kwargs["rescan_window_days"] == MAX_RESCAN_WINDOW_DAYS, (
        f"Expected rescan_window_days={MAX_RESCAN_WINDOW_DAYS}, got {call_kwargs['rescan_window_days']}"
    )
    assert call_kwargs["rescan_window_days"] != 7, (
        "rescan_window_days must NOT be the user-configured 7 — debug mode overrides it to 365"
    )


async def test_dbg02_imap_window_override(hass, mock_imap_config_entry):
    """DBG-02 IMAP: fetch_shipping_emails receives a since_date corresponding to 365-day lookback."""
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
            CONF_RESCAN_WINDOW_DAYS: 7,
        },
    )

    # W18/P14-WR-04: dual-candidate strategy — capture BEFORE so we bracket any
    # second-boundary crossing that occurs during the async poll.
    from datetime import UTC, datetime

    _IMAP_MONTH_ABBR_LOCAL = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )

    def _to_since_date(ts: int) -> str:
        dt = datetime.fromtimestamp(ts, tz=UTC)
        return f"{dt.day:02d}-{_IMAP_MONTH_ABBR_LOCAL[dt.month - 1]}-{dt.year}"

    expected_before = _to_since_date(int(time.time()) - MAX_RESCAN_WINDOW_DAYS * 86400)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    call_kwargs = mock_imap_cls.return_value.fetch_shipping_emails.call_args[1]
    since_date_arg = call_kwargs["since_date"]

    expected_after = _to_since_date(int(time.time()) - MAX_RESCAN_WINDOW_DAYS * 86400)
    # Accept either candidate (the date computed just before OR just after the poll)
    assert since_date_arg in {expected_before, expected_after}, (
        f"Expected since_date in {{{expected_before!r}, {expected_after!r}}} "
        f"(365-day lookback, dual-candidate), got {since_date_arg!r}"
    )


# ---------------------------------------------------------------------------
# DBG-03: dedup bypass — skipped_dedup never occurs; _async_save_store not called
# ---------------------------------------------------------------------------


async def test_dbg03_gmail_dedup_bypass(hass, mock_config_entry):
    """DBG-03 Gmail: pre-seeded tracking number is not used as dedup gate; store not written."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
    )

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
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:X")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        # Pre-seed the tracking number that would trigger dedup in non-debug mode
        from custom_components.shop2parcel.const import normalize_tracking_number

        coord._submitted_tracking_numbers[normalize_tracking_number("1Z999AA10123456784")] = None
        await coord._async_update_data()

    # No skipped_dedup event — dedup check was bypassed
    skipped_dedup_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "skipped_dedup"
    ]
    assert len(skipped_dedup_events) == 0, (
        f"Expected no skipped_dedup events in debug mode, got: {skipped_dedup_events}"
    )
    # Store NOT written (no dedup persistence in debug mode) — W17/P14-WR-02
    assert mock_store_cls.return_value.async_delay_save.call_count == 0, (
        f"Expected async_delay_save call_count=0 in debug mode, got {mock_store_cls.return_value.async_delay_save.call_count}"
    )


async def test_dbg03_imap_dedup_bypass(hass, mock_imap_config_entry):
    """DBG-03 IMAP: pre-seeded tracking number is not used as dedup gate; store not written."""
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
        },
    )

    # Minimal RFC 2822 raw email bytes with a text body the parser will process
    raw_email = (
        b"Subject: Your package has shipped\r\n"
        b"From: shop@test.com\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"Your tracking number is 1Z999AA10123456784\r\n"
    )

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            return_value=[{"uid": 1, "raw": raw_email}]
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("1"))

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        # Pre-seed the tracking number
        from custom_components.shop2parcel.const import normalize_tracking_number

        coord._submitted_tracking_numbers[normalize_tracking_number("1Z999AA10123456784")] = None
        await coord._async_update_data()

    skipped_dedup_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "skipped_dedup"
    ]
    assert len(skipped_dedup_events) == 0, (
        f"Expected no skipped_dedup events in debug mode, got: {skipped_dedup_events}"
    )
    # Store NOT written (no dedup persistence in debug mode) — W17/P14-WR-02
    assert mock_store_cls.return_value.async_delay_save.call_count == 0, (
        f"Expected async_delay_save call_count=0 in debug mode, got {mock_store_cls.return_value.async_delay_save.call_count}"
    )


# ---------------------------------------------------------------------------
# W17/P14-WR-02: stale-quota NOT cleared in debug mode
# ---------------------------------------------------------------------------


async def test_debug_mode_does_not_clear_stale_quota_gmail(hass, mock_config_entry):
    """W17/P14-WR-02 Gmail: stale _quota_exhausted_until is NOT cleared in debug mode."""
    import time as _time_mod

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
    )
    past_epoch = int(_time_mod.time()) - 3600  # 1 hour in the past

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        delay_save_mock = MagicMock()
        mock_store_cls.return_value.async_delay_save = delay_save_mock
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:X"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        coord._quota_exhausted_until = past_epoch
        await coord._async_update_data()

    # Quota timestamp must be UNCHANGED in debug mode
    assert coord._quota_exhausted_until == past_epoch, (
        f"Expected quota_exhausted_until unchanged ({past_epoch}), got {coord._quota_exhausted_until}"
    )
    # Store must NOT be written in debug mode
    assert delay_save_mock.call_count == 0, (
        f"Expected async_delay_save call_count=0 in debug mode, got {delay_save_mock.call_count}"
    )


async def test_debug_mode_does_not_clear_stale_quota_imap(hass, mock_imap_config_entry):
    """W17/P14-WR-02 IMAP: stale _quota_exhausted_until is NOT cleared in debug mode."""
    import time as _time_mod

    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
        },
    )
    past_epoch = int(_time_mod.time()) - 3600  # 1 hour in the past

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        delay_save_mock = MagicMock()
        mock_store_cls.return_value.async_delay_save = delay_save_mock
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        coord._quota_exhausted_until = past_epoch
        await coord._async_update_data()

    # Quota timestamp must be UNCHANGED in debug mode
    assert coord._quota_exhausted_until == past_epoch, (
        f"Expected quota_exhausted_until unchanged ({past_epoch}), got {coord._quota_exhausted_until}"
    )
    # Store must NOT be written in debug mode
    assert delay_save_mock.call_count == 0, (
        f"Expected async_delay_save call_count=0 in debug mode, got {delay_save_mock.call_count}"
    )


# ---------------------------------------------------------------------------
# DBG-04: no POST — async_add_delivery never called; dry_run_suppressed event recorded
# ---------------------------------------------------------------------------


async def test_dbg04_gmail_no_post(hass, mock_config_entry):
    """DBG-04 Gmail: async_add_delivery not called; one dry_run_suppressed scan event."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
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
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
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
            return_value=([{"id": "msg1"}], "q after:X")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0, (
        f"async_add_delivery must not be called in debug mode, call_count={mock_parcel_cls.return_value.async_add_delivery.call_count}"
    )
    dry_run_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "dry_run_suppressed"
    ]
    assert len(dry_run_events) == 1, (
        f"Expected exactly 1 dry_run_suppressed event, got: {dry_run_events}"
    )


async def test_dbg04_imap_no_post(hass, mock_imap_config_entry):
    """DBG-04 IMAP: async_add_delivery not called; one dry_run_suppressed scan event."""
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
        },
    )

    raw_email = (
        b"Subject: Your package has shipped\r\n"
        b"From: shop@test.com\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"Your tracking number is 1Z999AA10123456784\r\n"
    )

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            return_value=[{"uid": 1, "raw": raw_email}]
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0, (
        f"async_add_delivery must not be called in debug mode, call_count={mock_parcel_cls.return_value.async_add_delivery.call_count}"
    )
    dry_run_events = [
        e for e in coord._diagnostics.scan_events if e.get("outcome") == "dry_run_suppressed"
    ]
    assert len(dry_run_events) == 1, (
        f"Expected exactly 1 dry_run_suppressed event, got: {dry_run_events}"
    )


# ---------------------------------------------------------------------------
# DBG-05: INFO logs — [Shop2Parcel DEBUG] prefix per email at INFO level
# ---------------------------------------------------------------------------


async def test_dbg05_gmail_info_log(hass, mock_config_entry, caplog):
    """DBG-05 Gmail: [Shop2Parcel DEBUG] INFO log emitted for each email outcome."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
    )

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
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
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
            return_value=([{"id": "msg1"}], "q after:X")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={
                "internalDate": "1700000000000",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Test Ship"},
                        {"name": "From", "value": "shop@test.com"},
                        {"name": "Date", "value": "Mon, 14 Nov 2023 12:00:00 +0000"},
                    ]
                },
            }
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        with caplog.at_level(
            logging.DEBUG, logger="custom_components.shop2parcel.gmail_coordinator"
        ):
            await coord._async_update_data()

    debug_records = [
        r for r in caplog.records if "[Shop2Parcel DEBUG]" in r.message and r.levelname == "DEBUG"
    ]
    assert len(debug_records) >= 1, (
        f"Expected at least 1 [Shop2Parcel DEBUG] DEBUG record, got 0. Records: {[r.message for r in caplog.records]}"
    )
    # All four required fields must appear in at least one record
    combined = " ".join(r.message for r in debug_records)
    assert "subject=" in combined, "Expected 'subject=' in [Shop2Parcel DEBUG] log"
    assert "from=" in combined, "Expected 'from=' in [Shop2Parcel DEBUG] log"
    assert "candidates=" in combined, "Expected 'candidates=' in [Shop2Parcel DEBUG] log"
    assert "outcome=" in combined, "Expected 'outcome=' in [Shop2Parcel DEBUG] log"

    # One record per email — not duplicated (only one email was fed in)
    assert len(debug_records) == 1, (
        f"Expected exactly 1 [Shop2Parcel DEBUG] record for 1 email, got {len(debug_records)}"
    )


async def test_dbg05_imap_info_log(hass, mock_imap_config_entry, caplog):
    """DBG-05 IMAP: [Shop2Parcel DEBUG] INFO log emitted for each IMAP email outcome."""
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
        },
    )

    raw_email = (
        b"Subject: Your package has shipped\r\n"
        b"From: shop@test.com\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"Your tracking number is 1Z999AA10123456784\r\n"
    )

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(
            return_value=[{"uid": 1, "raw": raw_email}]
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("1"))

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()

        with caplog.at_level(
            logging.DEBUG, logger="custom_components.shop2parcel.imap_coordinator"
        ):
            await coord._async_update_data()

    debug_records = [
        r for r in caplog.records if "[Shop2Parcel DEBUG]" in r.message and r.levelname == "DEBUG"
    ]
    assert len(debug_records) >= 1, (
        f"Expected at least 1 [Shop2Parcel DEBUG] DEBUG record for IMAP, got 0. Records: {[r.message for r in caplog.records]}"
    )
    combined = " ".join(r.message for r in debug_records)
    assert "subject=" in combined, "Expected 'subject=' in IMAP [Shop2Parcel DEBUG] log"
    assert "from=" in combined, "Expected 'from=' in IMAP [Shop2Parcel DEBUG] log"
    assert "candidates=" in combined, "Expected 'candidates=' in IMAP [Shop2Parcel DEBUG] log"
    assert "outcome=" in combined, "Expected 'outcome=' in IMAP [Shop2Parcel DEBUG] log"


# ---------------------------------------------------------------------------
# DBG-06: persistent notification — async_create on debug poll; async_dismiss on normal init
# ---------------------------------------------------------------------------


async def test_dbg06_gmail_notification(hass, mock_config_entry):
    """DBG-06 Gmail: persistent_notification.async_create called after debug poll; async_dismiss called when debug_mode=False."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
    )

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification") as mock_pn,
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:X"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        # async_create called once after the debug poll
        assert mock_pn.async_create.call_count == 1, (
            f"Expected async_create call_count=1, got {mock_pn.async_create.call_count}"
        )
        create_kwargs = mock_pn.async_create.call_args[1]
        assert create_kwargs["notification_id"] == debug_mode_notification_id(
            mock_config_entry.entry_id
        ), f"Expected per-entry notification_id, got {create_kwargs['notification_id']!r}"
        assert create_kwargs["title"] == "Shop2Parcel Debug Mode", (
            f"Expected title='Shop2Parcel Debug Mode', got {create_kwargs['title']!r}"
        )
        assert "dry-run mode" in create_kwargs["message"], (
            f"Expected 'dry-run mode' in notification message, got {create_kwargs['message']!r}"
        )

        # Dismiss check: when debug_mode=False, __init__ should call async_dismiss
        # Reset mock to isolate the dismiss-on-init assertion
        mock_pn.async_dismiss.reset_mock()
        normal_entry = MockConfigEntry(
            domain=DOMAIN,
            data=mock_config_entry.data,
            options={CONF_DEBUG_MODE: False},
            unique_id="user2@gmail.com",
        )
        normal_entry.add_to_hass(hass)
        _coord_normal = GmailCoordinator(hass, normal_entry)
        assert mock_pn.async_dismiss.call_count == 1, (
            f"Expected async_dismiss call_count=1 when debug_mode=False, got {mock_pn.async_dismiss.call_count}"
        )
        dismiss_kwargs = mock_pn.async_dismiss.call_args[1]
        assert dismiss_kwargs["notification_id"] == debug_mode_notification_id(
            normal_entry.entry_id
        )


async def test_dbg06_imap_notification(hass, mock_imap_config_entry):
    """DBG-06 IMAP: async_create called after debug poll; async_dismiss on normal init."""
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            CONF_DEBUG_MODE: True,
        },
    )

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.imap_coordinator.persistent_notification") as mock_pn,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

        assert mock_pn.async_create.call_count == 1, (
            f"Expected async_create call_count=1, got {mock_pn.async_create.call_count}"
        )
        create_kwargs = mock_pn.async_create.call_args[1]
        assert create_kwargs["notification_id"] == debug_mode_notification_id(
            mock_imap_config_entry.entry_id
        ), f"Expected per-entry notification_id, got {create_kwargs['notification_id']!r}"
        assert create_kwargs["title"] == "Shop2Parcel Debug Mode"
        assert "dry-run mode" in create_kwargs["message"]

        # Dismiss-on-init: coordinator with debug_mode=False should dismiss
        mock_pn.async_dismiss.reset_mock()
        normal_imap_entry = MockConfigEntry(
            domain=DOMAIN,
            data=mock_imap_config_entry.data,
            options={
                "imap_search": 'SUBJECT "shipped"',
                "poll_interval": 30,
                CONF_DEBUG_MODE: False,
            },
            unique_id="user2@example.com@imap.example.com",
        )
        normal_imap_entry.add_to_hass(hass)
        _coord_normal = ImapCoordinator(hass, normal_imap_entry)
        assert mock_pn.async_dismiss.call_count == 1, (
            f"Expected async_dismiss call_count=1 when debug_mode=False, got {mock_pn.async_dismiss.call_count}"
        )
        dismiss_kwargs = mock_pn.async_dismiss.call_args[1]
        assert dismiss_kwargs["notification_id"] == debug_mode_notification_id(
            normal_imap_entry.entry_id
        )


# ---------------------------------------------------------------------------
# DBG-07: async_remove_entry — dismisses per-entry debug notification on removal
# ---------------------------------------------------------------------------


async def test_async_remove_entry_dismisses_debug_notification(hass, mock_config_entry):
    """DBG-07: async_remove_entry calls persistent_notification.async_dismiss with per-entry id.

    Phase 20 MRG-05: async_remove_entry now dismisses BOTH the debug-mode and
    Stage-2 cap notifications. This test verifies the debug-mode notification is
    still dismissed (call_count >= 1, with the correct notification_id in the calls).
    """
    from custom_components.shop2parcel import async_remove_entry

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_DEBUG_MODE: True},
    )

    with patch("homeassistant.components.persistent_notification.async_dismiss") as mock_dismiss:
        await async_remove_entry(hass, mock_config_entry)

    # Phase 20 + I2 fix: three dismiss calls (debug_mode + stage2_cap + stage2_failing).
    assert mock_dismiss.call_count == 3
    # The debug-mode notification must be one of the dismissed IDs.
    notification_ids = {call.kwargs["notification_id"] for call in mock_dismiss.call_args_list}
    assert debug_mode_notification_id(mock_config_entry.entry_id) in notification_ids
