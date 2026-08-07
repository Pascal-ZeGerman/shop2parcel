"""Coordinator-level tests for the sender-exclusion gate (Task 3, spike 027).

Gmail poll-driving harness copied from tests/test_gmail_local_keyword_filter.py;
IMAP poll-driving harness copied from tests/test_imap_coordinator.py. EmailParser
is mocked on both paths — assertions are made on parser.parse call counts and on
diagnostics/skip-reason/scan-event/seen-vs-inflight state, not on real parse output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ParseResult
from custom_components.shop2parcel.const import CONF_SENDER_EXCLUSIONS
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator

_BODY = "<html><body><p>Some shipment-shaped body content.</p></body></html>"


def _no_match_result() -> ParseResult:
    """A Stage-1-miss ParseResult — the harness's default EmailParser.parse() return."""
    return ParseResult(
        shipment=None,
        skip_reason="no_tracking_pattern",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


# ---------------------------------------------------------------------------
# Gmail harness (mirrors tests/test_gmail_local_keyword_filter.py)
# ---------------------------------------------------------------------------


def _gmail_msg(msg_id: str, sender: str, subject: str = "Order shipped") -> dict:
    """Build a Gmail message dict with a configurable From header."""
    return {
        "id": msg_id,
        "internalDate": "1700000000000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "text/html",
            "body": {"data": "PGh0bWw+PC9odG1sPg=="},
            "html_content": _BODY,
        },
    }


def _extract_html_side_effect(payload: dict) -> str | None:
    return payload.get("html_content")


async def _run_gmail_poll(
    hass,
    entry: MockConfigEntry,
    messages: list[dict],
    *,
    options: dict | None = None,
) -> tuple[GmailCoordinator, MagicMock]:
    """Run one full GmailCoordinator poll with the given messages, fully mocked I/O."""
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)

    mock_gmail = MagicMock()
    msg_ids = [m["id"] for m in messages]
    messages_by_id = {m["id"]: m for m in messages}
    mock_gmail.async_list_messages = AsyncMock(
        return_value=([{"id": mid} for mid in msg_ids], "after:0")
    )

    async def _get_message(_access_token, _msg_id):
        return messages_by_id[_msg_id]

    mock_gmail.async_get_message = AsyncMock(side_effect=_get_message)

    with (
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.GmailClient",
            return_value=mock_gmail,
        ),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            side_effect=_extract_html_side_effect,
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.persistent_notification"),
    ):
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _no_match_result()

        coord = GmailCoordinator(hass, entry)
        await coord._async_load_store()
        await coord._async_update_data()

    return coord, mock_parser_cls


async def test_gmail_excluded_sender_skips_parser(hass, mock_config_entry) -> None:
    """A message From an excluded domain never reaches EmailParser.parse."""
    messages = [_gmail_msg("msg_excl", "Digest <digest@substack.com>")]
    _coord, mock_parser_cls = await _run_gmail_poll(
        hass, mock_config_entry, messages, options={CONF_SENDER_EXCLUSIONS: ["substack.com"]}
    )
    mock_parser_cls.return_value.parse.assert_not_called()


async def test_gmail_excluded_sender_records_reason_and_scan_event(hass, mock_config_entry) -> None:
    """The skip appends a last_poll_skip_reasons entry and emits a matching scan event."""
    messages = [_gmail_msg("msg_excl2", "Digest <digest@substack.com>")]
    coord, _mock_parser_cls = await _run_gmail_poll(
        hass, mock_config_entry, messages, options={CONF_SENDER_EXCLUSIONS: ["substack.com"]}
    )
    d = coord._diagnostics
    assert d.last_poll_emails_scanned == 1
    assert d.emails_scanned_total == 1
    assert len(d.last_poll_skip_reasons) == 1
    skip_entry = d.last_poll_skip_reasons[0]
    assert skip_entry["message_id"] == "msg_excl2"
    assert skip_entry["reason"] == "sender_excluded"
    assert any(evt["outcome"] == "sender_excluded" for evt in d.scan_events)


async def test_gmail_excluded_sender_marked_inflight_not_seen(hass, mock_config_entry) -> None:
    """D-05: the exclusion gate uses the reversible in-flight cache, never the
    persisted seen cache.
    """
    messages = [_gmail_msg("msg_excl3", "Digest <digest@substack.com>")]
    coord, _mock_parser_cls = await _run_gmail_poll(
        hass, mock_config_entry, messages, options={CONF_SENDER_EXCLUSIONS: ["substack.com"]}
    )
    assert "msg_excl3" in coord._inflight_message_ids
    assert "msg_excl3" not in coord._seen_message_ids


async def test_gmail_exclusion_is_per_message_not_per_poll(hass, mock_config_entry) -> None:
    """A second, non-excluded message in the SAME poll still reaches the parser."""
    messages = [
        _gmail_msg("msg_excl4", "Digest <digest@substack.com>"),
        _gmail_msg("msg_ok4", "Shopify <no-reply@shopify.com>"),
    ]
    _coord, mock_parser_cls = await _run_gmail_poll(
        hass, mock_config_entry, messages, options={CONF_SENDER_EXCLUSIONS: ["substack.com"]}
    )
    assert mock_parser_cls.return_value.parse.call_count == 1


async def test_gmail_usps_informed_delivery_guard_end_to_end(hass, mock_config_entry) -> None:
    """MANDATORY guard (end-to-end restatement of the Task 1 unit guard): a
    'usps.com' exclusion entry must NOT exclude the real Informed Delivery
    sender — the message must still reach the parser.
    """
    messages = [
        _gmail_msg(
            "msg_usps",
            "USPS <USPSInformeddelivery@email.informeddelivery.usps.com>",
        )
    ]
    _coord, mock_parser_cls = await _run_gmail_poll(
        hass, mock_config_entry, messages, options={CONF_SENDER_EXCLUSIONS: ["usps.com"]}
    )
    mock_parser_cls.return_value.parse.assert_called_once()


async def test_gmail_no_exclusions_configured_all_reach_parser(hass, mock_config_entry) -> None:
    """D-03 fail-open: with no stored exclusions, every message still reaches
    the parser (byte-for-byte-identical behaviour to before this feature).
    """
    messages = [_gmail_msg("msg_noexcl", "Digest <digest@substack.com>")]
    _coord, mock_parser_cls = await _run_gmail_poll(hass, mock_config_entry, messages)
    mock_parser_cls.return_value.parse.assert_called_once()


# ---------------------------------------------------------------------------
# IMAP harness (mirrors tests/test_imap_coordinator.py)
# ---------------------------------------------------------------------------


def _imap_msg(uid: int, sender: str, subject: str = "Order shipped") -> dict:
    """Build a minimal raw IMAP message dict with a configurable From header."""
    raw = f"From: {sender}\r\nSubject: {subject}\r\n\r\n".encode()
    return {"uid": uid, "raw": raw}


async def _run_imap_poll(
    hass,
    entry: MockConfigEntry,
    messages: list[dict],
    *,
    options: dict | None = None,
) -> tuple[ImapCoordinator, MagicMock]:
    """Run one full ImapCoordinator poll with the given raw messages, fully mocked I/O."""
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(entry, options=options)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value=_BODY,
        ),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=messages)
        mock_parser_cls.return_value.parse.return_value = _no_match_result()

        coord = ImapCoordinator(hass, entry)
        await coord._async_load_store()
        await coord._async_update_data()

    return coord, mock_parser_cls


async def test_imap_excluded_sender_skips_parser_and_records_state(
    hass, mock_imap_config_entry
) -> None:
    """A raw message From an excluded domain never reaches EmailParser.parse,
    is marked in-flight (not seen), and records a sender_excluded skip reason.
    """
    messages = [_imap_msg(1, "digest@substack.com")]
    coord, mock_parser_cls = await _run_imap_poll(
        hass,
        mock_imap_config_entry,
        messages,
        options={CONF_SENDER_EXCLUSIONS: ["substack.com"]},
    )

    mock_parser_cls.return_value.parse.assert_not_called()
    assert "1" in coord._inflight_message_ids
    assert "1" not in coord._seen_message_ids
    d = coord._diagnostics
    assert len(d.last_poll_skip_reasons) == 1
    assert d.last_poll_skip_reasons[0]["reason"] == "sender_excluded"
    assert any(evt["outcome"] == "sender_excluded" for evt in d.scan_events)


async def test_imap_no_exclusions_configured_unchanged_behavior(
    hass, mock_imap_config_entry
) -> None:
    """D-03 fail-open: with no stored exclusions, the message still reaches the parser."""
    messages = [_imap_msg(2, "digest@substack.com")]
    _coord, mock_parser_cls = await _run_imap_poll(hass, mock_imap_config_entry, messages)
    mock_parser_cls.return_value.parse.assert_called_once()
