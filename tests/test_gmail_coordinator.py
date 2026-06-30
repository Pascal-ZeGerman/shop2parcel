"""Tests for GmailCoordinator strict carrier-format gate on the inline Ollama fallback path.

Phase 28 Plan 04 — R1/R2/R3:
- Gate-failing fallback result (ORDER-12345) must NOT be enqueued and must increment the
  carrier_format_rejected_total counter by exactly 1 (R1/R3).
- Spaced USPS fallback result must be enqueued using the separator-free canonical form (R2/D-03).
- Rejected values must only appear in DEBUG log, never INFO or above (R3/D-07/T-28-09).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.const import (
    CONF_CUSTOM_FIELDS,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT,
    CONF_OLLAMA_URL,
    CONF_QUEUE_MAXLEN,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_QUEUE_MAXLEN,
    DOMAIN,
)
from custom_components.shop2parcel.extractors.types import Stage2Result
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator


@pytest.fixture
def mock_stage2_entry() -> MockConfigEntry:
    """MockConfigEntry with Stage-2 (Ollama) enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            CONF_CUSTOM_FIELDS: [],
        },
        unique_id="gmail-strict-gate@test.com",
    )


def _make_stage1_miss_poll(msg_id: str = "msg_miss") -> MagicMock:
    """Build a mock GmailClient returning a Stage-1-miss message."""
    mock_gmail = MagicMock()
    mock_gmail.async_list_messages = AsyncMock(
        return_value=([{"id": msg_id}], "subject:(tracking)")
    )
    mock_gmail.async_get_message = AsyncMock(
        return_value={
            "id": msg_id,
            "internalDate": "1700000000000",
            "payload": {"mimeType": "text/html", "body": {"data": "PGh0bWw+PC9odG1sPg=="}},
        }
    )
    return mock_gmail


def _setup_mock_oauth(mock_oauth) -> None:
    """Configure mock OAuth session with a valid token."""
    mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
    mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
    mock_oauth.OAuth2Session.return_value.token = {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_at": 9999999999.0,
    }


def _make_parse_result_no_match():
    """Build a ParseResult for Stage-1 miss (no tracking found by HTML parser)."""
    from custom_components.shop2parcel.api.email_parser import ParseResult

    return ParseResult(
        shipment=None,
        skip_reason="no_match",
        strategy_used=None,
        keyword_hits={
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        },
    )


# ---------------------------------------------------------------------------
# Task 1 (RED): gate-failing fallback result — no enqueue, counter +1, DEBUG-only
# ---------------------------------------------------------------------------


async def test_fallback_gate_reject_order_number_no_enqueue(hass, mock_stage2_entry, caplog):
    """R1/R3: Ollama fallback returns 'ORDER-12345' (gate-failing) -> no _enqueue_stage2 call,
    carrier_format_rejected_total increments by exactly 1, value appears only in DEBUG.

    RED: fails because the current path uses _SANITY_RE which ACCEPTS 'ORDER-12345'
    (alphanumeric + hyphen, 12 chars, within 6-40), so enqueue IS called and the counter
    does not exist / does not increment.
    """
    mock_stage2_entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll("msg_gate_reject")
    mock_extractor = AsyncMock()

    # Ollama fallback returns a gate-failing tracking number (order reference, not carrier format)
    order_result = Stage2Result(
        locked={
            "tracking_number": "ORDER-12345",
            "carrier_name": "UPS",
            "order_name": "#1001",
        },
        custom={},
        passes_used=1,
        latency_ms=10.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=order_result)

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
            return_value="<html>order confirm body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch.object(GmailCoordinator, "_enqueue_stage2") as mock_enqueue,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_no_match()

        coord = GmailCoordinator(hass, mock_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._diagnostics.stage2_enabled = True
        coord._extractor = mock_extractor

        with caplog.at_level(logging.DEBUG):
            await coord._async_update_data()

    # R1: gate-failing result MUST NOT be enqueued
    mock_enqueue.assert_not_called()

    # R3: rejection counter must have incremented by exactly 1
    assert coord._diagnostics.carrier_format_rejected_total == 1, (
        "carrier_format_rejected_total must be 1 after gate rejection of ORDER-12345 "
        f"on the Gmail inline fallback path (was {coord._diagnostics.carrier_format_rejected_total})"
    )

    # R3/D-07: last rejected value must be the cleaned form (hyphens stripped, uppercased)
    assert coord._diagnostics.last_carrier_format_rejected_value == "ORDER12345", (
        "last_carrier_format_rejected_value must be the cleaned form 'ORDER12345' "
        f"(was {coord._diagnostics.last_carrier_format_rejected_value!r})"
    )

    # D-07/T-28-09: the rejected value must NOT appear in INFO+ log records
    rejected_clean = "ORDER12345"
    info_plus = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert not any(rejected_clean in r.getMessage() for r in info_plus), (
        f"Rejected value '{rejected_clean}' must not appear in INFO+ logs (DEBUG only): "
        f"{[r.getMessage() for r in info_plus if rejected_clean in r.getMessage()]}"
    )

    # D-07: the rejected value MUST appear in at least one DEBUG record
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(rejected_clean in r.getMessage() for r in debug_records), (
        f"Rejected value '{rejected_clean}' must appear in at least one DEBUG log record"
    )


async def test_fallback_gate_pass_spaced_usps_uses_clean_form(hass, mock_stage2_entry):
    """R2/D-03: Ollama fallback returns spaced USPS number -> enqueued; the value passed to
    _enqueue_stage2 is the separator-free canonical form (gate clean output), not the original
    spaced string.

    RED: fails because the current path calls normalize_tracking_number(tn) (str.strip().upper())
    which does NOT strip spaces, so the value passed to _enqueue_stage2 still contains spaces.
    After GREEN, validate_carrier_format strips spaces and the clean form drives dedup + enqueue.
    """
    mock_stage2_entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll("msg_spaced_usps")
    mock_extractor = AsyncMock()

    # Spaced USPS tracking number — internal spaces are valid input from Ollama
    spaced_usps = "9400 1111 2222 3333 4444 55"
    usps_result = Stage2Result(
        locked={
            "tracking_number": spaced_usps,
            "carrier_name": "USPS",
            "order_name": "#2002",
        },
        custom={},
        passes_used=1,
        latency_ms=8.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=usps_result)

    # Expected canonical form: spaces stripped, uppercased
    expected_clean = "9400111122223333444455"

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
            return_value="<html>usps body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch.object(GmailCoordinator, "_enqueue_stage2", return_value=True) as mock_enqueue,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_no_match()

        coord = GmailCoordinator(hass, mock_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._diagnostics.stage2_enabled = True
        coord._extractor = mock_extractor

        await coord._async_update_data()

    # R2: enqueue MUST be called (spaced USPS passes the strict gate)
    assert mock_enqueue.called, (
        "Expected _enqueue_stage2 to be called for spaced USPS tracking number"
    )

    # D-03: the first positional arg (normalized_tn / storage key for dedup) must be
    # the clean separator-free form, not the spaced original
    call_kwargs = mock_enqueue.call_args
    # _enqueue_stage2(normalized_fb, storage_key=msg_id, shipment=..., ...)
    # The first positional arg is normalized_tn used for dedup.
    first_pos_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("normalized_tn")
    assert first_pos_arg == expected_clean, (
        f"_enqueue_stage2 must receive clean form '{expected_clean}' as first arg "
        f"(got {first_pos_arg!r}) — gate clean output must replace normalize_tracking_number(tn)"
    )

    # D-03: ShipmentData.tracking_number built for the fallback must also use clean form
    # (check via the shipment kwarg passed to _enqueue_stage2)
    shipment_kwarg = call_kwargs[1].get("shipment")
    if shipment_kwarg is not None:
        assert shipment_kwarg.tracking_number == expected_clean, (
            f"ShipmentData.tracking_number must be clean form '{expected_clean}' "
            f"(got {shipment_kwarg.tracking_number!r})"
        )

    # No rejection counter increment (gate pass)
    assert coord._diagnostics.carrier_format_rejected_total == 0, (
        "carrier_format_rejected_total must remain 0 for a valid USPS tracking number"
    )


# ---------------------------------------------------------------------------
# Phase 28 quick-260630-n3k Task 2 (WR-03): carrier-format gate before the
# Gmail Stage-1 inline POST.
# ---------------------------------------------------------------------------


def _make_stage1_hit_poll(
    msg_id: str,
    tracking_number: str,
    carrier_name: str = "UPS",
) -> MagicMock:
    """Build a mock GmailClient returning a Stage-1-hit message with the given TN."""
    mock_gmail = MagicMock()
    mock_gmail.async_list_messages = AsyncMock(
        return_value=([{"id": msg_id}], "subject:(tracking)")
    )
    mock_gmail.async_get_message = AsyncMock(
        return_value={
            "id": msg_id,
            "internalDate": "1700000000000",
            "payload": {"mimeType": "text/html", "body": {"data": "PGh0bWw+PC9odG1sPg=="}},
        }
    )
    return mock_gmail


def _make_parse_result_hit(tracking_number: str, carrier_name: str = "UPS"):
    """Build a ParseResult with a Stage-1 shipment hit."""
    from custom_components.shop2parcel.api.email_parser import ParseResult
    from custom_components.shop2parcel.api.email_parser import ShipmentData as SD

    return ParseResult(
        shipment=SD(
            tracking_number=tracking_number,
            carrier_name=carrier_name,
            order_name="#test",
            message_id="msg_hit",
            email_date=1700000000,
        ),
        skip_reason=None,
        strategy_used="html_template",
        keyword_hits={
            "tracking_regex": False,
            "order_regex": False,
            "carrier_regex": False,
        },
    )


@pytest.fixture
def mock_no_stage2_entry() -> MockConfigEntry:
    """MockConfigEntry with Stage-2 DISABLED (no ollama_url in options)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={},  # no CONF_OLLAMA_URL → stage2_enabled stays False
        unique_id="gmail-inline-gate@test.com",
    )


async def test_gmail_inline_rejects_malformed_tracking_no_post(hass, mock_no_stage2_entry, caplog):
    """WR-03 RED: Stage-1 shipment with tracking_number that fails validate_carrier_format
    must NOT trigger async_add_delivery, must increment carrier_format_rejected_total by 1,
    and must NOT write the value to _submitted_tracking_numbers.

    RED: fails because there is currently no carrier-format gate before the inline POST —
    the malformed TN reaches async_add_delivery.
    """
    mock_no_stage2_entry.add_to_hass(hass)
    msg_id = "msg_wr03_reject"
    mock_gmail = _make_stage1_hit_poll(msg_id, "NOTATRACKINGNUM")

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
            return_value="<html>shipping body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_hit("NOTATRACKINGNUM")
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        # stage2 disabled (default, no ollama_url set)

        with caplog.at_level(logging.DEBUG):
            await coord._async_update_data()

    # (a) async_add_delivery must NEVER be called.
    mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()

    # (b) rejection counter must have incremented by exactly 1.
    assert coord._diagnostics.carrier_format_rejected_total == 1, (
        f"Expected carrier_format_rejected_total=1, got "
        f"{coord._diagnostics.carrier_format_rejected_total}"
    )
    assert coord._diagnostics.last_carrier_format_rejected_reason == "no_carrier_match"

    # (c) the malformed TN must NOT be in _submitted_tracking_numbers.
    assert all("NOTATRACKINGNUM" not in str(k) for k in coord._submitted_tracking_numbers), (
        "Malformed TN must not be written to _submitted_tracking_numbers"
    )

    # (d) DEBUG-only: the cleaned value must not appear in INFO+ logs.
    cleaned = "NOTATRACKINGNUM"
    info_plus = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert not any(cleaned in r.getMessage() for r in info_plus), (
        "Rejected value must not appear in INFO+ logs"
    )


async def test_gmail_inline_posts_clean_canonical_form(hass, mock_no_stage2_entry):
    """WR-03 / D-03 RED: Stage-1 shipment with a strippable-separator valid TN must POST
    the clean canonical form (no spaces) to async_add_delivery.

    RED: fails because there is currently no gate to strip the separator before POSTing —
    the raw spaced value is passed to async_add_delivery.
    """
    mock_no_stage2_entry.add_to_hass(hass)
    spaced_ups = "1Z 999AA1 0123456784"
    expected_clean = "1Z999AA10123456784"
    msg_id = "msg_wr03_clean"
    mock_gmail = _make_stage1_hit_poll(msg_id, spaced_ups)

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
            return_value="<html>shipping body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_hit(spaced_ups)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail

        await coord._async_update_data()

    # async_add_delivery must be called with the CLEAN form (no spaces).
    mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
    call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args[1]
    assert call_kwargs["tracking_number"] == expected_clean, (
        f"Expected tracking_number='{expected_clean}', got {call_kwargs['tracking_number']!r}"
    )
