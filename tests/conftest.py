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
# NOTE: googleapiclient.errors is intentionally NOT mocked here.
# tests/api/test_gmail_client.py sets up its own _MockHttpError and registers it
# via sys.modules before importing gmail_client. If conftest pre-registers
# googleapiclient.errors with a different class, the setdefault in test_gmail_client.py
# becomes a no-op and isinstance() checks in gmail_client._classify_gmail_error break.
# coordinator.py tests mock GmailClient directly so gmail_client.py's module-level
# import of HttpError is not exercised at coordinator test runtime.

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
