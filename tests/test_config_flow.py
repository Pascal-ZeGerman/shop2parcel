"""Tests for Shop2Parcel config flow — covers CONF-01 through CONF-07.

Google API libraries are mocked at sys.modules level to prevent real API calls.
These tests require pytest-homeassistant-custom-component; real HA modules are
loaded by the pytest plugin before collection and the sys.modules.setdefault calls
for homeassistant.* are no-ops.

Threat mitigation T-03-03-01/02: api_key and access_token values in tests are
always fake literals — never real credentials.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Mock HA framework modules before any import of config_flow
# ---------------------------------------------------------------------------

_mock_ha_config_entries = MagicMock()
_mock_ha_config_entries.SOURCE_REAUTH = "reauth"
_mock_ha_config_entries.ConfigFlowResult = dict  # type alias for tests

_mock_ha_const = MagicMock()
_mock_ha_const.CONF_TOKEN = "token"
_mock_ha_const.CONF_ACCESS_TOKEN = "access_token"

_mock_oauth2_flow = MagicMock()
_mock_ha_helpers = MagicMock()
_mock_ha_helpers.config_entry_oauth2_flow = _mock_oauth2_flow
_mock_ha_aiohttp_client = MagicMock()
_mock_ha_helpers.aiohttp_client = _mock_ha_aiohttp_client

_mock_ha_core = MagicMock()

_mock_voluptuous = MagicMock()

_mock_google_oauth2 = MagicMock()
_mock_credentials_module = MagicMock()
_mock_googleapiclient = MagicMock()
_mock_discovery = MagicMock()


class _FakeAbstractOAuth2FlowHandler:
    """Minimal stand-in for AbstractOAuth2FlowHandler."""

    DOMAIN = ""
    hass: Any = None

    def __init_subclass__(cls, domain: str = "", **kwargs: Any) -> None:
        """Accept domain keyword argument required by AbstractOAuth2FlowHandler."""
        super().__init_subclass__(**kwargs)

    def __init__(self) -> None:
        self._data: dict = {}
        self._title: str = ""
        self.context: dict = {"source": "user"}  # instance attribute

    @property
    def source(self) -> str:
        """Mirror real HA FlowHandler.source property."""
        return self.context.get("source", "user")

    def async_show_form(self, *, step_id, data_schema=None, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_update_reload_and_abort(self, entry, *, data=None, data_updates=None):
        return {"type": "abort", "reason": "reauth_successful"}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def _abort_if_unique_id_configured(self):
        pass

    def _abort_if_unique_id_mismatch(self, reason=None):
        pass

    def _get_reauth_entry(self):
        return MagicMock()

    async def async_set_unique_id(self, unique_id):
        pass

    async def async_step_user(self, user_input=None):
        return {"type": "form", "step_id": "user"}


_mock_oauth2_flow.AbstractOAuth2FlowHandler = _FakeAbstractOAuth2FlowHandler

sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", _mock_ha_config_entries)
sys.modules.setdefault("homeassistant.const", _mock_ha_const)
sys.modules.setdefault("homeassistant.helpers", _mock_ha_helpers)
sys.modules.setdefault("homeassistant.helpers.config_entry_oauth2_flow", _mock_oauth2_flow)
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", _mock_ha_aiohttp_client)
sys.modules.setdefault("homeassistant.core", _mock_ha_core)
sys.modules.setdefault("voluptuous", _mock_voluptuous)
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.oauth2", _mock_google_oauth2)
sys.modules.setdefault("google.oauth2.credentials", _mock_credentials_module)
sys.modules.setdefault("googleapiclient", _mock_googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", _mock_discovery)

from custom_components.shop2parcel.api.exceptions import (  # noqa: E402
    ParcelAppAuthError,
    ParcelAppTransientError,
)
from custom_components.shop2parcel.config_flow import OAuth2FlowHandler  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> OAuth2FlowHandler:
    """Return a fresh OAuth2FlowHandler with a mock hass."""
    handler = OAuth2FlowHandler.__new__(OAuth2FlowHandler)
    _FakeAbstractOAuth2FlowHandler.__init__(handler)
    handler.hass = MagicMock()
    handler.hass.async_add_executor_job = AsyncMock(return_value="user@gmail.com")
    return handler


FAKE_TOKEN_DATA = {
    "auth_implementation": "shop2parcel",
    "token": {
        "access_token": "fake-access-token",
        "expires_at": 9999999999.0,
        "refresh_token": "fake-refresh-token",
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    },
}


# ---------------------------------------------------------------------------
# Test: extra_authorize_data contains required OAuth2 parameters
# ---------------------------------------------------------------------------


def test_extra_authorize_data_has_offline_access():
    """extra_authorize_data must contain access_type=offline for refresh token."""
    handler = _make_handler()
    data = handler.extra_authorize_data
    assert data["access_type"] == "offline"


def test_extra_authorize_data_has_prompt_consent():
    """extra_authorize_data must contain prompt=consent to force consent screen."""
    handler = _make_handler()
    data = handler.extra_authorize_data
    assert data["prompt"] == "consent"


# ---------------------------------------------------------------------------
# Test: async_oauth_create_entry uses executor for Gmail profile fetch
# ---------------------------------------------------------------------------


async def test_async_oauth_create_entry_calls_executor_job():
    """Gmail profile fetch (synchronous google-api call) must run in executor."""
    handler = _make_handler()
    handler.hass.async_add_executor_job = AsyncMock(return_value="user@gmail.com")
    handler.async_set_unique_id = AsyncMock()

    # Patch async_step_finish to avoid further async logic
    handler.async_step_finish = AsyncMock(return_value={"type": "form", "step_id": "finish"})

    await handler.async_oauth_create_entry(FAKE_TOKEN_DATA)

    assert handler.hass.async_add_executor_job.called, (
        "async_add_executor_job must be called for the synchronous Gmail profile fetch"
    )


async def test_async_oauth_create_entry_sets_unique_id():
    """async_oauth_create_entry must call async_set_unique_id with email."""
    handler = _make_handler()
    handler.hass.async_add_executor_job = AsyncMock(return_value="user@gmail.com")
    handler.async_set_unique_id = AsyncMock()
    handler.async_step_finish = AsyncMock(return_value={"type": "form"})

    await handler.async_oauth_create_entry(FAKE_TOKEN_DATA)

    handler.async_set_unique_id.assert_called_once_with("user@gmail.com")


# ---------------------------------------------------------------------------
# Test: async_step_finish error handling
# ---------------------------------------------------------------------------


async def test_finish_step_auth_error_shows_invalid_api_key():
    """ParcelAppAuthError in finish step → errors["base"] == "invalid_api_key"."""
    handler = _make_handler()

    mock_client = AsyncMock()
    mock_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppAuthError("bad key"))

    mock_session = MagicMock()
    _mock_ha_aiohttp_client.async_get_clientsession = MagicMock(return_value=mock_session)

    with patch(
        "custom_components.shop2parcel.config_flow.ParcelAppClient",
        return_value=mock_client,
    ):
        result = await handler.async_step_finish(user_input={"api_key": "bad-key", "name": "Test"})

    assert result["errors"]["base"] == "invalid_api_key"


async def test_finish_step_transient_error_shows_cannot_connect():
    """ParcelAppTransientError in finish step → errors["base"] == "cannot_connect"."""
    handler = _make_handler()

    mock_client = AsyncMock()
    mock_client.async_get_deliveries = AsyncMock(side_effect=ParcelAppTransientError("timeout"))

    mock_session = MagicMock()
    _mock_ha_aiohttp_client.async_get_clientsession = MagicMock(return_value=mock_session)

    with patch(
        "custom_components.shop2parcel.config_flow.ParcelAppClient",
        return_value=mock_client,
    ):
        result = await handler.async_step_finish(user_input={"api_key": "any-key", "name": "Test"})

    assert result["errors"]["base"] == "cannot_connect"


async def test_finish_step_success_creates_entry_with_api_key():
    """Successful finish step → async_create_entry called with api_key in data."""
    handler = _make_handler()
    handler._data = dict(FAKE_TOKEN_DATA)
    handler._title = "Shop2Parcel (user@gmail.com)"

    mock_client = AsyncMock()
    mock_client.async_get_deliveries = AsyncMock(return_value=[])

    mock_session = MagicMock()
    _mock_ha_aiohttp_client.async_get_clientsession = MagicMock(return_value=mock_session)

    with patch(
        "custom_components.shop2parcel.config_flow.ParcelAppClient",
        return_value=mock_client,
    ):
        result = await handler.async_step_finish(
            user_input={"api_key": "valid-key-999", "name": "My Shop2Parcel"}
        )

    assert result["type"] == "create_entry"
    assert result["data"]["api_key"] == "valid-key-999"
    assert result["title"] == "My Shop2Parcel"


async def test_finish_step_no_input_shows_form():
    """async_step_finish with None input → returns form (step_id="finish")."""
    handler = _make_handler()
    result = await handler.async_step_finish(user_input=None)
    assert result["step_id"] == "finish"
    assert result["type"] == "form"


# ---------------------------------------------------------------------------
# Test: async_step_reauth_confirm
# ---------------------------------------------------------------------------


async def test_reauth_confirm_none_input_shows_form():
    """async_step_reauth_confirm with None input → returns reauth_confirm form."""
    handler = _make_handler()
    result = await handler.async_step_reauth_confirm(user_input=None)
    assert result["step_id"] == "reauth_confirm"
    assert result["type"] == "form"


async def test_reauth_confirm_with_input_goes_to_oauth2_not_picker():
    """async_step_reauth_confirm with user_input={} → calls super().async_step_user, not picker.

    WR-01: reauth must bypass the connection type picker by delegating to the base
    class OAuth2 method, not the overridden picker form.

    Patch the base class async_step_user at the real import path (works in both
    isolated test_config_flow.py runs and pytest-ha runs where real HA is loaded).
    """
    from custom_components.shop2parcel.config_flow import OAuth2FlowHandler

    # Find the actual base class in the real MRO (second entry after OAuth2FlowHandler itself)
    base_cls = OAuth2FlowHandler.__mro__[1]
    handler = _make_handler()
    with patch.object(
        base_cls,
        "async_step_user",
        new=AsyncMock(return_value={"type": "external", "step_id": "auth"}),
    ) as mock_super_user:
        result = await handler.async_step_reauth_confirm(user_input={})

    mock_super_user.assert_called_once()
    assert result.get("step_id") != "user", "Must NOT show the connection type picker during reauth"


# ---------------------------------------------------------------------------
# Test: reauth completion path — async_oauth_create_entry under SOURCE_REAUTH
# (Phase 6 D-01 gap fill: reauth path)
# ---------------------------------------------------------------------------


async def test_reauth_oauth_create_entry_calls_update_reload_and_abort():
    """SOURCE_REAUTH branch calls async_update_reload_and_abort with reauth_entry and data.

    Covers Phase 6 D-01 gap 1: existing reauth tests stop at the dialog; this exercises
    the OAuth-completed path that hands tokens back to HA and triggers entry reload.
    """
    handler = _make_handler()
    # source is a property reading from self.context["source"]; set context directly
    handler.context = {"source": _mock_ha_config_entries.SOURCE_REAUTH}  # "reauth"
    fake_reauth_entry = MagicMock()
    handler._get_reauth_entry = MagicMock(return_value=fake_reauth_entry)
    handler._abort_if_unique_id_mismatch = MagicMock()
    handler.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )
    handler.async_set_unique_id = AsyncMock()

    result = await handler.async_oauth_create_entry(FAKE_TOKEN_DATA)

    # Profile fetch happened
    handler.hass.async_add_executor_job.assert_called_once()
    # Unique ID was set to the email returned by the executor mock
    handler.async_set_unique_id.assert_awaited_once_with("user@gmail.com")
    # Mismatch guard was invoked with the documented reason
    handler._abort_if_unique_id_mismatch.assert_called_once_with(reason="wrong_account")
    # Reauth entry was looked up and used
    handler._get_reauth_entry.assert_called_once()
    handler.async_update_reload_and_abort.assert_called_once_with(
        fake_reauth_entry, data_updates=FAKE_TOKEN_DATA
    )
    # Result is the abort dict (does NOT proceed to async_step_finish)
    assert result == {"type": "abort", "reason": "reauth_successful"}


# ---------------------------------------------------------------------------
# Test: class structure requirements
# ---------------------------------------------------------------------------


def test_handler_has_domain_class_attribute():
    """OAuth2FlowHandler must have DOMAIN class attribute (framework requirement)."""
    assert hasattr(OAuth2FlowHandler, "DOMAIN")
    assert OAuth2FlowHandler.DOMAIN == "shop2parcel"


def test_handler_does_not_define_async_step_creation():
    """async_step_creation is RESERVED by framework — must NOT be defined in handler."""
    assert not hasattr(OAuth2FlowHandler, "async_step_creation") or (
        # If inherited from base class, it should not be overridden in our class
        "async_step_creation" not in OAuth2FlowHandler.__dict__
    ), "async_step_creation is reserved by AbstractOAuth2FlowHandler — do not override"


def test_handler_defines_async_step_finish():
    """async_step_finish must be defined in OAuth2FlowHandler."""
    assert "async_step_finish" in OAuth2FlowHandler.__dict__


def test_handler_defines_async_step_reauth():
    """async_step_reauth must be defined in OAuth2FlowHandler."""
    assert "async_step_reauth" in OAuth2FlowHandler.__dict__


def test_handler_defines_async_step_reauth_confirm():
    """async_step_reauth_confirm must be defined in OAuth2FlowHandler."""
    assert "async_step_reauth_confirm" in OAuth2FlowHandler.__dict__


# ---------------------------------------------------------------------------
# IMAP config flow stubs — D-01, D-02, D-03, D-04
# All xfail until Plan 09-03 implements the IMAP flow steps.
# ---------------------------------------------------------------------------


async def test_async_step_user_shows_picker():
    """D-01: async_step_user with None input returns a form with step_id='user'."""
    handler = _make_handler()
    result = await handler.async_step_user(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_async_step_user_routes_imap_to_async_step_imap():
    """D-01: async_step_user with connection_type='imap' delegates to async_step_imap."""
    handler = _make_handler()
    handler.async_step_imap = AsyncMock(return_value={"type": "form", "step_id": "imap"})
    result = await handler.async_step_user(user_input={"connection_type": "imap"})
    handler.async_step_imap.assert_called_once()
    assert result["step_id"] == "imap"


async def test_async_step_imap_returns_form():
    """D-02: async_step_imap with None input returns a form with step_id='imap'."""
    handler = _make_handler()
    result = await handler.async_step_imap(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "imap"


async def test_async_step_imap_on_success_stores_credentials_in_data():
    """D-02/D-03: Successful async_step_imap stores credentials in entry.data (not options).

    Credentials (host, port, username, password, tls_mode) must be in handler._data
    so they land in entry.data (encrypted) rather than entry.options.
    """
    handler = _make_handler()
    handler.async_set_unique_id = AsyncMock()
    handler.async_step_finish = AsyncMock(return_value={"type": "create_entry", "title": "Shop2Parcel (user@imap.example.com)", "data": {}})

    mock_imap_client = AsyncMock()
    mock_imap_client.fetch_shipping_emails = AsyncMock(return_value=([], None))

    with patch(
        "custom_components.shop2parcel.config_flow.ImapClient",
        return_value=mock_imap_client,
    ):
        await handler.async_step_imap(
            user_input={
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_username": "user@example.com",
                "imap_password": "app-password",
                "imap_tls": "ssl",
            }
        )

    # Credentials must be in handler._data (goes to entry.data, not entry.options)
    assert handler._data.get("imap_host") == "imap.example.com"
    assert handler._data.get("imap_username") == "user@example.com"
    assert handler._data.get("imap_password") == "app-password"
    assert "connection_type" in handler._data
    assert handler._data["connection_type"] == "imap"


async def test_async_step_imap_sets_unique_id_to_username_at_host():
    """D-03: async_step_imap sets unique_id to 'username@host' after successful connection."""
    handler = _make_handler()
    handler.async_set_unique_id = AsyncMock()
    handler.async_step_finish = AsyncMock(return_value={"type": "create_entry"})

    mock_imap_client = AsyncMock()
    mock_imap_client.fetch_shipping_emails = AsyncMock(return_value=([], None))

    with patch(
        "custom_components.shop2parcel.config_flow.ImapClient",
        return_value=mock_imap_client,
    ):
        await handler.async_step_imap(
            user_input={
                "imap_host": "imap.example.com",
                "imap_port": 993,
                "imap_username": "user@example.com",
                "imap_password": "app-password",
                "imap_tls": "ssl",
            }
        )

    handler.async_set_unique_id.assert_awaited_once_with("user@example.com@imap.example.com")


async def test_async_step_reauth_routes_imap_entry_to_reauth_imap():
    """D-04: async_step_reauth routes IMAP entries to async_step_reauth_imap."""
    handler = _make_handler()
    fake_entry = MagicMock()
    fake_entry.data = {"connection_type": "imap"}
    handler._get_reauth_entry = MagicMock(return_value=fake_entry)
    handler.async_step_reauth_imap = AsyncMock(
        return_value={"type": "form", "step_id": "reauth_imap"}
    )

    result = await handler.async_step_reauth(entry_data=fake_entry.data)

    handler.async_step_reauth_imap.assert_called_once()
    assert result["step_id"] == "reauth_imap"


# ---------------------------------------------------------------------------
# Gap closure regression tests — 09-05-PLAN.md
# ---------------------------------------------------------------------------


async def test_reauth_imap_form_uses_step_id_reauth_imap():
    """CR-01 regression: async_step_reauth_imap must use step_id='reauth_imap'.

    HA data_entry_flow dispatches the next form submission to
    async_step_{step_id}. The method is named async_step_reauth_imap,
    so step_id MUST be 'reauth_imap' — not 'reauth_confirm_imap'.
    This test fails if the bug is re-introduced.
    """
    handler = _make_handler()
    fake_entry = MagicMock()
    fake_entry.data = {
        "connection_type": "imap",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "user@example.com",
        "imap_tls": "ssl",
    }
    handler._get_reauth_entry = MagicMock(return_value=fake_entry)

    # Call with None input — handler should return the form (no user_input provided)
    result = await handler.async_step_reauth_imap(user_input=None)

    assert result["type"] == "form", (
        f"Expected form result but got: {result['type']}"
    )
    assert result["step_id"] == "reauth_imap", (
        f"step_id MUST be 'reauth_imap' (got '{result['step_id']}'). "
        "HA dispatches the next submission to async_step_{step_id} — "
        "'reauth_confirm_imap' has no matching handler method (CR-01)."
    )


async def test_reauth_imap_step_id_not_reauth_confirm_imap():
    """CR-01 regression (negative): step_id must NOT be 'reauth_confirm_imap'.

    Confirms the exact bug from CR-01 cannot regress: if step_id were
    'reauth_confirm_imap', HA would call async_step_reauth_confirm_imap
    on the next submission — a method that does not exist.
    """
    handler = _make_handler()
    fake_entry = MagicMock()
    fake_entry.data = {
        "connection_type": "imap",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_username": "user@example.com",
        "imap_tls": "ssl",
    }
    handler._get_reauth_entry = MagicMock(return_value=fake_entry)

    result = await handler.async_step_reauth_imap(user_input=None)

    assert result.get("step_id") != "reauth_confirm_imap", (
        "step_id='reauth_confirm_imap' is the CR-01 bug — no handler method exists for that step_id."
    )
