"""Tests for the application_credentials platform.

Covers the OAuth2 authorization server declaration and the description
placeholders shown in HA's "Add credentials" dialog. These were previously
untested — a typo in the Google authorize/token URLs would break every Gmail
setup with no test to catch it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.shop2parcel.application_credentials import (
    async_get_authorization_server,
    async_get_description_placeholders,
)


async def test_authorization_server_uses_google_oauth2_endpoints():
    """async_get_authorization_server returns Google's standard OAuth2 v2 endpoints."""
    server = await async_get_authorization_server(MagicMock())
    assert server.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert server.token_url == "https://oauth2.googleapis.com/token"


async def test_description_placeholders_contains_expected_keys():
    """async_get_description_placeholders returns the three keys the dialog references."""
    placeholders = await async_get_description_placeholders(MagicMock())
    assert set(placeholders) == {"oauth_consent_url", "more_info_url", "oauth_creds_url"}


async def test_description_placeholder_values_point_to_google_console_and_repo():
    """Placeholder URLs link to the Google Cloud console and the project repo."""
    placeholders = await async_get_description_placeholders(MagicMock())
    assert placeholders["oauth_consent_url"] == (
        "https://console.cloud.google.com/apis/credentials/consent"
    )
    assert placeholders["oauth_creds_url"] == ("https://console.cloud.google.com/apis/credentials")
    assert placeholders["more_info_url"] == "https://github.com/Pascal-ZeGerman/shop2parcel"
