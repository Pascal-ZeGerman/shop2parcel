"""Shared test fixtures for Shop2Parcel integration tests."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.const import DOMAIN

# NOTE: `hass` fixture is provided automatically by pytest-homeassistant-custom-component

# Mock google/googleapiclient so config_flow.py can be loaded without real packages
_GOOGLE_MOCK = MagicMock()
sys.modules.setdefault("google", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2.credentials", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient.discovery", _GOOGLE_MOCK)


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
