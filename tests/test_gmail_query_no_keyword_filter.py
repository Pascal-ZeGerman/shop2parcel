"""Regression test for debug session gmail-query-drops-emails (see
.planning/debug/gmail-query-drops-emails.md).

Root cause (confirmed via direct Gmail UI reproduction, not just code reading):
Gmail's search-engine query parser does not reliably return the full union of
results for a long chain of bare ``OR``-joined keyword terms. A real shipment
email (subject "Fwd: Pascal, I'm on my way !" from IZIMINI, forwarded via
web.de) plainly contained 4 of the 8 DEFAULT_GMAIL_QUERY keywords ("order",
"shipped" x2, "tracking", "shipment") as real indexable `<p>` text, and a bare
`shipped` Gmail search found it — but the full 8-term OR-chain query Shop2Parcel
builds for the Gmail List API call (with or without wrapping parentheses)
returned zero results for it. Because this filtering happens server-side
inside the Gmail List API call, a dropped message never reaches any of
Shop2Parcel's own code or diagnostics — a completely silent miss.

Fix contract: the base query GmailCoordinator hands to
GmailClient.async_list_messages() must no longer carry the keyword OR-chain —
only rescan_window_days, which build_incremental_query() (api/gmail_client.py,
unchanged, covered by tests/api/test_gmail_client.py) turns into a date-only
`after:` filter. Content filtering is the job of the existing local
EmailParser tiered pipeline (HTML template -> Tier 1 regex -> optional Tier 2
broad scan), which already runs on every message the poll fetches regardless
of how it was found.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.shop2parcel.const import CONF_GMAIL_QUERY, DEFAULT_GMAIL_QUERY
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator

# Keywords that make up DEFAULT_GMAIL_QUERY (the exact set proven to silently
# drop the IZIMINI shipment email when combined into a single OR-chain).
_KEYWORDS = (
    "tracking",
    "shipped",
    "shipment",
    "delivery",
    "delivered",
    "parcel",
    "package",
    "order",
)


async def test_gmail_poll_query_excludes_keyword_or_chain(hass, mock_config_entry):
    """The Gmail List API query built by a real poll cycle must not include the
    DEFAULT_GMAIL_QUERY keyword OR-chain — only the date window (`after:`) filter.

    RED: currently fails because GmailCoordinator._async_update_data_inner()
    passes the raw CONF_GMAIL_QUERY option string straight through to
    gmail.async_list_messages() as the `q` param for the List API call.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_GMAIL_QUERY: DEFAULT_GMAIL_QUERY},
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
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "after:0"))

        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()
        await coord._async_update_data()

    # async_list_messages(tok, query, rescan_window_days=...) — query is the
    # second positional arg. Note: GmailClient itself is fully mocked here, so
    # build_incremental_query() (which appends the `after:` filter inside the
    # real async_list_messages — see api/gmail_client.py) never executes in this
    # test; that composition is covered directly by tests/api/test_gmail_client.py.
    # This test's job is only to verify the *base* query the coordinator hands to
    # async_list_messages no longer carries the keyword OR-chain, and that
    # rescan_window_days is still forwarded so the date window continues to apply.
    call_args = mock_gmail_cls.return_value.async_list_messages.call_args
    sent_query = call_args[0][1]
    sent_rescan_window_days = call_args.kwargs.get("rescan_window_days")

    for keyword in _KEYWORDS:
        assert keyword not in sent_query, (
            f"Gmail List API query must not contain keyword '{keyword}' — Gmail's "
            "OR-chain search silently drops matching emails (see "
            ".planning/debug/gmail-query-drops-emails.md); content filtering must "
            f"happen locally in EmailParser after fetch, not server-side. Got query: {sent_query!r}"
        )
    assert " OR " not in sent_query, (
        f"Gmail List API query must not contain an OR-chain at all: {sent_query!r}"
    )
    assert sent_rescan_window_days == 30, (
        "Gmail List API call must still receive rescan_window_days so "
        "build_incremental_query() (api/gmail_client.py) can scope the query by date — "
        f"got rescan_window_days={sent_rescan_window_days!r}"
    )
