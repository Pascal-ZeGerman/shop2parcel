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

    # (c) the malformed TN must NOT be written to the shared hub's dedup set.
    assert all("NOTATRACKINGNUM" not in str(k) for k in coord._hub._submitted_tracking_numbers), (
        "Malformed TN must not be written to the shared hub's dedup set"
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


# ---------------------------------------------------------------------------
# CR-01: TLS certificate verification wiring — coordinator → ImapClient
# ---------------------------------------------------------------------------


async def test_imap_poll_passes_verify_tls_true_by_default(hass, mock_imap_no_stage2_entry):
    """CR-01: with no imap_verify_tls stored anywhere, the poll passes verify_tls=True."""
    mock_imap_no_stage2_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    call_kwargs = mock_imap_cls.return_value.fetch_shipping_emails.call_args.kwargs
    assert call_kwargs["verify_tls"] is True


async def test_imap_poll_options_verify_tls_overrides_data(hass):
    """CR-01: an options-flow imap_verify_tls=False overrides the entry.data value."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "imap",
            "imap_host": "imap.local",
            "imap_port": 993,
            "imap_username": "user@example.com",
            "imap_password": "app-password-here",
            "imap_tls": "ssl",
            "imap_verify_tls": True,
            "api_key": "test-parcelapp-key",
        },
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
            "imap_verify_tls": False,
        },
        unique_id="imap-verify-tls-test@example.com",
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        coord = ImapCoordinator(hass, entry)
        await coord._async_load_store()
        await coord._async_update_data()

    call_kwargs = mock_imap_cls.return_value.fetch_shipping_emails.call_args.kwargs
    assert call_kwargs["verify_tls"] is False


# ---------------------------------------------------------------------------
# IN-04: UIDVALIDITY-qualified storage keys
# ---------------------------------------------------------------------------


async def test_imap_storage_key_qualified_with_uidvalidity(hass, mock_imap_no_stage2_entry):
    """IN-04: when the client reports UIDVALIDITY, coordinator.data keys are
    '{uidvalidity}:{uid}' so a mailbox rebuild (which may reuse UIDs) cannot
    collide with previously persisted entries."""
    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = {
        "uid": 42,
        "raw": b"From: a@example.com\r\nSubject: shipped\r\n\r\n",
        "uidvalidity": 1748359721,
    }

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(
            "1Z999AA10123456784"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "1748359721:42" in data
    assert "42" not in data


async def test_imap_seen_gate_skips_reparse_of_terminal_messages(hass, mock_imap_no_stage2_entry):
    """WR-06: a message that reached a terminal decision (posted) is marked seen and
    its body is NOT re-parsed on the next poll — the IMAP path previously re-parsed
    the entire rescan window every poll."""
    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = _make_imap_message(uid=1, tracking_number="1Z999AA10123456784")

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(
            "1Z999AA10123456784"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()

        # Poll 1: message parsed and posted → terminal → marked seen.
        await coord._async_update_data()
        assert mock_parser_cls.return_value.parse.call_count == 1
        assert "1" in coord._seen_message_ids

        # Poll 2: the SAME window is returned again — the seen gate must skip
        # the body parse entirely.
        await coord._async_update_data()
        assert mock_parser_cls.return_value.parse.call_count == 1, (
            "seen message must not be re-parsed on the next poll"
        )
        # Still exactly one POST (dedup would guard anyway, but we never got there).
        mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()


async def test_imap_no_match_marked_seen_and_not_reparsed(hass, mock_imap_no_stage2_entry):
    """WR-06: a Stage-1 no-match is terminal on the IMAP path (no fallback exists) —
    marked seen so the body is not re-parsed every poll."""
    from custom_components.shop2parcel.api.email_parser import ParseResult  # noqa: PLC0415

    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = _make_imap_message(uid=9, tracking_number="unused")
    no_match = ParseResult(
        shipment=None,
        skip_reason="no_tracking_pattern",
        strategy_used=None,
        keyword_hits={
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        },
    )

    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch(
            "custom_components.shop2parcel.imap_coordinator.extract_html_body_imap",
            return_value="<html>marketing body</html>",
        ),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[raw_msg])
        mock_parser_cls.return_value.parse.return_value = no_match

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()

        await coord._async_update_data()
        assert "9" in coord._seen_message_ids
        assert mock_parser_cls.return_value.parse.call_count == 1

        await coord._async_update_data()
        assert mock_parser_cls.return_value.parse.call_count == 1


async def test_imap_transient_post_failure_stays_reparsable(hass, mock_imap_no_stage2_entry):
    """WR-06: a transient POST failure must NOT mark the message seen — the next
    poll re-parses and retries the forward."""
    from custom_components.shop2parcel.api.exceptions import (  # noqa: PLC0415
        ParcelAppTransientError,
    )

    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = _make_imap_message(uid=5, tracking_number="1Z999AA10123456784")

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(
            "1Z999AA10123456784"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("503")
        )

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()

        await coord._async_update_data()
        assert "5" not in coord._seen_message_ids
        assert "5" not in coord._inflight_message_ids

        # Second poll re-parses and retries the POST.
        await coord._async_update_data()
        assert mock_parser_cls.return_value.parse.call_count == 2
        assert mock_parcel_cls.return_value.async_add_delivery.await_count == 2


async def test_imap_storage_key_falls_back_to_bare_uid(hass, mock_imap_no_stage2_entry):
    """IN-04 backward compatibility: without a reported UIDVALIDITY (older client
    payloads, servers that omit it), the storage key stays the bare UID."""
    mock_imap_no_stage2_entry.add_to_hass(hass)

    raw_msg = {"uid": 42, "raw": b"From: a@example.com\r\nSubject: shipped\r\n\r\n"}

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(
            "1Z999AA10123456784"
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        data = await coord._async_update_data()

    assert "42" in data


# ---------------------------------------------------------------------------
# Phase 31-05 (QUOTA-02): IMAP inline forward path <-> shared hub daily budget
# ---------------------------------------------------------------------------


async def test_imap_inline_transient_error_refunds_reserved_slot(hass, mock_imap_no_stage2_entry):
    """D-01: a transient inline POST failure refunds the daily-budget slot it reserved —
    the shared hub's used_today returns to its pre-poll value (reserve then refund nets
    to zero movement), and the message is left re-parseable (not dedup-marked).
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppTransientError

    mock_imap_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    raw_msg = _make_imap_message(uid=7, tracking_number=tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("parcelapp.net 502")
        )

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        assert coord._hub.used_today == 0  # pre-poll baseline (conftest's shared test hub)

        await coord._async_update_data()

    # Reserved (try_consume) then refunded on the transient error — nets back to 0.
    assert coord._hub.used_today == 0, (
        f"Expected used_today back to 0 after refund, got {coord._hub.used_today}"
    )
    # Not dedup-marked — the message stays re-fetchable for retry.
    assert not coord._hub.is_submitted(tn)


async def test_imap_inline_429_blocks_shared_hub(hass, mock_imap_no_stage2_entry):
    """D-01/D-06: an inline 429 routes to hub.record_quota_exhausted (no refund) — the
    shared hub's quota_is_exhausted flips True, blocking ALL accounts, not just this one.
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppQuotaError

    mock_imap_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    raw_msg = _make_imap_message(uid=8, tracking_number=tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota exhausted", reset_at=9999999999)
        )

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        assert coord._hub.quota_is_exhausted is False

        await coord._async_update_data()

    assert coord._hub.quota_is_exhausted is True
    assert coord._hub.quota_exhausted_until == 9999999999
    # No refund on 429 (D-01) — the reserved slot stays consumed.
    assert coord._hub.used_today == 1


async def test_imap_inline_already_added_keeps_reserve(hass, mock_imap_no_stage2_entry):
    """WR-01 (31-REVIEW.md / 31-VERIFICATION.md): the IMAP inline forward path's
    AlreadyAdded/InvalidTracking except block must NOT refund the reserve it took
    via hub.try_consume() immediately before the POST — the slot stays consumed
    (D-01: it genuinely occupied a daily-budget slot from parcelapp's point of
    view), mirroring the already-tested 429 (no-refund) and transient (refund)
    branches above.
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppAlreadyAddedError

    mock_imap_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    raw_msg = _make_imap_message(uid=9, tracking_number=tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_imap_parse_result(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        coord = ImapCoordinator(hass, mock_imap_no_stage2_entry)
        await coord._async_load_store()
        assert coord._hub.used_today == 0  # pre-poll baseline (conftest's shared test hub)

        await coord._async_update_data()

    # Reserved (try_consume) then NOT refunded on AlreadyAdded (D-01) — used_today
    # stays at 1, not back at 0.
    assert coord._hub.used_today == 1, (
        f"Expected used_today to stay at 1 (no refund on AlreadyAdded), got {coord._hub.used_today}"
    )
    mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
