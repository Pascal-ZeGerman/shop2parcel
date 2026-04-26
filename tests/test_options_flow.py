"""Tests for Shop2Parcel options flow — covers EMAIL-05 (poll interval, Gmail query).

Wave 0 stubs per VALIDATION.md. Plan 02 implements options_flow.py and removes
the xfail decorators to flip these tests green.

Test fixtures (`hass`, `mock_config_entry`) come from tests/conftest.py.
"""
from __future__ import annotations

import pytest

from custom_components.shop2parcel.const import (  # noqa: F401
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_POLL_INTERVAL,
)


@pytest.mark.xfail(strict=True, reason="Plan 02 implements OptionsFlowHandler")
async def test_options_flow_shows_form_with_defaults(hass, mock_config_entry):
    """EMAIL-05: First open of options flow shows form pre-filled with defaults."""
    from custom_components.shop2parcel.options_flow import OptionsFlowHandler  # noqa: F401
    raise AssertionError("Plan 02 implements test_options_flow_shows_form_with_defaults")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements OptionsFlowHandler")
async def test_options_flow_saves_valid_input(hass, mock_config_entry):
    """EMAIL-05: Submitting valid poll_interval=60 saves to entry.options."""
    from custom_components.shop2parcel.options_flow import OptionsFlowHandler  # noqa: F401
    raise AssertionError("Plan 02 implements test_options_flow_saves_valid_input")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements OptionsFlowHandler")
async def test_poll_interval_validation(hass, mock_config_entry):
    """EMAIL-05: voluptuous Range(min=5, max=1440) rejects values outside range."""
    from custom_components.shop2parcel.options_flow import OptionsFlowHandler  # noqa: F401
    raise AssertionError("Plan 02 implements test_poll_interval_validation")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements OptionsFlowHandler")
async def test_gmail_query_default(hass, mock_config_entry):
    """EMAIL-05: Form default for CONF_GMAIL_QUERY equals DEFAULT_GMAIL_QUERY when entry has no override."""
    from custom_components.shop2parcel.options_flow import OptionsFlowHandler  # noqa: F401
    raise AssertionError("Plan 02 implements test_gmail_query_default")
