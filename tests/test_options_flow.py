"""Tests for Shop2Parcel options flow — covers EMAIL-05."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import voluptuous as vol

from custom_components.shop2parcel.const import (
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_POLL_INTERVAL,
)
from custom_components.shop2parcel.options_flow import OptionsFlowHandler


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


async def test_options_flow_shows_form_with_defaults(hass, mock_config_entry):
    """EMAIL-05: First open of options flow shows form pre-filled with defaults."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    schema = result["data_schema"]
    # vol.Schema stores defaults on each Required key
    schema_dict = {str(k): k for k in schema.schema}
    poll_key = schema_dict[CONF_POLL_INTERVAL]
    query_key = schema_dict[CONF_GMAIL_QUERY]
    assert poll_key.default() == DEFAULT_POLL_INTERVAL
    assert query_key.default() == DEFAULT_GMAIL_QUERY


async def test_options_flow_saves_valid_input(hass, mock_config_entry):
    """EMAIL-05: Submitting valid poll_interval saves to entry.options."""
    handler, fake_entry = _make_handler_with_options(options={})
    user_input = {CONF_POLL_INTERVAL: 60, CONF_GMAIL_QUERY: "from:test"}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_poll_interval_validation(hass, mock_config_entry):
    """EMAIL-05: voluptuous Range(min=5, max=1440) rejects values outside range."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        # Show form to get the schema
        result = await handler.async_step_init(user_input=None)
    schema = result["data_schema"]

    # Valid: in range
    schema({CONF_POLL_INTERVAL: 30, CONF_GMAIL_QUERY: "from:test"})
    schema({CONF_POLL_INTERVAL: 5, CONF_GMAIL_QUERY: "from:test"})
    schema({CONF_POLL_INTERVAL: 1440, CONF_GMAIL_QUERY: "from:test"})

    # Invalid: below min
    with pytest.raises(vol.Invalid):
        schema({CONF_POLL_INTERVAL: 4, CONF_GMAIL_QUERY: "from:test"})

    # Invalid: above max
    with pytest.raises(vol.Invalid):
        schema({CONF_POLL_INTERVAL: 1441, CONF_GMAIL_QUERY: "from:test"})


async def test_gmail_query_default(hass, mock_config_entry):
    """EMAIL-05: Form default for CONF_GMAIL_QUERY equals DEFAULT_GMAIL_QUERY when no override."""
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=None)
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    query_key = schema_dict[CONF_GMAIL_QUERY]
    assert query_key.default() == DEFAULT_GMAIL_QUERY

    # When entry.options has an override, default reflects it
    handler2, fake_entry2 = _make_handler_with_options(options={CONF_GMAIL_QUERY: "from:custom"})
    with patch.object(
        type(handler2), "config_entry", new_callable=PropertyMock, return_value=fake_entry2
    ):
        result2 = await handler2.async_step_init(user_input=None)
    schema2 = result2["data_schema"]
    schema2_dict = {str(k): k for k in schema2.schema}
    query_key2 = schema2_dict[CONF_GMAIL_QUERY]
    assert query_key2.default() == "from:custom"


# ---------------------------------------------------------------------------
# IMAP options flow branch (Phase 9)
# ---------------------------------------------------------------------------


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


async def test_options_flow_imap_shows_imap_search_field(hass, mock_imap_config_entry):
    """Phase 9 D-07: IMAP entry options form shows CONF_IMAP_SEARCH, not CONF_GMAIL_QUERY."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        CONF_GMAIL_QUERY,
        CONF_IMAP_SEARCH,
        DEFAULT_IMAP_SEARCH,
    )

    handler, fake_entry = _make_imap_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=None)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    schema = result["data_schema"]
    schema_keys = [str(k) for k in schema.schema]
    assert CONF_IMAP_SEARCH in schema_keys, "IMAP options form must show imap_search field"
    assert CONF_GMAIL_QUERY not in schema_keys, "IMAP options form must NOT show gmail_query field"


async def test_options_flow_imap_saves_imap_search(hass, mock_imap_config_entry):
    """Phase 9 D-07: Submitting IMAP options saves imap_search to entry.options."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        CONF_IMAP_SEARCH,
        CONF_POLL_INTERVAL,
    )

    handler, fake_entry = _make_imap_handler_with_options(options={})
    user_input = {CONF_POLL_INTERVAL: 60, CONF_IMAP_SEARCH: 'SUBJECT "tracking"'}
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_options_flow_gmail_still_shows_gmail_query(hass, mock_config_entry):
    """Phase 9 backwards compatibility: Gmail entry options form still shows gmail_query field."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        CONF_GMAIL_QUERY,
        CONF_IMAP_SEARCH,
    )

    # Gmail entry: connection_type="gmail" explicitly set in fake_entry.data
    handler, fake_entry = _make_handler_with_options(options={})
    with patch.object(
        type(handler), "config_entry", new_callable=PropertyMock, return_value=fake_entry
    ):
        result = await handler.async_step_init(user_input=None)
    schema = result["data_schema"]
    schema_keys = [str(k) for k in schema.schema]
    assert CONF_GMAIL_QUERY in schema_keys, "Gmail entry must still show gmail_query field"
    assert CONF_IMAP_SEARCH not in schema_keys, "Gmail entry must NOT show imap_search field"
