"""Tests for Shop2Parcel options flow — covers EMAIL-05, CFG-01/02/03, OLLM-01/02/03, FLD-01/02, QUICK-260622-j6w."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
import voluptuous as vol
from homeassistant.helpers.selector import SelectSelector

from custom_components.shop2parcel.api.exceptions import OllamaTransientError
from custom_components.shop2parcel.const import (
    CONF_CUSTOM_FIELDS,
    CONF_DEBUG_MODE,
    CONF_FIELD_DESCRIPTION,
    CONF_FIELD_NAME,
    CONF_GMAIL_QUERY,
    CONF_IMAP_SEARCH,
    CONF_IMAP_VERIFY_TLS,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT,
    CONF_OLLAMA_URL,
    CONF_POLL_INTERVAL,
    CONF_QUEUE_MAXLEN,
    CONF_RESCAN_WINDOW_DAYS,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUEUE_MAXLEN,
    DEFAULT_RESCAN_WINDOW_DAYS,
    MAX_RESCAN_WINDOW_DAYS,
    MIN_RESCAN_WINDOW_DAYS,
)
from custom_components.shop2parcel.options_flow import _OLLAMA_LOCALHOST_URL, OptionsFlowHandler


def _make_handler_with_options(options: dict) -> tuple[OptionsFlowHandler, MagicMock]:
    """Construct OptionsFlowHandler with a fake config_entry.options.

    Returns (handler, fake_entry) — callers must use patch.object as a context
    manager to safely scope the config_entry property override to the test.
    """
    handler = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry = MagicMock()
    fake_entry.options = options
    fake_entry.data = {"connection_type": "gmail"}  # explicit, not MagicMock default
    return handler, fake_entry


def _make_imap_handler_with_options(options: dict) -> tuple[OptionsFlowHandler, MagicMock]:
    """Construct OptionsFlowHandler with a fake IMAP config_entry.

    Returns (handler, fake_entry) — callers must use patch.object as a context
    manager to safely scope the config_entry property override to the test.
    """
    handler = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry = MagicMock()
    fake_entry.options = options
    fake_entry.data = {"connection_type": "imap"}
    return handler, fake_entry


@pytest.fixture(autouse=True)
def no_localhost_probe():
    """Suppress the localhost Ollama probe in all tests.

    Tests that specifically verify probe behaviour override this via their own
    patch.object(OptionsFlowHandler, '_probe_ollama_localhost', ...) block,
    which takes precedence (LIFO patch stack).
    """
    with patch.object(
        OptionsFlowHandler,
        "_probe_ollama_localhost",
        new=AsyncMock(return_value=""),
    ):
        yield


# ---------------------------------------------------------------------------
# Task 1 new tests: menu-first init + async_step_settings
# ---------------------------------------------------------------------------


async def test_init_returns_menu(hass, mock_config_entry):
    """D-01: async_step_init returns a top-level menu with 'settings' and 'custom_fields'."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=None)
    assert result["type"] == "menu"
    assert result["step_id"] == "init"
    assert "settings" in result["menu_options"]
    assert "custom_fields" in result["menu_options"]


async def test_settings_shows_form(hass, mock_config_entry):
    """async_step_settings returns a form at step_id='settings' with all required fields."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "settings"
    schema_keys = [str(k) for k in result["data_schema"].schema]
    # All new Ollama fields must be present
    for key in (CONF_OLLAMA_URL, CONF_OLLAMA_MODEL, CONF_OLLAMA_TIMEOUT, CONF_QUEUE_MAXLEN):
        assert key in schema_keys, f"Expected '{key}' in settings schema"
    # Existing fields must also be present
    for key in (CONF_POLL_INTERVAL, CONF_GMAIL_QUERY, CONF_DEBUG_MODE):
        assert key in schema_keys, f"Expected '{key}' in settings schema"


async def test_settings_locked_fields_placeholder(hass, mock_config_entry):
    """CFG-03: description_placeholders['locked_fields'] includes all 4 locked fields
    (LOH-SUMMARY: order_summary is now the 4th locked field).
    """
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["description_placeholders"]["locked_fields"] == (
        "tracking_number, carrier_name, order_name, order_summary"
    )


async def test_settings_empty_ollama_url_skips_tags_call(hass, mock_config_entry):
    """OLLM-01 empty path: empty ollama_url skips async_get_tags and creates entry."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
        ) as mock_tags,
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert mock_tags.call_count == 0, "async_get_tags must NOT be called when url is empty"
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_settings_unreachable_ollama_url(hass, mock_config_entry):
    """CFG-01: unreachable ollama_url → form with errors['base']='ollama_cannot_connect'."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "http://127.0.0.1:9999",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            side_effect=OllamaTransientError("connection refused"),
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"]["base"] == "ollama_cannot_connect"


async def test_settings_model_not_found(hass, mock_config_entry):
    """CFG-02: model not in /api/tags → form with errors['base']='ollama_model_not_found'."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "http://192.168.0.190:11434",
        CONF_OLLAMA_MODEL: "qwen3.5:2b",
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["llama3.1:8b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"]["base"] == "ollama_model_not_found"
    assert result["description_placeholders"]["missing_model"] == "qwen3.5:2b"


async def test_settings_happy_path(hass, mock_config_entry):
    """Happy path: matching model in /api/tags → create_entry."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "http://192.168.0.190:11434",
        CONF_OLLAMA_MODEL: "qwen3.5:2b",
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["qwen3.5:2b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry"


async def test_settings_ollama_timeout_validation(hass, mock_config_entry):
    """OLLM-03: schema rejects timeout outside 10-300."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]

    # Valid boundary values
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: 10,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: 300,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )

    # Below min: 5
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: 5,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )

    # Above max: 301
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: 301,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )


async def test_settings_queue_maxlen_validation(hass, mock_config_entry):
    """QUE-01: schema rejects queue_maxlen outside 1-256."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]

    # Valid boundary values
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: 1,
        }
    )
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: 256,
        }
    )

    # Below min: 0
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: 0,
            }
        )

    # Above max: 257
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: 257,
            }
        )


async def test_settings_default_ollama_model(hass, mock_config_entry):
    """OLLM-02: default CONF_OLLAMA_MODEL is DEFAULT_OLLAMA_MODEL when options={}."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    assert model_key.default() == DEFAULT_OLLAMA_MODEL


async def test_settings_uses_shared_session(hass, mock_config_entry):
    """T-17-03-04: async_get_clientsession(self.hass) is called (inject-websession rule)."""
    handler, fake_entry = _make_handler_with_options(options={})
    fake_entry.hass = hass
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "http://192.168.0.190:11434",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["qwen3.5:2b"],
        ) as _mock_tags,
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ) as mock_session,
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert mock_session.call_count == 1, "async_get_clientsession must be called once"
    assert mock_session.call_args[0][0] is hass
    assert result["type"] == "create_entry"


async def test_custom_fields_returns_menu(hass, mock_config_entry):
    """Plan 04: async_step_custom_fields now returns a menu (stub replaced by real CRUD)."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_custom_fields(user_input=None)
    assert result["type"] == "menu"
    assert result["step_id"] == "custom_fields"


# ---------------------------------------------------------------------------
# Regression tests — retargeted from async_step_init to async_step_settings
# ---------------------------------------------------------------------------


async def test_options_flow_shows_form_with_defaults(hass, mock_config_entry):
    """EMAIL-05: First open of options flow shows form pre-filled with defaults."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "settings"
    schema = result["data_schema"]
    # vol.Schema stores defaults on each Required key
    schema_dict = {str(k): k for k in schema.schema}
    poll_key = schema_dict[CONF_POLL_INTERVAL]
    query_key = schema_dict[CONF_GMAIL_QUERY]
    assert poll_key.default() == DEFAULT_POLL_INTERVAL
    assert query_key.default() == DEFAULT_GMAIL_QUERY


async def test_options_flow_saves_valid_input(hass, mock_config_entry):
    """EMAIL-05: Submitting valid options saves to entry.options."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 60,
        CONF_GMAIL_QUERY: "from:test",
        CONF_RESCAN_WINDOW_DAYS: 30,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_poll_interval_validation(hass, mock_config_entry):
    """EMAIL-05: voluptuous Range(min=5, max=1440) rejects values outside range."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]

    # Valid: in range
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )
    schema(
        {
            CONF_POLL_INTERVAL: 5,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )
    schema(
        {
            CONF_POLL_INTERVAL: 1440,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: 30,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )

    # Invalid: below min
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 4,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )

    # Invalid: above max
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 1441,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: 30,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )


async def test_gmail_query_default(hass, mock_config_entry):
    """EMAIL-05: Form default for CONF_GMAIL_QUERY equals DEFAULT_GMAIL_QUERY when no override."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    query_key = schema_dict[CONF_GMAIL_QUERY]
    assert query_key.default() == DEFAULT_GMAIL_QUERY

    # When entry.options has an override, default reflects it
    handler2, fake_entry2 = _make_handler_with_options(options={CONF_GMAIL_QUERY: "from:custom"})
    with patch.object(
        type(handler2), "config_entry", new_callable=PropertyMock, return_value=fake_entry2
    ):
        result2 = await handler2.async_step_settings(user_input=None)
    schema2 = result2["data_schema"]
    schema2_dict = {str(k): k for k in schema2.schema}
    query_key2 = schema2_dict[CONF_GMAIL_QUERY]
    assert query_key2.default() == "from:custom"


# ---------------------------------------------------------------------------
# IMAP options flow branch (Phase 9) — retargeted to async_step_settings
# ---------------------------------------------------------------------------


async def test_options_flow_imap_shows_imap_search_field(hass, mock_imap_config_entry):
    """Phase 9 D-07: IMAP entry options form shows CONF_IMAP_SEARCH, not CONF_GMAIL_QUERY."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "settings"
    schema = result["data_schema"]
    schema_keys = [str(k) for k in schema.schema]
    assert CONF_IMAP_SEARCH in schema_keys, "IMAP options form must show imap_search field"
    assert CONF_GMAIL_QUERY not in schema_keys, "IMAP options form must NOT show gmail_query field"


async def test_options_flow_imap_saves_imap_search(hass, mock_imap_config_entry):
    """Phase 9 D-07: Submitting IMAP options saves imap_search to entry.options."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 60,
        CONF_IMAP_SEARCH: 'SUBJECT "tracking"',
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_options_flow_imap_shows_verify_tls_field(hass, mock_imap_config_entry):
    """CR-01: IMAP entry options form shows the imap_verify_tls toggle."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema_keys = [str(k) for k in result["data_schema"].schema]
    assert CONF_IMAP_VERIFY_TLS in schema_keys, "IMAP options form must show imap_verify_tls"


async def test_options_flow_gmail_hides_verify_tls_field(hass, mock_config_entry):
    """CR-01: Gmail entry options form does NOT show the IMAP-only imap_verify_tls toggle."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema_keys = [str(k) for k in result["data_schema"].schema]
    assert CONF_IMAP_VERIFY_TLS not in schema_keys, (
        "Gmail options form must NOT show imap_verify_tls"
    )


async def test_options_flow_imap_saves_verify_tls_false(hass, mock_imap_config_entry):
    """CR-01: submitting imap_verify_tls=False persists the opt-out to entry.options."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 60,
        CONF_IMAP_SEARCH: 'SUBJECT "tracking"',
        CONF_IMAP_VERIFY_TLS: False,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_IMAP_VERIFY_TLS] is False


async def test_options_flow_imap_search_rejects_control_chars(hass, mock_imap_config_entry):
    """WR-01: an imap_search containing CR/LF is rejected at entry time with a field error."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 60,
        CONF_IMAP_SEARCH: 'SUBJECT "shipped"\r\nUID STORE 1 +FLAGS \\Deleted',
        CONF_IMAP_VERIFY_TLS: True,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "form", "control characters must NOT create an entry"
    assert result["errors"] == {CONF_IMAP_SEARCH: "invalid_imap_search"}


async def test_options_flow_gmail_still_shows_gmail_query(hass, mock_config_entry):
    """Phase 9 backwards compatibility: Gmail entry options form still shows gmail_query field."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]
    schema_keys = [str(k) for k in schema.schema]
    assert CONF_GMAIL_QUERY in schema_keys, "Gmail entry must still show gmail_query field"
    assert CONF_IMAP_SEARCH not in schema_keys, "Gmail entry must NOT show imap_search field"


# ---------------------------------------------------------------------------
# QF-02: rescan_window_days option tests — retargeted to async_step_settings
# ---------------------------------------------------------------------------


async def test_gmail_options_flow_includes_rescan_window(hass, mock_config_entry):
    """QF-02: Gmail entry options schema includes CONF_RESCAN_WINDOW_DAYS with correct defaults."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    assert CONF_RESCAN_WINDOW_DAYS in schema_dict, (
        "Gmail options form must include rescan_window_days field"
    )
    rescan_key = schema_dict[CONF_RESCAN_WINDOW_DAYS]
    assert rescan_key.default() == DEFAULT_RESCAN_WINDOW_DAYS, (
        f"Default must be {DEFAULT_RESCAN_WINDOW_DAYS}, got {rescan_key.default()}"
    )
    # Validate range enforced: too low
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: MIN_RESCAN_WINDOW_DAYS - 1,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )
    # Validate range enforced: too high
    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_POLL_INTERVAL: 30,
                CONF_GMAIL_QUERY: "from:test",
                CONF_RESCAN_WINDOW_DAYS: MAX_RESCAN_WINDOW_DAYS + 1,
                CONF_OLLAMA_URL: "",
                CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
                CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            }
        )
    # Valid boundary values
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: MIN_RESCAN_WINDOW_DAYS,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "from:test",
            CONF_RESCAN_WINDOW_DAYS: MAX_RESCAN_WINDOW_DAYS,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )


async def test_imap_options_flow_excludes_rescan_window(hass, mock_imap_config_entry):
    """QF-02: IMAP entry options form must NOT include CONF_RESCAN_WINDOW_DAYS (Gmail-only)."""
    handler, fake_entry = _make_imap_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_keys = [str(k) for k in schema.schema]
    assert CONF_RESCAN_WINDOW_DAYS not in schema_keys, (
        "IMAP options form must NOT include rescan_window_days (Gmail-only feature)"
    )


async def test_options_flow_persists_rescan_window(hass, mock_config_entry):
    """QF-02: Submitting form with rescan_window_days=90 persists the value in entry.options."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {
        CONF_POLL_INTERVAL: 30,
        CONF_GMAIL_QUERY: "from:shopify",
        CONF_RESCAN_WINDOW_DAYS: 90,
        CONF_DEBUG_MODE: False,
        CONF_OLLAMA_URL: "",
        CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
    }
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_RESCAN_WINDOW_DAYS] == 90, (
        "rescan_window_days=90 must be persisted in entry.options"
    )


# ---------------------------------------------------------------------------
# Plan 04: FLD-01 / FLD-02 — custom fields CRUD (Tests 1–16)
# ---------------------------------------------------------------------------


async def test_custom_fields_menu_empty(hass, mock_config_entry):
    """Test 1 (RED): empty options → menu with only add_custom_field + current_fields='none'."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_custom_fields(user_input=None)
    assert result["type"] == "menu"
    assert result["step_id"] == "custom_fields"
    assert result["menu_options"] == ["add_custom_field"]
    assert result["description_placeholders"]["current_fields"] == "none"


async def test_custom_fields_menu_with_existing(hass, mock_config_entry):
    """Test 2: one existing field → menu includes remove_custom_field + correct placeholder."""
    handler, fake_entry = _make_handler_with_options(
        options={CONF_CUSTOM_FIELDS: [{"name": "estimated_delivery", "description": "ISO 8601"}]}
    )
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_custom_fields(user_input=None)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["add_custom_field", "remove_custom_field"]
    assert result["description_placeholders"]["current_fields"] == "estimated_delivery"


async def test_add_custom_field_shows_form(hass, mock_config_entry):
    """Test 3: async_step_add_custom_field(None) returns form at step_id='add_custom_field'."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "add_custom_field"
    schema_keys = [str(k) for k in result["data_schema"].schema]
    assert CONF_FIELD_NAME in schema_keys
    assert CONF_FIELD_DESCRIPTION in schema_keys


async def test_add_custom_field_happy_path(hass, mock_config_entry):
    """Test 4 (FLD-02 happy path): valid name + description → create_entry with field stored."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "estimated_delivery", CONF_FIELD_DESCRIPTION: "ISO 8601 date"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_CUSTOM_FIELDS] == [
        {"name": "estimated_delivery", "description": "ISO 8601 date"}
    ]


async def test_add_custom_field_appends_to_existing(hass, mock_config_entry):
    """Test 5 (FLD-02 append): adding to existing list preserves prior entry + appends new."""
    handler, fake_entry = _make_handler_with_options(
        options={CONF_CUSTOM_FIELDS: [{"name": "estimated_delivery", "description": None}]}
    )
    user_input = {CONF_FIELD_NAME: "shipping_method"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_CUSTOM_FIELDS] == [
        {"name": "estimated_delivery", "description": None},
        {"name": "shipping_method", "description": None},
    ]


async def test_add_custom_field_empty_description_normalized_to_none(hass, mock_config_entry):
    """Test 6 (FLD-02 empty description → None): '' is stored as None, not empty string."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "shipping_method", CONF_FIELD_DESCRIPTION: ""}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "create_entry"
    stored = result["data"][CONF_CUSTOM_FIELDS][0]
    assert stored == {"name": "shipping_method", "description": None}
    assert stored["description"] is None, "Empty description must be stored as None, not ''"


async def test_add_custom_field_absent_description_normalized_to_none(hass, mock_config_entry):
    """Test 7 (FLD-02 missing description key → None): omitting key stores description=None."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "shipping_method"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "create_entry"
    stored = result["data"][CONF_CUSTOM_FIELDS][0]
    assert stored["description"] is None


async def test_add_custom_field_locked_tracking_number(hass, mock_config_entry):
    """Test 8 (FLD-01): 'tracking_number' rejected with field-scoped locked_field_collision."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "tracking_number"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "locked_field_collision"
    # Entry must NOT have been updated
    assert CONF_CUSTOM_FIELDS not in fake_entry.options


async def test_add_custom_field_locked_carrier_name(hass, mock_config_entry):
    """Test 8b (FLD-01): 'carrier_name' rejected with locked_field_collision."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "carrier_name"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "locked_field_collision"


async def test_add_custom_field_locked_order_name(hass, mock_config_entry):
    """Test 8c (FLD-01): 'order_name' rejected with locked_field_collision."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "order_name"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "locked_field_collision"


async def test_add_custom_field_invalid_uppercase(hass, mock_config_entry):
    """Test 9 (FLD-02 invalid — uppercase): 'Foo' rejected with invalid_field_name."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "Foo"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "invalid_field_name"


async def test_add_custom_field_invalid_space(hass, mock_config_entry):
    """Test 10 (FLD-02 invalid — space): 'foo bar' rejected with invalid_field_name."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "foo bar"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "invalid_field_name"


async def test_add_custom_field_invalid_leading_digit(hass, mock_config_entry):
    """Test 11 (FLD-02 invalid — leading digit): '1foo' rejected with invalid_field_name."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "1foo"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "invalid_field_name"


async def test_add_custom_field_invalid_too_long(hass, mock_config_entry):
    """Test 12 (FLD-02 invalid — too long): 33-char name rejected with invalid_field_name."""
    handler, fake_entry = _make_handler_with_options(options={})
    # 33 chars: starts with letter, rest lowercase — too long for _FIELD_NAME_RE {0,31} = max 32
    long_name = "a" + "b" * 32  # 33 chars total
    user_input = {CONF_FIELD_NAME: long_name}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert result["errors"][CONF_FIELD_NAME] == "invalid_field_name"


async def test_remove_custom_field_shows_selector(hass, mock_config_entry):
    """Test 13: remove step shows vol.In selector with existing field names."""
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_CUSTOM_FIELDS: [
                {"name": "estimated_delivery", "description": None},
                {"name": "shipping_method", "description": None},
            ]
        }
    )
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_remove_custom_field(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "remove_custom_field"
    # The schema's CONF_FIELD_NAME validator must be vol.In containing both names
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    assert CONF_FIELD_NAME in schema_dict
    # vol.In validator: both names must be acceptable, others must not
    schema({CONF_FIELD_NAME: "estimated_delivery"})
    schema({CONF_FIELD_NAME: "shipping_method"})
    with pytest.raises(vol.Invalid):
        schema({CONF_FIELD_NAME: "nonexistent_field"})


async def test_remove_custom_field_happy_path(hass, mock_config_entry):
    """Test 14 (FLD-02 remove): removing 'estimated_delivery' leaves only 'shipping_method'."""
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_CUSTOM_FIELDS: [
                {"name": "estimated_delivery", "description": None},
                {"name": "shipping_method", "description": None},
            ]
        }
    )
    user_input = {CONF_FIELD_NAME: "estimated_delivery"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_remove_custom_field(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_CUSTOM_FIELDS] == [{"name": "shipping_method", "description": None}]


async def test_add_custom_field_stateless_across_calls(hass, mock_config_entry):
    """Test 15 (Pitfall 2): two consecutive add calls each read options fresh — no shared state."""
    # First call: start from one field
    handler1, fake_entry1 = _make_handler_with_options(
        options={CONF_CUSTOM_FIELDS: [{"name": "estimated_delivery", "description": None}]}
    )
    user_input1 = {CONF_FIELD_NAME: "shipping_method"}
    with patch.object(
        type(handler1), "config_entry", new_callable=PropertyMock, return_value=fake_entry1
    ):
        result1 = await handler1.async_step_add_custom_field(user_input=user_input1)
    assert result1["type"] == "create_entry"
    fields1 = result1["data"][CONF_CUSTOM_FIELDS]
    assert len(fields1) == 2
    assert {"name": "estimated_delivery", "description": None} in fields1
    assert {"name": "shipping_method", "description": None} in fields1

    # Second call: independent handler from same starting options — result must also have 2 fields
    handler2, fake_entry2 = _make_handler_with_options(
        options={CONF_CUSTOM_FIELDS: [{"name": "estimated_delivery", "description": None}]}
    )
    user_input2 = {CONF_FIELD_NAME: "carrier_code"}
    with patch.object(
        type(handler2), "config_entry", new_callable=PropertyMock, return_value=fake_entry2
    ):
        result2 = await handler2.async_step_add_custom_field(user_input=user_input2)
    assert result2["type"] == "create_entry"
    fields2 = result2["data"][CONF_CUSTOM_FIELDS]
    assert len(fields2) == 2
    assert {"name": "estimated_delivery", "description": None} in fields2
    assert {"name": "carrier_code", "description": None} in fields2


async def test_add_custom_field_locked_collision_is_field_scoped(hass, mock_config_entry):
    """Test 16 (FLD-01): locked-name collision sets errors[CONF_FIELD_NAME], NOT errors['base']."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_FIELD_NAME: "tracking_number"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_add_custom_field(user_input=user_input)
    assert result["type"] == "form"
    assert CONF_FIELD_NAME in result["errors"], "Error must be field-scoped to CONF_FIELD_NAME"
    assert "base" not in result["errors"], "Error must NOT be on 'base' (must be field-scoped)"
    assert result["errors"][CONF_FIELD_NAME] == "locked_field_collision"


# ---------------------------------------------------------------------------
# Localhost probe tests (T-probe-01 / T-probe-02 / T-probe-03)
# ---------------------------------------------------------------------------


async def test_probe_localhost_prefills_url_when_ollama_running(hass, mock_config_entry):
    """T-probe-01: When stored URL is empty and probe finds localhost Ollama, URL field is pre-filled."""
    handler, fake_entry = _make_handler_with_options(options={})
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch.object(
            OptionsFlowHandler,
            "_probe_ollama_localhost",
            new=AsyncMock(return_value=_OLLAMA_LOCALHOST_URL),
        ),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["qwen3.5:2b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    url_key = schema_dict[CONF_OLLAMA_URL]
    assert url_key.default() == _OLLAMA_LOCALHOST_URL, (
        "URL field must be pre-filled with localhost URL when probe succeeds"
    )


async def test_probe_localhost_no_prefill_when_unreachable(hass, mock_config_entry):
    """T-probe-02: When probe finds nothing (autouse suppresses it), URL field default stays empty."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)

    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    url_key = schema_dict[CONF_OLLAMA_URL]
    assert url_key.default() == "", "URL field must stay empty when probe finds no Ollama instance"


async def test_probe_localhost_skipped_when_url_already_stored(hass, mock_config_entry):
    """T-probe-03: When a URL is already stored in options, probe is never called."""
    stored = "http://192.168.0.10:11434"
    handler, fake_entry = _make_handler_with_options(options={CONF_OLLAMA_URL: stored})
    probe_mock = AsyncMock(return_value=_OLLAMA_LOCALHOST_URL)
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch.object(OptionsFlowHandler, "_probe_ollama_localhost", new=probe_mock),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=[],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert probe_mock.call_count == 0, "Probe must not run when a URL is already stored"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    url_key = schema_dict[CONF_OLLAMA_URL]
    assert url_key.default() == stored, "Stored URL must be used as default, not the probe result"


# ---------------------------------------------------------------------------
# QUICK-260622-j6w: dropdown model field (Tests A-D)
# ---------------------------------------------------------------------------


async def test_model_field_dropdown_when_tags_available(hass, mock_config_entry):
    """Test A: stored URL set + /api/tags returns models → CONF_OLLAMA_MODEL is SelectSelector.

    Exercises the Gmail/else branch (the primary branch).
    """
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_OLLAMA_URL: "http://192.168.0.190:11434",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        }
    )
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["qwen3.5:2b", "llama3.1:8b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    model_validator = schema.schema[model_key]

    assert isinstance(model_validator, SelectSelector), (
        "CONF_OLLAMA_MODEL must be a SelectSelector when /api/tags returns models"
    )
    option_values = [
        opt if isinstance(opt, str) else opt.get("value", opt)
        for opt in model_validator.config["options"]
    ]
    assert "qwen3.5:2b" in option_values, "qwen3.5:2b must be in dropdown options"
    assert "llama3.1:8b" in option_values, "llama3.1:8b must be in dropdown options"


async def test_model_field_dropdown_imap_branch(hass, mock_imap_config_entry):
    """Test A (IMAP branch): stored URL + /api/tags returns models → SelectSelector on IMAP form."""
    handler, fake_entry = _make_imap_handler_with_options(
        options={
            CONF_OLLAMA_URL: "http://192.168.0.190:11434",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        }
    )
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            return_value=["qwen3.5:2b", "llama3.1:8b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    model_validator = schema.schema[model_key]

    assert isinstance(model_validator, SelectSelector), (
        "IMAP branch: CONF_OLLAMA_MODEL must be SelectSelector when tags are available"
    )


async def test_model_field_text_fallback_on_connection_error(hass, mock_config_entry):
    """Test B: stored URL + /api/tags raises OllamaTransientError → CONF_OLLAMA_MODEL is str.

    The form must still render without error (no exception propagates to the user).
    """
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_OLLAMA_URL: "http://192.168.0.190:11434",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        }
    )
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            side_effect=OllamaTransientError("connection refused"),
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert result["type"] == "form", "Form must still render when /api/tags is unreachable"
    assert not result.get("errors"), "No errors must be set when fallback to text field occurs"

    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    model_validator = schema.schema[model_key]

    assert not isinstance(model_validator, SelectSelector), (
        "CONF_OLLAMA_MODEL must fall back to plain str validator on connection error"
    )


async def test_model_field_text_when_url_empty(hass, mock_config_entry):
    """Test C: stored URL empty → no fetch attempted, CONF_OLLAMA_MODEL is plain str."""
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        }
    )
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
        ) as mock_tags,
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert mock_tags.call_count == 0, "async_get_tags must NOT be called when stored URL is empty"
    assert result["type"] == "form"

    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    model_validator = schema.schema[model_key]

    assert not isinstance(model_validator, SelectSelector), (
        "CONF_OLLAMA_MODEL must be plain str validator when stored URL is empty"
    )


async def test_model_field_dropdown_includes_stored_model_not_in_tags(hass, mock_config_entry):
    """Test D: stored model NOT in returned tags → dropdown still includes stored model.

    The currently-configured model must always be selectable, even if it is no
    longer present in the live /api/tags list (e.g. model was deleted from server).
    """
    stored_model = "old-model:7b"
    handler, fake_entry = _make_handler_with_options(
        options={
            CONF_OLLAMA_URL: "http://192.168.0.190:11434",
            CONF_OLLAMA_MODEL: stored_model,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
        }
    )
    with (
        patch.object(
            type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
        ),
        patch.object(type(handler), "hass", new_callable=PropertyMock, return_value=hass),
        patch(
            "custom_components.shop2parcel.options_flow.OllamaClient.async_get_tags",
            # stored_model is NOT in the returned list
            return_value=["qwen3.5:2b", "llama3.1:8b"],
        ),
        patch(
            "custom_components.shop2parcel.options_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        result = await handler.async_step_settings(user_input=None)

    assert result["type"] == "form"
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    model_key = schema_dict[CONF_OLLAMA_MODEL]
    model_validator = schema.schema[model_key]

    assert isinstance(model_validator, SelectSelector), (
        "CONF_OLLAMA_MODEL must still be a SelectSelector"
    )
    option_values = [
        opt if isinstance(opt, str) else opt.get("value", opt)
        for opt in model_validator.config["options"]
    ]
    assert stored_model in option_values, (
        f"Stored model '{stored_model}' must be included in dropdown options even if not in /api/tags"
    )


# ---------------------------------------------------------------------------
# QUICK-260630-tfz: gmail_query optional — empty/whitespace coerced to DEFAULT_GMAIL_QUERY
# ---------------------------------------------------------------------------

_GMAIL_BASE_INPUT = {
    CONF_POLL_INTERVAL: 30,
    CONF_RESCAN_WINDOW_DAYS: DEFAULT_RESCAN_WINDOW_DAYS,
    CONF_DEBUG_MODE: False,
    CONF_OLLAMA_URL: "",  # empty URL skips async_get_tags call
    CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
    CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
}


async def test_gmail_query_empty_string_persists_default(hass, mock_config_entry):
    """Test A (QUICK-260630-tfz): submitting gmail_query="" persists DEFAULT_GMAIL_QUERY, not "".

    An empty string matches ALL Gmail messages, causing a full-inbox scan
    (DoS against Gmail API quota and the HA event loop — T-tfz-01).
    The submit handler must coerce "" to DEFAULT_GMAIL_QUERY before create_entry.
    """
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {**_GMAIL_BASE_INPUT, CONF_GMAIL_QUERY: ""}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry", f"Expected create_entry, got {result['type']}"
    assert result["data"][CONF_GMAIL_QUERY] == DEFAULT_GMAIL_QUERY, (
        f"Empty gmail_query must be coerced to DEFAULT_GMAIL_QUERY ({DEFAULT_GMAIL_QUERY!r}), "
        f"got {result['data'][CONF_GMAIL_QUERY]!r}"
    )


async def test_gmail_query_whitespace_persists_default(hass, mock_config_entry):
    """Test B (QUICK-260630-tfz): submitting gmail_query="   " persists DEFAULT_GMAIL_QUERY.

    Whitespace-only strings are semantically empty and would produce a broken
    Gmail query — they must be coerced to DEFAULT_GMAIL_QUERY at write time.
    """
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {**_GMAIL_BASE_INPUT, CONF_GMAIL_QUERY: "   "}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry", f"Expected create_entry, got {result['type']}"
    assert result["data"][CONF_GMAIL_QUERY] == DEFAULT_GMAIL_QUERY, (
        f"Whitespace-only gmail_query must be coerced to DEFAULT_GMAIL_QUERY ({DEFAULT_GMAIL_QUERY!r}), "
        f"got {result['data'][CONF_GMAIL_QUERY]!r}"
    )


async def test_gmail_query_non_empty_preserved_verbatim(hass, mock_config_entry):
    """Test C (QUICK-260630-tfz): submitting a non-empty gmail_query persists it unchanged.

    The coercion must only fire for empty/whitespace inputs — real user queries
    must not be silently replaced with the default.
    """
    handler, fake_entry = _make_handler_with_options(options={})
    custom_query = "from:shopify"
    user_input = {**_GMAIL_BASE_INPUT, CONF_GMAIL_QUERY: custom_query}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=user_input)
    assert result["type"] == "create_entry", f"Expected create_entry, got {result['type']}"
    assert result["data"][CONF_GMAIL_QUERY] == custom_query, (
        f"Non-empty gmail_query must be persisted verbatim ({custom_query!r}), "
        f"got {result['data'][CONF_GMAIL_QUERY]!r}"
    )


async def test_gmail_query_schema_accepts_empty_string(hass, mock_config_entry):
    """Test D (QUICK-260630-tfz): the schema must accept gmail_query="" (no vol.Invalid raised).

    The current schema uses vol.Required + vol.Length(min=1) which rejects "".
    After the change, vol.Optional + vol.Length(max=500) (no min=1) must allow
    it through — the DoS risk is handled by write-time coercion, not schema rejection.
    """
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_settings(user_input=None)
    schema = result["data_schema"]
    # Calling the schema with gmail_query="" must NOT raise vol.Invalid.
    schema(
        {
            CONF_POLL_INTERVAL: 30,
            CONF_GMAIL_QUERY: "",
            CONF_RESCAN_WINDOW_DAYS: DEFAULT_RESCAN_WINDOW_DAYS,
            CONF_OLLAMA_URL: "",
            CONF_OLLAMA_MODEL: DEFAULT_OLLAMA_MODEL,
            CONF_OLLAMA_TIMEOUT: DEFAULT_OLLAMA_TIMEOUT,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        }
    )
