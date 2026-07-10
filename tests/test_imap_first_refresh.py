"""Tests for PAR-02: ImapCoordinator must set _first_refresh_done on first success.

Phase 29 Plan 02 — the IMAP poll path reads self._first_refresh_done (to gate the
inline Ollama fallback past the HA bootstrap first-refresh window) but never sets
it to True, so the guard stays permanently active for IMAP accounts. This mirrors
the fix already present in GmailCoordinator._async_update_data (gmail_coordinator.py:200).

- test_imap_first_refresh_done_set_on_success: a clean poll must flip the flag True.
- test_imap_first_refresh_done_not_set_on_failure: a poll that raises UpdateFailed
  must leave the flag False (the assignment must live ONLY in the success path,
  never inside the `except BaseException` block — T-29-03 / SPEC prohibition).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_imap_first_refresh_entry() -> MockConfigEntry:
    """IMAP MockConfigEntry with Stage-2 DISABLED (no ollama_url in options).

    Modeled on tests/test_imap_coordinator.py::mock_imap_no_stage2_entry, with a
    distinct unique_id to keep this plan's fixtures isolated from other IMAP tests.
    """
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
        unique_id="imap-first-refresh-test@example.com",
    )


def _make_imap_coordinator(hass, entry: MockConfigEntry) -> ImapCoordinator:
    """Construct an ImapCoordinator with the store patched out (no real I/O)."""
    entry.add_to_hass(hass)
    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore"):
        return ImapCoordinator(hass, entry)


# ---------------------------------------------------------------------------
# PAR-02: _first_refresh_done must be set on success, not on failure.
# ---------------------------------------------------------------------------


async def test_imap_first_refresh_done_set_on_success(hass, mock_imap_first_refresh_entry):
    """RED (current code): a clean poll must flip _first_refresh_done to True.

    Matches GmailCoordinator._async_update_data (gmail_coordinator.py:200) — the
    assignment must land on the success return path of ImapCoordinator too.
    """
    coord = _make_imap_coordinator(hass, mock_imap_first_refresh_entry)
    assert coord._first_refresh_done is False  # starts False (coordinator.py:548)

    coord._async_update_data_inner = AsyncMock(return_value={})

    result = await coord._async_update_data()

    assert result == {}
    assert coord._first_refresh_done is True


async def test_imap_first_refresh_done_not_set_on_failure(hass, mock_imap_first_refresh_entry):
    """A poll that raises UpdateFailed must leave _first_refresh_done False.

    Prohibition (T-29-03): the assignment must never appear inside the
    `except BaseException` block — a transient first-poll failure must keep the
    bootstrap first-refresh guard active.
    """
    from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: PLC0415

    coord = _make_imap_coordinator(hass, mock_imap_first_refresh_entry)
    assert coord._first_refresh_done is False

    coord._async_update_data_inner = AsyncMock(side_effect=UpdateFailed("simulated IMAP failure"))

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    assert coord._first_refresh_done is False
