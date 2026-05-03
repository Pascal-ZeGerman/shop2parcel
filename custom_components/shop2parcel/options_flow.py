"""Options flow for Shop2Parcel — poll interval + Gmail search query.

Phase 4: subclasses OptionsFlowWithReload (HA 2024.9+) so saving the form
automatically reloads the config entry — coordinator is re-instantiated with
the new poll interval. No manual update listener required (CONTEXT.md D-07).

Locked decisions:
- D-07: OptionsFlowWithReload, NOT manual entry.add_update_listener.
- D-08: CONF_POLL_INTERVAL int range 5..1440, default 30; CONF_GMAIL_QUERY str.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload

from .const import (
    CONF_GMAIL_QUERY,
    CONF_IMAP_SEARCH,
    CONF_POLL_INTERVAL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_POLL_INTERVAL,
)


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle Shop2Parcel options flow.

    Subclassing OptionsFlowWithReload triggers automatic config entry reload
    on save — HA calls async_unload_entry + async_setup_entry with the new
    options dict, and the coordinator picks up the new poll interval.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show form with current values; save and reload on submit."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        conn_type = self.config_entry.data.get("connection_type", "gmail")
        if conn_type == CONNECTION_TYPE_IMAP:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=5, max=1440)),
                    vol.Required(
                        CONF_IMAP_SEARCH,
                        default=self.config_entry.options.get(
                            CONF_IMAP_SEARCH, DEFAULT_IMAP_SEARCH
                        ),
                    ): vol.All(str, vol.Length(min=1, max=500)),
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=5, max=1440)),
                    vol.Required(
                        CONF_GMAIL_QUERY,
                        default=self.config_entry.options.get(
                            CONF_GMAIL_QUERY, DEFAULT_GMAIL_QUERY
                        ),
                        # min=1 prevents an empty query which matches ALL Gmail messages,
                        # causing the coordinator to attempt parsing every email in the inbox
                        # (DoS against Gmail API quota and the HA event loop).
                        # max=500 mirrors Gmail's practical query length limit.
                    ): vol.All(str, vol.Length(min=1, max=500)),
                }
            )
        return self.async_show_form(step_id="init", data_schema=schema)
