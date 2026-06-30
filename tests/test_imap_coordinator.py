"""Tests for ImapCoordinator carrier-format gate before the Stage-1 inline POST.

Phase 28 quick-260630-n3k Task 3 — symmetry with WR-02/WR-03:
- Gate-failing Stage-1 shipment (NOTATRACKINGNUM) must NOT trigger async_add_delivery,
  must increment carrier_format_rejected_total by 1 with reason 'no_carrier_match'.
- Stage-1 shipment with a strippable separator must POST the separator-free canonical form.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_imap_no_stage2_entry() -> MockConfigEntry:
    """IMAP MockConfigEntry with Stage-2 DISABLED (no ollama_url in options)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "imap",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_username": "user@example.com",
            "imap_password": "app-password-here",
            "imap_tls": "ssl",
            "api_key": "test-parcelapp-key",
        },
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            # no CONF_OLLAMA_URL → stage2_enabled stays False
        },
        unique_id="imap-gate-test@example.com",
    )


def _make_imap_message(uid: int, tracking_number: str, carrier_name: str = "UPS") -> dict:
    """Build a minimal raw IMAP message dict that the coordinator will parse.

    The coordinator calls extract_html_body_imap on the raw bytes and then runs
    EmailParser.parse() on the result.  We mock both of these below, so the
    raw bytes only need to be truthy enough to not trip the 'no_html_body' guard.
    """
    return {
        "uid": uid,
        "raw": b"From: noreply@shipping.example.com\r\nSubject: Your order has shipped\r\n\r\n",
    }


def _make_imap_parse_result(tracking_number: str, carrier_name: str = "UPS"):
    """Build a ParseResult with a Stage-1 shipment hit (for IMAP)."""
    from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData

    return ParseResult(
        shipment=ShipmentData(
            tracking_number=tracking_number,
            carrier_name=carrier_name,
            order_name="#imap-test",
            message_id="imap-uid-1",
            email_date=0,
        ),
        skip_reason=None,
        strategy_used="html_template",
        keyword_hits={
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        },
    )


# ---------------------------------------------------------------------------
# Task 3 RED: carrier-format gate before the IMAP Stage-1 inline POST.
# ---------------------------------------------------------------------------


async def test_imap_inline_rejects_malformed_tracking_no_post(
    hass, mock_imap_no_stage2_entry, caplog
):
    """WR-02/WR-03 symmetry RED: Stage-1 IMAP shipment with tracking_number that fails
    validate_carrier_format must NOT trigger async_add_delivery, must increment
    carrier_format_rejected_total by 1, and must NOT write to _submitted_tracking_numbers.

    RED: fails because there is currently no carrier-format gate before the IMAP inline
    POST — the malformed TN reaches async_add_delivery.
    """
    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = _make_imap_message(uid=1, tracking_number="NOTATRACKINGNUM")

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipping body</html>",
        ),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result("NOTATRACKINGNUM")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        # stage2 disabled by default (no ollama_url)

        with caplog.at_level(logging.DEBUG):
            await coord._async_update_data()

    # (a) async_add_delivery must NEVER be called.
    mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()

    # (b) rejection counter must have incremented by exactly 1.
    assert coord._diagnostics.carrier_format_rejected_total == 1, (
        f"Expected carrier_format_rejected_total=1, got "
        f"{coord._diagnostics.carrier_format_rejected_total}"
    )
    assert coord._diagnostics.last_carrier_format_rejected_reason == "no_carrier_match", (
        f"Expected reason='no_carrier_match', got "
        f"{coord._diagnostics.last_carrier_format_rejected_reason!r}"
    )

    # (c) the malformed TN must NOT be written to _submitted_tracking_numbers.
    assert all("NOTATRACKINGNUM" not in str(k) for k in coord._submitted_tracking_numbers), (
        "Malformed TN must not be written to _submitted_tracking_numbers"
    )

    # (d) rejected value must not appear in INFO+ logs (DEBUG-only per D-07/T-28-09).
    cleaned = "NOTATRACKINGNUM"
    info_plus = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert not any(cleaned in r.getMessage() for r in info_plus), (
        "Rejected value must not appear in INFO+ logs"
    )


async def test_imap_inline_posts_clean_canonical_form(hass, mock_imap_no_stage2_entry):
    """WR-02/WR-03 symmetry / D-03 RED: Stage-1 IMAP shipment with a strippable-separator
    valid TN must POST the clean canonical form (no spaces) to async_add_delivery.

    RED: fails because there is currently no gate to strip separators before POSTing —
    the raw spaced value reaches async_add_delivery.
    """
    mock_imap_no_stage2_entry.add_to_hass(hass)

    spaced_ups = "1Z 999AA1 0123456784"
    expected_clean = "1Z999AA10123456784"

    raw_msg = _make_imap_message(uid=2, tracking_number=spaced_ups)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>shipping body</html>",
        ),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(spaced_ups)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()

        await coord._async_update_data()

    # async_add_delivery must be called with the CLEAN form (no spaces).
    mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
    call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args[1]
    assert call_kwargs["tracking_number"] == expected_clean, (
        f"Expected tracking_number='{expected_clean}', got {call_kwargs['tracking_number']!r}"
    )
