"""Coordinator-level tests for the local keyword pre-filter and per-poll
message volume cap (D-01, D-02) — follow-up to gmail-query-drops-emails.

Uses the same full-poll harness pattern as tests/test_gmail_query_no_keyword_filter.py
and tests/test_debug_mode.py::test_dbg02_gmail_window_override (mocked
GmailClient / OAuth / Store / ParcelAppClient). EmailParser is also mocked —
assertions are made on parser.parse call counts and on diagnostics, not on
real parse output.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ParseResult
from custom_components.shop2parcel.const import CONF_DEBUG_MODE, CONF_GMAIL_QUERY
from custom_components.shop2parcel.gmail_coordinator import (
    _LOCAL_FILTER_SKIP_REASON,
    GmailCoordinator,
)

# Real IZIMINI body text (see .planning/debug/gmail-query-drops-emails.md) —
# contains 4 of the 8 DEFAULT_GMAIL_QUERY keywords ("order", "shipped" x2,
# "tracking", "shipment") — the regression anchor for the local filter.
_IZIMINI_BODY = (
    "<html><body><p>All items from your order 149164 have been shipped. "
    "The tracking number for these items is SBAAAAQLCQ6U4P269.</p></body></html>"
)
_NO_KEYWORD_BODY = "<html><body><p>Hello there, nothing relevant in here at all.</p></body></html>"


def _no_match_result() -> ParseResult:
    """A Stage-1-miss ParseResult — the harness's default EmailParser.parse() return."""
    return ParseResult(
        shipment=None,
        skip_reason="no_tracking_pattern",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _msg(
    msg_id: str, subject: str, html_content: str, internal_date_ms: str = "1700000000000"
) -> dict:
    """Build a Gmail message dict. ``html_content`` is read by the patched
    extract_html_body side effect below (real base64 body data is unused).
    """
    return {
        "id": msg_id,
        "internalDate": internal_date_ms,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "shop@example.com"},
            ],
            "mimeType": "text/html",
            "body": {"data": "PGh0bWw+PC9odG1sPg=="},
            "html_content": html_content,
        },
    }


def _extract_html_side_effect(payload: dict) -> str | None:
    return payload.get("html_content")


async def _run_poll(
    hass,
    entry: MockConfigEntry,
    messages: list[dict],
    *,
    options: dict | None = None,
    pre_poll=None,
) -> tuple[GmailCoordinator, MagicMock, MagicMock]:
    """Run one full GmailCoordinator poll with the given messages, fully mocked I/O.

    Returns (coord, mock_parser_cls, mock_gmail) so callers can assert on
    parser.parse.call_count, on fetched message IDs, and on coordinator state
    (diagnostics, seen/inflight caches).
    """
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
        if pre_poll is not None:
            pre_poll(coord)
        await coord._async_update_data()

    return coord, mock_parser_cls, mock_gmail


# ---------------------------------------------------------------------------
# D-01: local keyword filter behaviour (Tests 1-8)
# ---------------------------------------------------------------------------


async def test_no_keyword_match_skips_parse(hass, mock_config_entry) -> None:
    """Test 1: no default keyword in subject/body -> parser.parse never called."""
    messages = [_msg("msg_no_kw", "Newsletter", _NO_KEYWORD_BODY)]
    _coord, mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    mock_parser_cls.return_value.parse.assert_not_called()


async def test_no_keyword_match_records_skip_reason_and_scan_event(hass, mock_config_entry) -> None:
    """Test 2: the skip appends a last_poll_skip_reasons entry, increments
    last_poll_emails_scanned + emails_scanned_total, and emits a matching
    scan event outcome.
    """
    messages = [_msg("msg_no_kw2", "Newsletter", _NO_KEYWORD_BODY)]
    coord, _mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    d = coord._diagnostics
    assert d.last_poll_emails_scanned == 1
    assert d.emails_scanned_total == 1
    assert len(d.last_poll_skip_reasons) == 1
    skip_entry = d.last_poll_skip_reasons[0]
    assert skip_entry["message_id"] == "msg_no_kw2"
    assert skip_entry["reason"] == _LOCAL_FILTER_SKIP_REASON
    assert any(evt["outcome"] == _LOCAL_FILTER_SKIP_REASON for evt in d.scan_events)


async def test_izimini_body_passes_local_filter(hass, mock_config_entry) -> None:
    """Test 3: a message whose body contains a real keyword (IZIMINI regression
    text) IS passed to parser.parse and produces no local-filter skip reason.
    """
    messages = [_msg("msg_izimini", "Fwd: Pascal, I'm on my way !", _IZIMINI_BODY)]
    coord, mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    mock_parser_cls.return_value.parse.assert_called_once()
    d = coord._diagnostics
    assert not any(e.get("reason") == _LOCAL_FILTER_SKIP_REASON for e in d.last_poll_skip_reasons)


async def test_keyword_in_subject_only_passes(hass, mock_config_entry) -> None:
    """Test 4: a keyword present only in the subject (body has none) still passes."""
    messages = [_msg("msg_subj_kw", "Your package has shipped today", _NO_KEYWORD_BODY)]
    _coord, mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    mock_parser_cls.return_value.parse.assert_called_once()


async def test_filtered_message_marked_seen_not_inflight(hass, mock_config_entry) -> None:
    """Test 5: in non-debug mode a filtered-out message is marked via the
    persisted seen cache (not the in-flight cache).
    """
    messages = [_msg("msg_seen_check", "Newsletter", _NO_KEYWORD_BODY)]
    coord, _mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    assert "msg_seen_check" in coord._seen_message_ids
    assert "msg_seen_check" not in coord._inflight_message_ids


async def test_filtered_message_in_debug_mode_not_marked_seen(hass, mock_config_entry) -> None:
    """Test 6: in debug mode the same message is filtered (parse not called)
    but is NOT added to the persisted seen cache.
    """
    messages = [_msg("msg_debug_filter", "Newsletter", _NO_KEYWORD_BODY)]
    coord, mock_parser_cls, _mock_gmail = await _run_poll(
        hass, mock_config_entry, messages, options={CONF_DEBUG_MODE: True}
    )
    mock_parser_cls.return_value.parse.assert_not_called()
    assert "msg_debug_filter" not in coord._seen_message_ids
    assert "msg_debug_filter" not in coord._inflight_message_ids


async def test_operator_only_query_fails_open_with_warning(
    hass, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test 7: an operator-only stored gmail_query fails open — every message
    reaches parser.parse — and a WARNING names the dropped tokens.
    """
    messages = [_msg("msg_operator_only", "Newsletter", _NO_KEYWORD_BODY)]
    with caplog.at_level(logging.WARNING):
        _coord, mock_parser_cls, _mock_gmail = await _run_poll(
            hass,
            mock_config_entry,
            messages,
            options={CONF_GMAIL_QUERY: "from:shopify.com -label:spam"},
        )
    mock_parser_cls.return_value.parse.assert_called_once()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("from:shopify.com" in r.getMessage() for r in warnings), (
        f"Expected a WARNING naming the dropped operator token 'from:shopify.com': "
        f"{[r.getMessage() for r in warnings]}"
    )


async def test_dropped_token_warning_emitted_once_across_polls(
    hass, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test 8: the dropped-token warning fires at most once across two
    consecutive polls with the same stored query value (no per-poll spam).
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_GMAIL_QUERY: "from:shopify.com -label:spam"}
    )

    msg1 = _msg("msg_poll1", "Newsletter", _NO_KEYWORD_BODY)
    msg2 = _msg("msg_poll2", "Another newsletter", _NO_KEYWORD_BODY)
    messages_by_id = {msg1["id"]: msg1, msg2["id"]: msg2}

    mock_gmail = MagicMock()

    async def _get_message(_access_token, _msg_id):
        return messages_by_id[_msg_id]

    mock_gmail.async_get_message = AsyncMock(side_effect=_get_message)
    mock_gmail.async_list_messages = AsyncMock(
        side_effect=[
            ([{"id": "msg_poll1"}], "after:0"),
            ([{"id": "msg_poll2"}], "after:0"),
        ]
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

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

        with caplog.at_level(logging.WARNING):
            await coord._async_update_data()
            await coord._async_update_data()

    matching_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "from:shopify.com" in r.getMessage()
    ]
    assert len(matching_warnings) == 1, (
        f"Expected exactly one dropped-token warning across two polls with the "
        f"same stored query value, got {len(matching_warnings)}"
    )


# ---------------------------------------------------------------------------
# D-02: per-poll message volume cap (Tests 9-14)
# ---------------------------------------------------------------------------


async def test_cap_limits_fetch_to_100(hass, mock_config_entry) -> None:
    """Test 9: 150 post-seen-filter messages -> only 100 reach the fetch loop."""
    messages = [_msg(f"msg_{i}", "Order shipped", _IZIMINI_BODY) for i in range(150)]
    _coord, _mock_parser_cls, mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    assert mock_gmail.async_get_message.await_count == 100


async def test_cap_keeps_newest_front_slice(hass, mock_config_entry) -> None:
    """Test 10: the 100 processed are the FRONT of the list (newest-first) —
    the direction-reversal versus IMAP's [-N:] slice.
    """
    messages = [_msg(f"msg_{i}", "Order shipped", _IZIMINI_BODY) for i in range(150)]
    _coord, _mock_parser_cls, mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    fetched_ids = {call.args[1] for call in mock_gmail.async_get_message.await_args_list}
    expected_ids = {f"msg_{i}" for i in range(100)}
    assert fetched_ids == expected_ids
    assert "msg_149" not in fetched_ids


async def test_cap_applied_after_seen_filter(hass, mock_config_entry) -> None:
    """Test 11: the cap applies AFTER the seen/in-flight filter — seed 120
    already-seen IDs followed by 30 new ones; all 30 new messages are fetched
    (a before-filter cap would spend the whole budget on already-seen IDs and
    reach none).
    """
    seen_ids = [f"seen_{i}" for i in range(120)]
    new_ids = [f"new_{i}" for i in range(30)]
    all_ids = seen_ids + new_ids
    messages = [_msg(mid, "Order shipped", _IZIMINI_BODY) for mid in all_ids]

    def _seed_seen(coord: GmailCoordinator) -> None:
        for sid in seen_ids:
            coord._mark_message_seen(sid)

    _coord, _mock_parser_cls, mock_gmail = await _run_poll(
        hass, mock_config_entry, messages, pre_poll=_seed_seen
    )
    fetched_ids = {call.args[1] for call in mock_gmail.async_get_message.await_args_list}
    assert fetched_ids == set(new_ids), (
        f"Expected all 30 new messages fetched (cap applied AFTER the seen filter); "
        f"unexpected={fetched_ids - set(new_ids)} missing={set(new_ids) - fetched_ids}"
    )


async def test_cap_logs_warning_and_sets_diagnostics_counter(
    hass, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Test 12: capping logs a WARNING naming the actual count and the cap, and
    sets the diagnostics counter to the number of messages dropped.
    """
    messages = [_msg(f"msg_{i}", "Order shipped", _IZIMINI_BODY) for i in range(150)]
    with caplog.at_level(logging.WARNING):
        coord, _mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    assert coord._diagnostics.last_poll_emails_capped == 50
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("150" in r.getMessage() and "100" in r.getMessage() for r in warnings), (
        f"Expected a WARNING naming both the actual count (150) and the cap (100): "
        f"{[r.getMessage() for r in warnings]}"
    )


async def test_cap_applies_in_debug_mode(hass, mock_config_entry) -> None:
    """Test 13: the cap also applies in debug mode (where the seen filter is
    skipped and the window is forced to 365 days — the worst case it bounds).
    """
    messages = [_msg(f"msg_{i}", "Order shipped", _IZIMINI_BODY) for i in range(150)]
    coord, _mock_parser_cls, mock_gmail = await _run_poll(
        hass, mock_config_entry, messages, options={CONF_DEBUG_MODE: True}
    )
    assert mock_gmail.async_get_message.await_count == 100
    assert coord._diagnostics.last_poll_emails_capped == 50


async def test_cap_not_hit_leaves_counter_zero(hass, mock_config_entry) -> None:
    """Test 14: a poll returning fewer than the cap leaves the diagnostics
    counter at 0.
    """
    messages = [_msg("msg_only_one", "Order shipped", _IZIMINI_BODY)]
    coord, _mock_parser_cls, _mock_gmail = await _run_poll(hass, mock_config_entry, messages)
    assert coord._diagnostics.last_poll_emails_capped == 0
