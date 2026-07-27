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

from custom_components.shop2parcel.api.exceptions import GmailStaleTokenError
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
        # Simulate a subsequent (non-bootstrap) poll so the first-refresh skip does not apply.
        coord._first_refresh_done = True

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
        # Simulate a subsequent (non-bootstrap) poll so the first-refresh skip does not apply.
        coord._first_refresh_done = True

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

    # (c) the malformed TN must NOT be in the shared hub's dedup set.
    assert all("NOTATRACKINGNUM" not in str(k) for k in coord._hub._submitted_tracking_numbers), (
        "Malformed TN must not be written to the shared hub's dedup set"
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


# ---------------------------------------------------------------------------
# LOH-SUMMARY: Gmail Stage-1 inline POST description fallback
# ---------------------------------------------------------------------------


async def test_gmail_stage1_inline_post_description_falls_back_to_order_name(
    hass, mock_no_stage2_entry
):
    """LOH-SUMMARY: Gmail Stage-1 inline POST (no Stage-2 summary, raw ShipmentData).

    When order_summary is None (Stage-1-only path), description must fall back to
    order_name — the existing behavior is preserved at gmail_coordinator.py:775.
    """
    from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData

    mock_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    msg_id = "msg_loh_inline"
    mock_gmail = _make_stage1_hit_poll(msg_id, tn)

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

        # Stage-1 shipment — order_summary is None (default)
        stage1_shipment = ShipmentData(
            tracking_number=tn,
            carrier_name="UPS",
            order_name="#1234",
            message_id=msg_id,
            email_date=1700000000,
        )
        parse_result = ParseResult(
            shipment=stage1_shipment,
            skip_reason=None,
            strategy_used="html_template",
            keyword_hits={
                "tracking_regex": True,
                "order_regex": True,
                "carrier_regex": True,
            },
        )
        mock_parser_cls.return_value.parse.return_value = parse_result
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail

        await coord._async_update_data()

    # LOH-SUMMARY: order_summary is None → description falls back to order_name.
    mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
    call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args.kwargs
    assert call_kwargs["description"] == "#1234"


# ---------------------------------------------------------------------------
# IN-07: fallback prefetch cache + matched/found diagnostics
# ---------------------------------------------------------------------------


async def test_fallback_cache_hit_skips_extractor_and_cap(hass, mock_stage2_entry):
    """IN-07: a cap-deferred prefetched result cached by the worker must be reused by
    the gatekeeper on the re-fetch poll — no second Ollama call, no cap slot consumed,
    and the job is enqueued carrying the cached result."""
    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_cache_hit"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock()  # must never be awaited

    cached_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "#1001",
        },
        custom={},
        passes_used=1,
        latency_ms=10.0,
    )

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
        # Simulate a subsequent (non-bootstrap) poll so the first-refresh skip does not apply.
        coord._first_refresh_done = True
        # Simulate the worker's cap-skip having cached the prefetched result.
        coord._fallback_prefetch_cache[msg_id] = cached_result

        await coord._async_update_data()

    # No second Ollama pass; no cap slot / LLM attempt consumed.
    mock_extractor.async_extract.assert_not_awaited()
    assert coord._stage2_fallback_extractions_this_poll == 0
    assert coord._diagnostics.stage2_llm_attempts_total == 0
    # The cached result is consumed (popped) and handed to the enqueue.
    assert msg_id not in coord._fallback_prefetch_cache
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args.kwargs["prefetched_result"] is cached_result
    # IN-07 (a): fallback-found shipments count in the matched/found diagnostics.
    assert coord._diagnostics.emails_matched_total == 1
    assert coord._diagnostics.tracking_numbers_found_total == 1
    assert coord._diagnostics.last_poll_found[0]["tracking_number"] == "1Z999AA10123456784"


async def test_fallback_enqueue_counts_matched_found_diagnostics(hass, mock_stage2_entry):
    """IN-07 (a): a fallback-found shipment (fresh extraction path) must bump
    emails_matched / tracking_numbers_found / last_poll_found on enqueue."""
    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_fb_diag"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(
        return_value=Stage2Result(
            locked={
                "tracking_number": "1Z999AA10123456784",
                "carrier_name": "UPS",
                "order_name": "#1001",
            },
            custom={},
            passes_used=1,
            latency_ms=10.0,
        )
    )

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
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch.object(GmailCoordinator, "_enqueue_stage2", return_value=True),
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
        # Simulate a subsequent (non-bootstrap) poll so the first-refresh skip does not apply.
        coord._first_refresh_done = True

        await coord._async_update_data()

    assert coord._diagnostics.emails_matched_total == 1
    assert coord._diagnostics.last_poll_emails_matched == 1
    assert coord._diagnostics.tracking_numbers_found_total == 1
    found = coord._diagnostics.last_poll_found
    assert len(found) == 1
    assert found[0]["tracking_number"] == "1Z999AA10123456784"
    assert found[0]["message_id"] == msg_id


# ---------------------------------------------------------------------------
# Quick-260703-mac: first-refresh skip, subsequent-poll run, wall-clock budget
# ---------------------------------------------------------------------------


async def test_first_refresh_skips_inline_fallback(hass, mock_stage2_entry):
    """Quick-260703-mac T-mac-01: the bootstrap first refresh (before _first_refresh_done
    is set) must NOT await async_extract on a Stage-1-miss email. The message must remain
    un-marked (not in seen/inflight) so it is re-inspected on the next poll."""
    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_first_refresh_miss"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock()  # must never be awaited

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
            return_value="<html>first refresh body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
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
        # Confirm the flag starts False (bootstrap first refresh scenario).
        assert coord._first_refresh_done is False

        await coord._async_update_data()

    # async_extract must NEVER have been called on the first refresh.
    mock_extractor.async_extract.assert_not_awaited()
    # No LLM attempt counted.
    assert coord._diagnostics.stage2_llm_attempts_total == 0
    # Message must be left un-marked so the next poll re-inspects it.
    assert msg_id not in coord._seen_message_ids, (
        "Stage-1-miss must not be marked seen on first refresh"
    )
    assert msg_id not in coord._inflight_message_ids, (
        "Stage-1-miss must not be marked inflight on first refresh"
    )
    # Flag must be True after the first successful poll completes.
    assert coord._first_refresh_done is True


async def test_second_poll_runs_inline_fallback(hass, mock_stage2_entry):
    """Quick-260703-mac T-mac-01 (steady-state): after the first refresh sets
    _first_refresh_done = True, a second poll on the same Stage-1-miss email
    must await async_extract (inline fallback runs normally)."""
    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_second_poll_miss"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    # Return a gate-failing result so the extraction runs but does not enqueue
    # (keeps the test focused on whether async_extract is called, not on enqueue).
    from custom_components.shop2parcel.extractors.types import Stage2Result

    fallback_result = Stage2Result(
        locked={"tracking_number": "ORDER-SKIP", "carrier_name": "", "order_name": ""},
        custom={},
        passes_used=1,
        latency_ms=5.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=fallback_result)

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
            return_value="<html>second poll body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
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

        # First poll: skips extraction, sets _first_refresh_done = True.
        await coord._async_update_data()
        assert coord._first_refresh_done is True
        mock_extractor.async_extract.assert_not_awaited()

        # Second poll: the message is still un-marked so it re-appears; inline
        # fallback must now run (flag is True).
        mock_extractor.async_extract.reset_mock()
        await coord._async_update_data()

    # async_extract must have been awaited exactly once on the second poll
    # (single message; a double-extraction bug would make this fail).
    mock_extractor.async_extract.assert_awaited_once()


async def test_wall_clock_budget_stops_inline_fallback(hass, mock_stage2_entry):
    """Quick-260703-mac T-mac-02: when the per-poll monotonic deadline has already
    elapsed by the time the budget check runs, a Stage-1-miss email must NOT trigger
    async_extract and must be left un-marked (deferred to next poll).

    The budget is checked between extractions (a single in-flight call is not interrupted).
    Strategy: wrap _reset_stage2_poll_counters with a side-effect that forces the
    deadline to 0.0 (always in the past) after the normal reset runs, so that the
    budget check in the gatekeeper sees an exhausted deadline regardless of real time.
    """
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator

    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_budget_exhausted"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock()  # must never be awaited

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
            return_value="<html>budget body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
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
        # Skip the first-refresh guard so only the budget guard is under test.
        coord._first_refresh_done = True

        # Wrap _reset_stage2_poll_counters to force the deadline to epoch 0 (always past)
        # immediately after the normal reset sets it to time.monotonic() + 60.
        _orig_reset = Shop2ParcelCoordinator._reset_stage2_poll_counters

        def _reset_and_exhaust_budget(self_inner):
            _orig_reset(self_inner)
            self_inner._stage2_fallback_inline_deadline = 0.0  # force exhausted

        with patch.object(
            Shop2ParcelCoordinator,
            "_reset_stage2_poll_counters",
            _reset_and_exhaust_budget,
        ):
            await coord._async_update_data()

    # Budget was already exhausted at check time — no Ollama call must have run.
    mock_extractor.async_extract.assert_not_awaited()
    # Message must be left un-marked so it is deferred to the next poll.
    assert msg_id not in coord._seen_message_ids, (
        "Budget-deferred Stage-1-miss must not be marked seen"
    )
    assert msg_id not in coord._inflight_message_ids, (
        "Budget-deferred Stage-1-miss must not be marked inflight"
    )


# ---------------------------------------------------------------------------
# ollama-fallback-retry-loop: inline OllamaSchemaError poison-message quarantine
# ---------------------------------------------------------------------------


async def _run_inline_fallback_polls(
    hass,
    entry,
    *,
    msg_id: str,
    extract_side_effect,
    polls: int,
) -> GmailCoordinator:
    """Drive `polls` poll cycles on one Stage-1-miss message.

    async_extract uses `extract_side_effect` (an exception instance/class or callable).
    The message is left un-marked by the inline fallback failure paths, so — absent a
    quarantine — it re-appears in every poll's message list and is re-inferred each time.
    Returns the coordinator so the caller can assert on call counts / seen state.
    """
    entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(side_effect=extract_side_effect)

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
            return_value="<html>unparseable digest body</html>",
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_no_match()

        coord = GmailCoordinator(hass, entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._diagnostics.stage2_enabled = True
        coord._extractor = mock_extractor
        # Skip the bootstrap first-refresh guard so the inline fallback runs from poll 1.
        coord._first_refresh_done = True

        for _ in range(polls):
            await coord._async_update_data()

    coord._test_extractor = mock_extractor  # type: ignore[attr-defined]
    return coord


async def test_inline_schema_error_quarantines_after_threshold(hass, mock_stage2_entry):
    """ollama-fallback-retry-loop: a deterministically-unparseable Stage-1-miss email
    (async_extract raises OllamaSchemaError every time) must STOP being re-inferred once
    the per-message schema-failure count reaches STAGE2_MSG_QUARANTINE_THRESHOLD.

    Before the fix the inline fallback `continue`s without any per-message quarantine, so
    the same email is re-fetched and re-inferred on every poll forever (observed 93x live).
    """
    from custom_components.shop2parcel.api.exceptions import OllamaSchemaError
    from custom_components.shop2parcel.const import STAGE2_MSG_QUARANTINE_THRESHOLD

    threshold = STAGE2_MSG_QUARANTINE_THRESHOLD
    msg_id = "msg_usps_digest_unparseable"

    # Run one poll PAST the threshold so we can prove the (threshold+1)-th poll no longer
    # re-infers.
    coord = await _run_inline_fallback_polls(
        hass,
        mock_stage2_entry,
        msg_id=msg_id,
        extract_side_effect=OllamaSchemaError("No JSON object found in LLM response (len=518)"),
        polls=threshold + 3,
    )

    extractor = coord._test_extractor  # type: ignore[attr-defined]
    # async_extract must have run at most `threshold` times, then stopped — NOT once per poll.
    assert extractor.async_extract.await_count == threshold, (
        f"Expected exactly {threshold} inference attempts before quarantine, "
        f"got {extractor.async_extract.await_count} (infinite-loop regression?)"
    )
    # The message must now be terminal (marked seen) so the poll gate skips it.
    assert msg_id in coord._seen_message_ids, (
        "A quarantined schema-failing email must be marked seen so it stops being re-fetched"
    )


async def test_inline_transient_error_never_quarantines(hass, mock_stage2_entry):
    """ollama-fallback-retry-loop (design constraint / finding #594): a TRANSIENT Ollama
    outage (async_extract raises OllamaTransientError) must NEVER mark a message seen and
    must keep retrying every poll — a network blip must not permanently poison a legitimate
    shipment email.
    """
    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import STAGE2_MSG_QUARANTINE_THRESHOLD

    polls = STAGE2_MSG_QUARANTINE_THRESHOLD + 3
    msg_id = "msg_transient_outage"

    coord = await _run_inline_fallback_polls(
        hass,
        mock_stage2_entry,
        msg_id=msg_id,
        extract_side_effect=OllamaTransientError("connection refused"),
        polls=polls,
    )

    extractor = coord._test_extractor  # type: ignore[attr-defined]
    # A transient error must be retried on EVERY poll — one inference per poll, no quarantine.
    assert extractor.async_extract.await_count == polls, (
        f"Transient errors must keep retrying every poll: expected {polls} attempts, "
        f"got {extractor.async_extract.await_count}"
    )
    # The message must NEVER be marked seen (would permanently skip a legit email on restart-persist).
    assert msg_id not in coord._seen_message_ids, (
        "A transient Ollama outage must NOT mark the message seen (finding #594 regression)"
    )


# ---------------------------------------------------------------------------
# gmail-oauth-refresh-fields: stale-token 401 → force-refresh + retry-once (self-healing)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gmail_entry() -> MockConfigEntry:
    """MockConfigEntry with Stage-2 disabled (simplest poll path for the retry-wrapper tests)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "stale-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={},  # stage2 disabled
        unique_id="gmail-stale-token@test.com",
    )


def _setup_mock_oauth_with_impl(mock_oauth, implementation) -> None:
    """Configure the mocked config_entry_oauth2_flow so async_get_config_entry_implementation
    returns the supplied implementation (with a controllable async_refresh_token) and the
    OAuth2Session reports the stale access token that the poll starts with."""
    mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=implementation)
    mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
    mock_oauth.OAuth2Session.return_value.token = {
        "access_token": "stale-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_at": 9999999999.0,
    }


async def test_stale_token_force_refresh_and_retry_succeeds(hass, mock_gmail_entry):
    """A GmailStaleTokenError on async_list_messages triggers a FORCED token refresh and ONE
    retry of the same call, which succeeds — the poll completes (is NOT skipped) and the new
    token is persisted to the config entry. No ConfigEntryAuthFailed (no reauth)."""
    mock_gmail_entry.add_to_hass(hass)

    # List raises stale-token on the first call, then succeeds on the retry with the fresh token.
    mock_gmail = MagicMock()
    mock_gmail.async_list_messages = AsyncMock(
        side_effect=[
            GmailStaleTokenError("credentials do not contain the necessary fields"),
            ([], "subject:(tracking) after:123"),  # retry succeeds → empty result
        ]
    )
    mock_gmail.async_get_message = AsyncMock(return_value={})

    # Implementation.async_refresh_token returns a NEW token dict with a fresh access_token.
    implementation = MagicMock()
    implementation.async_refresh_token = AsyncMock(
        return_value={
            "access_token": "fresh-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
            "expires_in": 3599,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
    )

    with (
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.GmailClient",
            return_value=mock_gmail,
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        _setup_mock_oauth_with_impl(mock_oauth, implementation)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()

        coord = GmailCoordinator(hass, mock_gmail_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._first_refresh_done = True

        # Must NOT raise — the retry succeeds so the poll completes normally.
        result = await coord._async_update_data()

    # The poll succeeded (empty result dict, not an exception).
    assert result == {}
    # async_list_messages was called exactly twice: original (stale) + one retry.
    assert mock_gmail.async_list_messages.await_count == 2, (
        "Expected exactly one retry after the stale-token error "
        f"(got {mock_gmail.async_list_messages.await_count} calls)"
    )
    # A forced refresh happened exactly once.
    implementation.async_refresh_token.assert_awaited_once()
    # The retry used the FRESH access token (second call's first positional arg).
    retry_call = mock_gmail.async_list_messages.await_args_list[1]
    assert retry_call.args[0] == "fresh-access-token", (
        "The retried list call must use the force-refreshed access token"
    )
    # The new token was persisted to the config entry (async_ensure_token_valid still owns
    # the normal path, but the forced refresh writes the fresh token here).
    assert mock_gmail_entry.data["token"]["access_token"] == "fresh-access-token", (
        "The force-refreshed token must be persisted to the config entry data"
    )


async def test_stale_token_retry_also_fails_degrades_to_transient(hass, mock_gmail_entry):
    """If the retry ALSO raises GmailStaleTokenError, the poll degrades to the transient path
    (UpdateFailed, poll skipped, recovers next cycle) — NOT ConfigEntryAuthFailed (no reauth),
    and NOT an infinite retry loop (the refresh + retry each happen at most once)."""
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_gmail_entry.add_to_hass(hass)

    # List raises stale-token on BOTH the original call and the retry.
    mock_gmail = MagicMock()
    mock_gmail.async_list_messages = AsyncMock(
        side_effect=[
            GmailStaleTokenError("credentials do not contain the necessary fields"),
            GmailStaleTokenError("still stale after forced refresh"),
        ]
    )
    mock_gmail.async_get_message = AsyncMock(return_value={})

    implementation = MagicMock()
    implementation.async_refresh_token = AsyncMock(
        return_value={
            "access_token": "fresh-but-still-rejected-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
            "expires_in": 3599,
            "token_type": "Bearer",
        }
    )

    with (
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.GmailClient",
            return_value=mock_gmail,
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        _setup_mock_oauth_with_impl(mock_oauth, implementation)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()

        coord = GmailCoordinator(hass, mock_gmail_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._first_refresh_done = True

        # Degrades to the transient path (UpdateFailed), NOT reauth (ConfigEntryAuthFailed).
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()

    # Bounded: exactly two list calls (original + one retry), one forced refresh — no loop.
    assert mock_gmail.async_list_messages.await_count == 2, (
        "The retry must happen at most once — no infinite retry loop "
        f"(got {mock_gmail.async_list_messages.await_count} calls)"
    )
    implementation.async_refresh_token.assert_awaited_once()

    # Explicit guard: the failure must NOT be a reauth trigger.
    assert not isinstance(UpdateFailed, ConfigEntryAuthFailed)  # sanity: distinct exception types


# ---------------------------------------------------------------------------
# Phase 35 Plan 04 (MRG-05, SC-1 Pitfall 2): inline Stage-1-miss fallback
# grounding gate. The fb_shipment built directly from the Ollama fallback
# result (no merge_llm_authoritative call — Stage-1 is None here, Pattern 3)
# must gate order_name/order_summary through validate_grounding() using
# body-only prose (preprocess_html(html)) BEFORE construction, mirroring the
# production worker's grounding gate from Plan 35-03.
# ---------------------------------------------------------------------------

# Body-only prose containing no merchant-identity content tokens (no "target",
# "coffee", "customer", "care", etc.) — a claimed order_name/order_summary
# whose tokens are absent here must be dropped by the gate.
_INLINE_FALLBACK_NO_MERCHANT_HTML = (
    "<html><body><p>Hi there, your order has shipped via UPS. Tracking number: "
    "1Z999AA10123456784</p></body></html>"
)
# Body-only prose that DOES contain the "Target"/"Coffee"/"maker" content
# tokens — a claimed order_name/order_summary using those tokens must be kept.
_INLINE_FALLBACK_TARGET_HTML = (
    "<html><body><p>Your order from Target has shipped: Coffee maker included. "
    "Tracking number: 1Z999AA10123456784</p></body></html>"
)


async def test_inline_fallback_grounding_gate_discards_ungrounded_order_summary(
    hass, mock_stage2_entry
):
    """SC-1 Pitfall 2: the fallback result fabricates order_summary='Target - Coffee
    maker' but the body prose (preprocess_html(html)) contains none of its content
    tokens — the inline fallback must discard it (fb_shipment.order_summary is None)
    before fb_shipment is built, and record the rejection on the dedicated counter."""
    mock_stage2_entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll("msg_inline_ungrounded_summary")
    mock_extractor = AsyncMock()

    fb_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "",
            "order_summary": "Target - Coffee maker",
        },
        custom={},
        passes_used=1,
        latency_ms=9.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=fb_result)

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
            return_value=_INLINE_FALLBACK_NO_MERCHANT_HTML,
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
        coord._first_refresh_done = True

        await coord._async_update_data()

    assert mock_enqueue.called, "Expected _enqueue_stage2 to be called (tracking number is valid)"
    shipment_kwarg = mock_enqueue.call_args[1].get("shipment")
    assert shipment_kwarg is not None
    assert shipment_kwarg.order_summary is None, (
        f"order_summary must be dropped to None (got {shipment_kwarg.order_summary!r})"
    )
    assert coord._diagnostics.grounding_rejected_total == 1, (
        f"grounding_rejected_total must be 1 (was {coord._diagnostics.grounding_rejected_total})"
    )
    assert coord._diagnostics.last_grounding_rejected_value == "Target - Coffee maker"
    assert coord._diagnostics.last_grounding_rejected_reason == "ungrounded"
    # The dedicated grounding counter must never conflate with carrier-format rejections.
    assert coord._diagnostics.carrier_format_rejected_total == 0


async def test_inline_fallback_grounding_gate_sender_label_not_grounded(hass, mock_stage2_entry):
    """Spike 012 seed: the fallback result parrots a generic sender/subject-style
    label ('Customer Care') as order_name/order_summary. Since source_text is
    ALWAYS body-only prose (never sender/subject header context), 'customer'/'care'
    tokens are structurally absent from the gate's evidence — both fields must be
    dropped every time (order_name -> '', order_summary -> None)."""
    mock_stage2_entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll("msg_inline_sender_label")
    mock_extractor = AsyncMock()

    fb_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "Customer Care",
            "order_summary": "Customer Care - Your order #1234",
        },
        custom={},
        passes_used=1,
        latency_ms=7.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=fb_result)

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
            return_value=_INLINE_FALLBACK_NO_MERCHANT_HTML,
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
        coord._first_refresh_done = True

        await coord._async_update_data()

    assert mock_enqueue.called, "Expected _enqueue_stage2 to be called (tracking number is valid)"
    shipment_kwarg = mock_enqueue.call_args[1].get("shipment")
    assert shipment_kwarg is not None
    assert shipment_kwarg.order_name == "", (
        f"order_name must be dropped to '' (got {shipment_kwarg.order_name!r})"
    )
    assert shipment_kwarg.order_summary is None, (
        f"order_summary must be dropped to None (got {shipment_kwarg.order_summary!r})"
    )
    assert coord._diagnostics.grounding_rejected_total >= 1, (
        "grounding_rejected_total must increment for the sender-label fabrication "
        f"(was {coord._diagnostics.grounding_rejected_total})"
    )


async def test_inline_fallback_grounding_gate_keeps_grounded_order_fields(hass, mock_stage2_entry):
    """order_name/order_summary whose content tokens DO appear in the body prose
    must be kept intact, and grounding_rejected_total must stay 0."""
    mock_stage2_entry.add_to_hass(hass)
    mock_gmail = _make_stage1_miss_poll("msg_inline_grounded")
    mock_extractor = AsyncMock()

    fb_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "Target",
            "order_summary": "Target - Coffee maker",
        },
        custom={},
        passes_used=1,
        latency_ms=6.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=fb_result)

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
            return_value=_INLINE_FALLBACK_TARGET_HTML,
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
        coord._first_refresh_done = True

        await coord._async_update_data()

    assert mock_enqueue.called, "Expected _enqueue_stage2 to be called (tracking number is valid)"
    shipment_kwarg = mock_enqueue.call_args[1].get("shipment")
    assert shipment_kwarg is not None
    assert shipment_kwarg.order_name == "Target"
    assert shipment_kwarg.order_summary == "Target - Coffee maker"
    assert coord._diagnostics.grounding_rejected_total == 0


# ---------------------------------------------------------------------------
# Phase 31-05 (QUOTA-02): Gmail inline forward path <-> shared hub daily budget
# ---------------------------------------------------------------------------


async def test_gmail_inline_transient_error_refunds_reserved_slot(hass, mock_no_stage2_entry):
    """D-01: a transient inline POST failure refunds the daily-budget slot it reserved —
    the shared hub's used_today returns to its pre-poll value (reserve then refund nets
    to zero movement), and the message is left pending-retry (not dedup-marked).
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppTransientError

    mock_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    msg_id = "msg_transient_refund"
    mock_gmail = _make_stage1_hit_poll(msg_id, tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_hit(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("parcelapp.net 502")
        )

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        assert coord._hub.used_today == 0  # pre-poll baseline (conftest's shared test hub)

        await coord._async_update_data()

    # Reserved (try_consume) then refunded on the transient error — nets back to 0.
    assert coord._hub.used_today == 0, (
        f"Expected used_today back to 0 after refund, got {coord._hub.used_today}"
    )
    # Not dedup-marked — the message stays re-fetchable for retry.
    assert not coord._hub.is_submitted(tn)


async def test_gmail_inline_429_blocks_shared_hub(hass, mock_no_stage2_entry):
    """D-01/D-06: an inline 429 routes to hub.record_quota_exhausted (no refund) — the
    shared hub's quota_is_exhausted flips True, blocking ALL accounts, not just this one.
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppQuotaError

    mock_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    msg_id = "msg_429_blocks_all"
    mock_gmail = _make_stage1_hit_poll(msg_id, tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_hit(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppQuotaError("quota exhausted", reset_at=9999999999)
        )

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        assert coord._hub.quota_is_exhausted is False

        await coord._async_update_data()

    assert coord._hub.quota_is_exhausted is True
    assert coord._hub.quota_exhausted_until == 9999999999
    # No refund on 429 (D-01) — the reserved slot stays consumed.
    assert coord._hub.used_today == 1


async def test_gmail_inline_already_added_keeps_reserve(hass, mock_no_stage2_entry):
    """WR-01 (31-REVIEW.md / 31-VERIFICATION.md): the Gmail inline forward path's
    AlreadyAdded/InvalidTracking except block must NOT refund the reserve it took
    via hub.try_consume() immediately before the POST — the slot stays consumed
    (D-01: it genuinely occupied a daily-budget slot from parcelapp's point of
    view), mirroring the already-tested 429 (no-refund) and transient (refund)
    branches above.
    """
    from custom_components.shop2parcel.api.exceptions import ParcelAppAlreadyAddedError

    mock_no_stage2_entry.add_to_hass(hass)
    tn = "1Z999AA10123456784"
    msg_id = "msg_already_added_keeps_reserve"
    mock_gmail = _make_stage1_hit_poll(msg_id, tn)

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
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_hit(tn)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        coord = GmailCoordinator(hass, mock_no_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        assert coord._hub.used_today == 0  # pre-poll baseline (conftest's shared test hub)

        await coord._async_update_data()

    # Reserved (try_consume) then NOT refunded on AlreadyAdded (D-01) — used_today
    # stays at 1, not back at 0.
    assert coord._hub.used_today == 1, (
        f"Expected used_today to stay at 1 (no refund on AlreadyAdded), got {coord._hub.used_today}"
    )
    mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()


# ---------------------------------------------------------------------------
# Phase 33 Plan 01 (D-01 baseline gap): multi-shipment-digest characterization.
# ---------------------------------------------------------------------------


async def test_fallback_multi_shipment_digest(hass, mock_stage2_entry):
    """Closes the one identified D-01 baseline gap: a multi-shipment digest email
    (several tracking-number-shaped strings in one body) that Stage-1 fully misses
    must still yield exactly ONE fallback-extracted tracking number and exactly ONE
    `_enqueue_stage2` call.

    Assumption A2 (RESEARCH.md Pitfall 6): the inline Gmail fallback path is
    structurally single-shipment — `Stage2Result.locked` holds one
    `tracking_number`, and one `ShipmentData` is built and enqueued per message.
    This test pins that behavior; it does NOT attempt to make the fallback
    iterate multiple shipments per email (that would be new behavior, out of
    scope for this characterization-only plan, and would violate Prohibition P2).
    """
    mock_stage2_entry.add_to_hass(hass)
    msg_id = "msg_multi_shipment_digest"
    mock_gmail = _make_stage1_miss_poll(msg_id)
    mock_extractor = AsyncMock()

    # A realistic multi-shipment digest body: several tracking-number-shaped
    # strings for different carriers, all in one email. Stage-1 (EmailParser.parse)
    # is mocked to a genuine full miss below (shipment=None, no extra_shipments),
    # so only the Ollama fallback extracts anything from this body.
    digest_html = (
        "<html><body>"
        "<p>Your recent shipments:</p>"
        "<p>Package 1 - UPS 1Z999AA10123456784</p>"
        "<p>Package 2 - USPS 9400111122223333444455</p>"
        "<p>Package 3 - FedEx 999999999999</p>"
        "</body></html>"
    )

    # The extractor returns exactly ONE Stage2Result (structurally single-shipment
    # per Assumption A2) — one of the tracking numbers drawn from the digest body.
    digest_result = Stage2Result(
        locked={
            "tracking_number": "1Z999AA10123456784",
            "carrier_name": "UPS",
            "order_name": "",
        },
        custom={},
        passes_used=1,
        latency_ms=10.0,
    )
    mock_extractor.async_extract = AsyncMock(return_value=digest_result)

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
            return_value=digest_html,
        ),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch.object(GmailCoordinator, "_enqueue_stage2", return_value=True) as mock_enqueue,
    ):
        _setup_mock_oauth(mock_oauth)
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        # Genuine full Stage-1 miss: the parser populates neither `shipment` nor
        # `extra_shipments` even though the body contains multiple TN-shaped strings.
        mock_parser_cls.return_value.parse.return_value = _make_parse_result_no_match()

        coord = GmailCoordinator(hass, mock_stage2_entry)
        await coord._async_load_store()
        coord._email_client = mock_gmail
        coord._diagnostics.stage2_enabled = True
        coord._extractor = mock_extractor
        # Simulate a subsequent (non-bootstrap) poll so the first-refresh skip does not apply.
        coord._first_refresh_done = True

        await coord._async_update_data()

    # Exactly ONE Ollama extraction ran against the whole digest body.
    mock_extractor.async_extract.assert_awaited_once()

    # Exactly ONE _enqueue_stage2 call, carrying the single extracted tracking number.
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args
    first_pos_arg = call_args[0][0] if call_args[0] else call_args[1].get("normalized_tn")
    assert first_pos_arg == "1Z999AA10123456784", (
        f"Expected exactly one enqueue with tracking number '1Z999AA10123456784' "
        f"(got {first_pos_arg!r})"
    )
    shipment_kwarg = call_args[1].get("shipment")
    assert shipment_kwarg is not None
    assert shipment_kwarg.tracking_number == "1Z999AA10123456784"

    # Matched/found diagnostics reflect exactly one shipment found, not three.
    assert coord._diagnostics.tracking_numbers_found_total == 1
    assert len(coord._diagnostics.last_poll_found) == 1
