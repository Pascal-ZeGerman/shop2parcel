"""Application credentials support for Shop2Parcel.

Declares the Google OAuth2 authorization server for HA's application_credentials platform.
The authorization_url and token_url are Google's standard OAuth2 v2 endpoints.
"""
from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return Google OAuth2 authorization server."""
    return AuthorizationServer(
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    return {
        "oauth_consent_url": "https://console.cloud.google.com/apis/credentials/consent",
        "more_info_url": "https://github.com/yourusername/shop2parcel",
        "oauth_creds_url": "https://console.cloud.google.com/apis/credentials",
    }
