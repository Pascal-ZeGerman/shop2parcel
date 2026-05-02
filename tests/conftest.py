"""Shared test fixtures for Shop2Parcel integration tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# IMPORTANT: Mock google/googleapiclient BEFORE importing any custom_components.shop2parcel
# module. Phase 4 __init__.py imports coordinator.py at module level, which imports
# gmail_client.py, which imports from google.oauth2.credentials and googleapiclient.
# Python executes __init__.py when first accessing the shop2parcel package, so the mocks
# MUST be in sys.modules before `from custom_components.shop2parcel.const import DOMAIN`
# runs below (that import triggers the package __init__.py).
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

from custom_components.shop2parcel.const import DOMAIN

# NOTE: `hass` fixture is provided automatically by pytest-homeassistant-custom-component


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
