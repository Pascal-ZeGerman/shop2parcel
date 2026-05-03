"""Shared test fixtures for Shop2Parcel integration tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock google/googleapiclient before any shop2parcel import. The lazy import
# inside async_setup_entry fires when the hass fixture runs async_setup_entry,
# which imports coordinator.py, which imports gmail_client.py. The mocks must
# be in sys.modules before `from custom_components.shop2parcel.const import DOMAIN`
# (below) triggers the package __init__.py on first access.
_GOOGLE_MOCK = MagicMock()
sys.modules.setdefault("google", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2.credentials", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient.discovery", _GOOGLE_MOCK)
# Phase 7 fix: tests/test_coordinator.py must be collectable standalone (without
# tests/api/test_gmail_client.py running first).  The gmail_client module-level
# `from googleapiclient.errors import HttpError` fails when googleapiclient is a
# MagicMock and googleapiclient.errors is NOT in sys.modules.
#
# Solution: register a minimal errors-module mock with HttpError as a real exception
# class (required for isinstance() checks in _classify_gmail_error).
# tests/api/test_gmail_client.py uses setdefault — since conftest runs first this
# setdefault is now a no-op.  That test file is updated to use direct assignment
# so its _MockHttpError class takes effect for gmail_client's already-cached import.
#
# NOTE: gmail_client.py caches the HttpError class on first import.  When the full
# suite runs (gmail_client tests first), the class in play is from test_gmail_client.py.
# When test_coordinator.py runs alone, the class below is used — coordinator tests
# mock GmailClient entirely so HttpError is never exercised in those tests.

class _StubHttpError(Exception):
    """Stub HttpError for coordinator-test isolation — not used in isinstance() path."""
    def __init__(self, resp=None, content=b""):
        self.resp = resp
        self.content = content


_ERRORS_MOCK = MagicMock()
_ERRORS_MOCK.HttpError = _StubHttpError
sys.modules.setdefault("googleapiclient.errors", _ERRORS_MOCK)

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN

# NOTE: `hass` fixture is provided automatically by pytest-homeassistant-custom-component


async def setup_coordinator_with_data(
    hass, mock_config_entry, data: dict[str, ShipmentData]
):
    """Shared helper: set up the coordinator with pre-seeded data and forward to platforms.

    Patches all coordinator dependencies (GmailClient, ParcelAppClient, EmailParser,
    Store, config_entry_oauth2_flow) so no real I/O occurs during setup.  After
    async_setup, coordinator.data is replaced with the supplied ``data`` dict and
    hass.async_block_till_done() drains any resulting listener callbacks.

    Returns the configured coordinator instance.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Store") as mock_store_cls,
        patch("custom_components.shop2parcel.coordinator.config_entry_oauth2_flow") as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data(data)
        await hass.async_block_till_done()
        return coordinator


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):  # noqa: F811
    """Allow HA's component loader to find custom_components/ during tests."""
    return enable_custom_integrations


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a MockConfigEntry with minimal valid data for Shop2Parcel."""
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
        unique_id="user@gmail.com",
    )


@pytest.fixture
def mock_imap_config_entry() -> MockConfigEntry:
    """Return a MockConfigEntry with minimal valid IMAP data for Shop2Parcel."""
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
        },
        unique_id="user@example.com@imap.example.com",
    )
