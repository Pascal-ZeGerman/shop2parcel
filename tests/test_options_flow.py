"""Tests for Shop2Parcel options flow — covers EMAIL-05."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.shop2parcel.const import (
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_POLL_INTERVAL,
)
from custom_components.shop2parcel.options_flow import OptionsFlowHandler


def _make_handler_with_options(options: dict) -> OptionsFlowHandler:
    """Construct OptionsFlowHandler with a fake config_entry.options via patching config_entry property."""
    handler = OptionsFlowHandler.__new__(OptionsFlowHandler)
    fake_entry = MagicMock()
    fake_entry.options = options
    # Patch the config_entry property on the instance to return our fake entry
    # We bypass the HA property by patching on the class temporarily is fragile;
    # instead patch it on the instance via __class__ interception with a descriptor approach.
    # The cleanest isolation: patch the config_entry *property* to return fake_entry.
    type(handler).config_entry = property(lambda self: fake_entry)
    return handler


async def test_options_flow_shows_form_with_defaults(hass, mock_config_entry):
    """EMAIL-05: First open of options flow shows form pre-filled with defaults."""
    handler = _make_handler_with_options(options={})
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
    handler = _make_handler_with_options(options={})
    user_input = {CONF_POLL_INTERVAL: 60, CONF_GMAIL_QUERY: "from:test"}
    result = await handler.async_step_init(user_input=user_input)
    assert result["type"] == "create_entry"
    assert result["data"] == user_input


async def test_poll_interval_validation(hass, mock_config_entry):
    """EMAIL-05: voluptuous Range(min=5, max=1440) rejects values outside range."""
    handler = _make_handler_with_options(options={})
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
    handler = _make_handler_with_options(options={})
    result = await handler.async_step_init(user_input=None)
    schema = result["data_schema"]
    schema_dict = {str(k): k for k in schema.schema}
    query_key = schema_dict[CONF_GMAIL_QUERY]
    assert query_key.default() == DEFAULT_GMAIL_QUERY

    # When entry.options has an override, default reflects it
    handler2 = _make_handler_with_options(options={CONF_GMAIL_QUERY: "from:custom"})
    result2 = await handler2.async_step_init(user_input=None)
    schema2 = result2["data_schema"]
    schema2_dict = {str(k): k for k in schema2.schema}
    query_key2 = schema2_dict[CONF_GMAIL_QUERY]
    assert query_key2.default() == "from:custom"
