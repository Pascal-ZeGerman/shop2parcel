"""Config flow for Shop2Parcel — Gmail OAuth2 + parcelapp.net API key setup.

Flow sequence:
  1. HA Application Credentials UI (external) — user enters Gmail client_id + client_secret once
  2. OAuth2 redirect (inherited from AbstractOAuth2FlowHandler) — Google consent + token exchange
  3. async_oauth_create_entry hook — extract Gmail email, set unique_id, go to finish step
  4. async_step_finish (custom) — user enters parcelapp.net API key and entry name

Reauth path:
  async_step_reauth → async_step_reauth_confirm → async_step_user (re-runs OAuth2 redirect)

CRITICAL: async_step_creation is RESERVED by AbstractOAuth2FlowHandler for token exchange.
The custom parcelapp data-collection step MUST be async_step_finish (step_id="finish").

Security:
  T-03-03-01: api_key is never logged — caught by exception type only, not message content.
  T-03-03-02: access_token and refresh_token are never logged — _get_profile() uses the
              token only for the API call, never for logging.
  T-03-03-04: async_set_unique_id(email) + _abort_if_unique_id_configured() prevents
              duplicate config entries for the same Gmail account.
  T-03-03-05: Scope is explicitly set to gmail.readonly only.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.exceptions import ParcelAppAuthError, ParcelAppTransientError
from .api.parcelapp import ParcelAppClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONF_API_KEY = "api_key"
CONF_NAME = "name"


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Shop2Parcel OAuth2 authentication."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Initialize."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._title: str = ""

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Return extra parameters sent to Google's authorize endpoint.

        access_type=offline: instructs Google to issue a refresh_token.
        prompt=consent: forces Google to show the consent screen every time,
            ensuring a new refresh_token is issued even if the user previously
            authorized. Without this, re-authorization skips the consent screen
            and does NOT issue a new refresh_token — breaking token refresh after
            the initial access_token expires.
        """
        return {
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Called by framework after successful OAuth2 token exchange.

        Fetch Gmail email address via executor (synchronous google-api call).
        Set unique_id to Gmail email to prevent duplicate config entries.
        In reauth path: verify same account, update tokens, reload entry.
        In setup path: store data and proceed to finish step.
        """

        def _get_profile() -> str:
            """Synchronous Gmail profile fetch — must run in executor."""
            credentials = Credentials(data[CONF_TOKEN][CONF_ACCESS_TOKEN])
            return (
                build("gmail", "v1", credentials=credentials)
                .users()
                .getProfile(userId="me")
                .execute()["emailAddress"]
            )

        email: str = await self.hass.async_add_executor_job(_get_profile)
        await self.async_set_unique_id(email)

        if self.source == SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(reauth_entry, data=data)

        self._abort_if_unique_id_configured()
        self._data = data
        self._title = f"Shop2Parcel ({email})"
        return await self.async_step_finish()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect parcelapp.net API key and entry name.

        Validates API key via lightweight GET deliveries call (view-deliveries endpoint,
        20/hour quota — separate from and cheaper than add-delivery 20/day quota).
        Pre-fills name field with "Shop2Parcel ({email})" derived from OAuth2 token.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = ParcelAppClient(
                session=session, api_key=user_input[CONF_API_KEY]
            )
            try:
                await client.async_get_deliveries()
            except ParcelAppAuthError:
                errors["base"] = "invalid_api_key"
            except ParcelAppTransientError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={**self._data, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="finish",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(CONF_NAME, default=self._title): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Initiate re-authorization flow.

        Called by HA Repairs system when ConfigEntryAuthFailed is raised (Phase 4).
        Immediately shows the confirmation dialog — no user input needed yet.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authorization dialog, then re-run inherited OAuth2 redirect.

        Shows a form with descriptive text (from strings.json config.step.reauth_confirm).
        On submit (any user_input), delegates to async_step_user which re-runs the
        inherited OAuth2 redirect without asking for the parcelapp API key again (D-11).
        """
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
