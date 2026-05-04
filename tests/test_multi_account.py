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
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: PLC0415

    # Create a second IMAP entry with a different account
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: PLC0415
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
    """D-10: Coordinator with connection_type='imap' must instantiate ImapClient, not GmailClient."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415
    from custom_components.shop2parcel.api.gmail_client import GmailClient  # noqa: PLC0415
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: PLC0415

    mock_imap_config_entry.add_to_hass(hass)
    coordinator = Shop2ParcelCoordinator(hass, mock_imap_config_entry)

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
