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
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.exceptions import ImapAuthError, ImapTransientError, ParcelAppAuthError, ParcelAppTransientError
from .api.imap_client import ImapClient
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_CONNECTION_TYPE,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_TLS,
    CONF_IMAP_USERNAME,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONF_NAME = "name"


class OAuth2FlowHandler(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OptionsFlowHandler":
        """Return options flow handler — registers the gear icon in HA UI.

        Lazy import avoids circular dependency: options_flow.py imports from
        const.py, which is also imported here. Direct import at module top
        works today but the lazy form is the HA-idiomatic pattern.
        """
        from .options_flow import OptionsFlowHandler

        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show connection type picker; route to Gmail OAuth2 or IMAP.

        D-01: Override inherited async_step_user to inject connection type selection.
        Gmail path: super().async_step_user() — inherited OAuth2 redirect unchanged.
        IMAP path: async_step_imap() — custom credential collection.

        CRITICAL: guard is `user_input is not None`, not `user_input` — empty dict is valid.
        CRITICAL: super().async_step_user() called with NO arguments (not user_input=None).
        """
        if user_input is not None:
            conn_type = user_input[CONF_CONNECTION_TYPE]
            if conn_type == CONNECTION_TYPE_IMAP:
                return await self.async_step_imap()
            # Gmail: delegate to inherited OAuth2 flow (async_step_pick_implementation)
            return await super().async_step_user()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_TYPE): vol.In(
                        [CONNECTION_TYPE_GMAIL, CONNECTION_TYPE_IMAP]
                    ),
                }
            ),
        )

    async def async_step_imap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect IMAP credentials and validate connection.

        D-02: Collects host, port, username, password, tls_mode in one form.
        Port default is 993 (SSL, the most common case). Dynamic port pre-fill
        (changing default to 143 when STARTTLS/none is selected) is a UX enhancement
        deferred from Phase 9 — users selecting STARTTLS or none must manually update
        the port field if needed. Static default 993 is acceptable for Phase 9.
        D-03: unique_id = f"{username}@{host}" after successful connection test.
        D-12: Entry title = f"Shop2Parcel ({username}@{host})".
        Security T-09-03: Credentials stored in entry.data (encrypted). Never in options.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_IMAP_HOST]
            port = user_input[CONF_IMAP_PORT]
            username = user_input[CONF_IMAP_USERNAME]
            password = user_input[CONF_IMAP_PASSWORD]
            tls_mode = user_input[CONF_IMAP_TLS]

            # Test IMAP connection in executor (synchronous imaplib call)
            imap_client = ImapClient(self.hass.async_add_executor_job)
            try:
                await imap_client.fetch_shipping_emails(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    tls_mode=tls_mode,
                    search_criteria='SUBJECT "shipped"',
                    since_uid=None,
                )
            except ImapAuthError:
                errors["base"] = "invalid_auth"
            except ImapTransientError:
                errors["base"] = "imap_cannot_connect"
            else:
                account_id = f"{username}@{host}"
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured(error="already_configured_imap")
                self._title = f"Shop2Parcel ({account_id})"
                self._data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_IMAP,
                    CONF_IMAP_HOST: host,
                    CONF_IMAP_PORT: port,
                    CONF_IMAP_USERNAME: username,
                    CONF_IMAP_PASSWORD: password,
                    CONF_IMAP_TLS: tls_mode,
                }
                return await self.async_step_finish()

        return self.async_show_form(
            step_id="imap",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IMAP_HOST): str,
                    vol.Required(CONF_IMAP_PORT, default=993): int,
                    vol.Required(CONF_IMAP_USERNAME): str,
                    vol.Required(CONF_IMAP_PASSWORD): str,
                    vol.Required(CONF_IMAP_TLS, default="ssl"): vol.In(
                        ["ssl", "starttls", "none"]
                    ),
                }
            ),
            errors=errors,
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Called by framework after successful OAuth2 token exchange.

        Fetch Gmail email address via executor (synchronous google-api call).
        Set unique_id to Gmail email to prevent duplicate config entries.
        In reauth path: verify same account, update tokens, reload entry.
        In setup path: store data and proceed to finish step.
        """

        def _get_profile() -> str:
            """Synchronous Gmail profile fetch — must run in executor."""
            credentials = Credentials(
                token=data[CONF_TOKEN][CONF_ACCESS_TOKEN],
                refresh_token=data[CONF_TOKEN].get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
            )
            return (
                build("gmail", "v1", credentials=credentials)
                .users()
                .getProfile(userId="me")
                .execute()["emailAddress"]
            )

        try:
            email: str = await self.hass.async_add_executor_job(_get_profile)
        except Exception:
            _LOGGER.exception("Failed to fetch Gmail profile during config flow")
            return self.async_abort(reason="oauth_error")
        await self.async_set_unique_id(email)

        if self.source == SOURCE_REAUTH:
            reauth_entry = self._get_reauth_entry()
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(reauth_entry, data_updates=data)

        self._abort_if_unique_id_configured()
        self._data = data
        self._title = f"Shop2Parcel ({email})"
        return await self.async_step_finish()

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect parcelapp.net API key and entry name.

        Validates API key via lightweight GET deliveries call (view-deliveries endpoint,
        20/hour quota — separate from and cheaper than add-delivery 20/day quota).
        Pre-fills name field with "Shop2Parcel ({email})" derived from OAuth2 token.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = ParcelAppClient(session=session, api_key=user_input[CONF_API_KEY])
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Initiate re-authorization flow.

        D-04: Branch on connection_type so IMAP entries use IMAP reauth form,
        Gmail entries use the existing OAuth2 reauth confirm flow.
        """
        reauth_entry = self._get_reauth_entry()
        if reauth_entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_IMAP:
            return await self.async_step_reauth_imap()
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
        return await super().async_step_user()

    async def async_step_reauth_imap(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter IMAP credentials after auth failure.

        D-04: Triggered when coordinator raises ConfigEntryAuthFailed for IMAP entries.
        Shows the same credential form as async_step_imap; on success updates entry
        data and reloads.
        """
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            host = user_input.get(CONF_IMAP_HOST, reauth_entry.data.get(CONF_IMAP_HOST, ""))
            port = user_input.get(CONF_IMAP_PORT, reauth_entry.data.get(CONF_IMAP_PORT, 993))
            username = user_input.get(CONF_IMAP_USERNAME, reauth_entry.data.get(CONF_IMAP_USERNAME, ""))
            password = user_input[CONF_IMAP_PASSWORD]
            tls_mode = user_input.get(CONF_IMAP_TLS, reauth_entry.data.get(CONF_IMAP_TLS, "ssl"))

            imap_client = ImapClient(self.hass.async_add_executor_job)
            try:
                await imap_client.fetch_shipping_emails(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    tls_mode=tls_mode,
                    search_criteria='SUBJECT "shipped"',
                    since_uid=None,
                )
            except ImapAuthError:
                errors["base"] = "invalid_auth"
            except ImapTransientError:
                errors["base"] = "imap_cannot_connect"
            else:
                new_data = dict(reauth_entry.data)
                new_data[CONF_IMAP_HOST] = host
                new_data[CONF_IMAP_PORT] = port
                new_data[CONF_IMAP_USERNAME] = username
                new_data[CONF_IMAP_PASSWORD] = password
                new_data[CONF_IMAP_TLS] = tls_mode
                return self.async_update_reload_and_abort(reauth_entry, data=new_data)

        # Pre-fill with existing values except password (never pre-fill credentials)
        return self.async_show_form(
            step_id="reauth_imap",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IMAP_HOST,
                        default=reauth_entry.data.get(CONF_IMAP_HOST, ""),
                    ): str,
                    vol.Required(
                        CONF_IMAP_PORT,
                        default=reauth_entry.data.get(CONF_IMAP_PORT, 993),
                    ): int,
                    vol.Required(
                        CONF_IMAP_USERNAME,
                        default=reauth_entry.data.get(CONF_IMAP_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_IMAP_PASSWORD): str,
                    vol.Required(
                        CONF_IMAP_TLS,
                        default=reauth_entry.data.get(CONF_IMAP_TLS, "ssl"),
                    ): vol.In(["ssl", "starttls", "none"]),
                }
            ),
            errors=errors,
        )
