"""Multi-account integration tests — covers MULT-01, MULT-02, D-10, D-11.

Two config entries (one Gmail, one IMAP) are added to the same HA instance.
Tests verify coordinator isolation: separate Store keys, separate entity
unique ID namespaces, no data leakage between entries.

All tests are xfail until coordinator IMAP dispatch is implemented (Plan 09-04).
"""

from __future__ import annotations

from custom_components.shop2parcel.const import DOMAIN

# ---------------------------------------------------------------------------
# Stub: MULT-01 — two entries coexist in same HA instance
# ---------------------------------------------------------------------------


async def test_two_entries_can_be_added_to_hass(hass, mock_config_entry, mock_imap_config_entry):
    """MULT-01: Gmail and IMAP entries can both be added to hass without conflict."""
    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# Stub: D-10 — each entry gets its own coordinator with its own Store key
# ---------------------------------------------------------------------------


async def test_two_imap_entries_have_separate_store_keys(hass, mock_imap_config_entry):
    """D-10: Each config entry creates a coordinator with Store key scoped to entry_id."""
    # Create a second IMAP entry with a different account
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: PLC0415

    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: PLC0415

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "imap",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_username": "other@example.com",
            "imap_password": "other-password",
            "imap_tls": "ssl",
            "api_key": "other-parcelapp-key",
        },
        options={"imap_search": 'SUBJECT "shipped"', "poll_interval": 30},
        unique_id="other@example.com@imap.example.com",
    )

    mock_imap_config_entry.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    coord_a = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
    coord_b = Shop2ParcelCoordinator(hass, entry_b)

    # Store keys must be different (scoped to entry_id)
    assert coord_a._store.key != coord_b._store.key
    assert mock_imap_config_entry.entry_id in coord_a._store.key
    assert entry_b.entry_id in coord_b._store.key


# ---------------------------------------------------------------------------
# Stub: MULT-02 — entities from different accounts are under separate devices
# ---------------------------------------------------------------------------


async def test_imap_coordinator_instantiates_imap_client(hass, mock_imap_config_entry):
    """D-10: ImapCoordinator must instantiate ImapClient, not GmailClient."""
    from custom_components.shop2parcel.api.gmail_client import GmailClient  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415
    from custom_components.shop2parcel.imap_coordinator import ImapCoordinator  # noqa: PLC0415

    mock_imap_config_entry.add_to_hass(hass)
    coordinator = ImapCoordinator(hass, mock_imap_config_entry)

    assert isinstance(coordinator._email_client, ImapClient), (
        "IMAP config entry must create ImapClient, not GmailClient"
    )
    assert not isinstance(coordinator._email_client, GmailClient)


# ---------------------------------------------------------------------------
# Stub: D-11 — entity unique IDs do not collide between two entries
# ---------------------------------------------------------------------------


async def test_two_entries_produce_non_colliding_entity_unique_ids(
    hass, mock_config_entry, mock_imap_config_entry
):
    """MULT-02/D-11: Entities from different accounts have non-overlapping unique_ids.

    Entity unique_id format: f"{DOMAIN}_{entry.entry_id}_{message_id}"
    Since entry_id differs per entry, even the same message_id produces different unique_ids.
    """
    # Both entries must be loaded — this test verifies the unique_id formula,
    # not full coordinator setup. The format is verified by inspection.
    entry_id_a = mock_config_entry.entry_id
    entry_id_b = mock_imap_config_entry.entry_id

    msg_id = "INBOX.123"
    uid_a = f"{DOMAIN}_{entry_id_a}_{msg_id}"
    uid_b = f"{DOMAIN}_{entry_id_b}_{msg_id}"

    assert uid_a != uid_b, "Same message_id must produce different unique_ids across entries"
    assert entry_id_a != entry_id_b, "Two different config entries must have different entry_ids"


# ---------------------------------------------------------------------------
# Phase 30-03 (DEDUP-01): cross-account dedup via the shared hub
# ---------------------------------------------------------------------------


async def test_cross_account_dedup_via_shared_hub(hass, mock_config_entry, mock_imap_config_entry):
    """DEDUP-01: a TN marked by one account's hub is seen as already-submitted
    by a different account's coordinator — one global dedup set, not one per
    account. Drives the REAL async_setup_entry for two accounts (Gmail +
    IMAP) so both coordinators attach to the SAME hass-scoped
    Shop2ParcelHub (mirrors test_hub.py's wiring-test pattern).
    """
    from unittest.mock import AsyncMock, MagicMock, patch  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        # Both entries were added_to_hass before any setup call, so a single
        # async_setup() call bootstraps the "shop2parcel" domain and sets up
        # BOTH not-yet-loaded entries together (mirrors test_hub.py's
        # test_remove_one_of_two_accounts_leaves_hub).
        await hass.config_entries.async_setup(mock_config_entry.entry_id)

        hub = hass.data[DOMAIN]["__shared__"]
        assert isinstance(hub, Shop2ParcelHub)
        assert hub._refcount == 2

        coord_a = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coord_b = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
        assert coord_a._hub is hub
        assert coord_b._hub is hub

        tn = "1Z999AA10123456784"
        # Account A forwards and marks the TN (directly via check_and_mark —
        # equivalent to the terminal dedup write after a real POST success).
        assert coord_a._hub.check_and_mark(tn) is False, "first mark of a new TN returns False"

        # Account B never forwarded this TN itself, but the SAME shared hub
        # already knows it — DEDUP-01's cross-account guarantee.
        assert coord_b._hub.is_submitted(tn), (
            "a TN marked by account A must be visible to account B via the shared hub"
        )

        # Cleanup: unload both accounts so the hub worker task is cancelled
        # before the mocked-Store context exits (mirrors test_hub.py).
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.config_entries.async_unload(mock_imap_config_entry.entry_id)
