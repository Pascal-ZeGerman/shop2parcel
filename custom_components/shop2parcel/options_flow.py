"""Options flow for Shop2Parcel — menu-first multi-step form.

Phase 17: Rewritten to a menu-first pattern (D-01). async_step_init now returns
a top-level menu with two options: 'settings' (main settings form) and
'custom_fields' (CRUD for custom Ollama extraction fields — Plan 04 stub for now).

async_step_settings carries ALL existing settings (poll interval, Gmail/IMAP
query, rescan window, debug mode) PLUS all new Ollama fields (URL, model,
timeout, queue_maxlen). Saving with a non-empty Ollama URL validates the server
via GET /api/tags before persisting.

Locked decisions:
- D-01: async_step_init is a menu, not a form.
- D-02: Step IDs: init (menu), settings (form), custom_fields (Plan 04 CRUD).
- D-03: Both Gmail and IMAP branches include Ollama fields (connection-type-agnostic).
- D-04: OllamaClient.async_get_tags is a @staticmethod; no instance needed.
- D-07: OptionsFlowWithReload, NOT manual entry.add_update_listener.
- D-08: CONF_POLL_INTERVAL int range 5..1440, default 30.
- T-17-03-04: inject-websession rule; no new aiohttp.ClientSession inside the flow.
- T-17-03-05: CONF_STAGE2_ENABLED is NOT exposed as a form field (derived in __init__.py).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.exceptions import OllamaTransientError
from .api.ollama_client import OllamaClient
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_DEBUG_MODE,
    CONF_GMAIL_QUERY,
    CONF_IMAP_SEARCH,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT,
    CONF_OLLAMA_URL,
    CONF_POLL_INTERVAL,
    CONF_QUEUE_MAXLEN,
    CONF_RESCAN_WINDOW_DAYS,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUEUE_MAXLEN,
    DEFAULT_RESCAN_WINDOW_DAYS,
    LOCKED_OLLAMA_FIELDS,
    MAX_RESCAN_WINDOW_DAYS,
    MIN_RESCAN_WINDOW_DAYS,
)


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle Shop2Parcel options flow.

    Subclassing OptionsFlowWithReload triggers automatic config entry reload
    on save — HA calls async_unload_entry + async_setup_entry with the new
    options dict, and the coordinator picks up the new poll interval.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Top-level menu: route to settings or custom fields CRUD."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "custom_fields"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the full settings form; validate Ollama URL/model on submit.

        Both Gmail and IMAP branches include the Ollama fields (D-03).
        When ollama_url is non-empty, GET /api/tags is called to verify
        the server is reachable and the chosen model is available.
        """
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {
            "locked_fields": ", ".join(LOCKED_OLLAMA_FIELDS),
        }

        conn_type = self.config_entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)

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
                    vol.Optional(
                        CONF_DEBUG_MODE,
                        default=self.config_entry.options.get(CONF_DEBUG_MODE, False),
                    ): bool,
                    vol.Optional(
                        CONF_OLLAMA_URL,
                        default=self.config_entry.options.get(CONF_OLLAMA_URL, ""),
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL
                        ),
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_TIMEOUT,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT
                        ),
                    ): vol.All(int, vol.Range(min=10, max=300)),
                    vol.Required(
                        CONF_QUEUE_MAXLEN,
                        default=self.config_entry.options.get(
                            CONF_QUEUE_MAXLEN, DEFAULT_QUEUE_MAXLEN
                        ),
                    ): vol.All(int, vol.Range(min=1, max=256)),
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
                    # QF-02: Gmail-only rescan window. Allows widening the after: filter
                    # without clearing forwarded_ids — already-forwarded shipments are
                    # deduplicated before any ParcelApp POST, so increasing this value
                    # is safe (minor extra Gmail API calls at most).
                    vol.Required(
                        CONF_RESCAN_WINDOW_DAYS,
                        default=self.config_entry.options.get(
                            CONF_RESCAN_WINDOW_DAYS, DEFAULT_RESCAN_WINDOW_DAYS
                        ),
                    ): vol.All(
                        int, vol.Range(min=MIN_RESCAN_WINDOW_DAYS, max=MAX_RESCAN_WINDOW_DAYS)
                    ),
                    vol.Optional(
                        CONF_DEBUG_MODE,
                        default=self.config_entry.options.get(CONF_DEBUG_MODE, False),
                    ): bool,
                    vol.Optional(
                        CONF_OLLAMA_URL,
                        default=self.config_entry.options.get(CONF_OLLAMA_URL, ""),
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL
                        ),
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_TIMEOUT,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT
                        ),
                    ): vol.All(int, vol.Range(min=10, max=300)),
                    vol.Required(
                        CONF_QUEUE_MAXLEN,
                        default=self.config_entry.options.get(
                            CONF_QUEUE_MAXLEN, DEFAULT_QUEUE_MAXLEN
                        ),
                    ): vol.All(int, vol.Range(min=1, max=256)),
                }
            )

        if user_input is not None:
            url = user_input.get(CONF_OLLAMA_URL, "").strip()
            if url:
                session = async_get_clientsession(self.hass)
                timeout = user_input.get(CONF_OLLAMA_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT)
                try:
                    tags = await OllamaClient.async_get_tags(session, url, timeout)
                except OllamaTransientError:
                    errors["base"] = "ollama_cannot_connect"
                else:
                    model = user_input.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)
                    if model not in tags:
                        errors["base"] = "ollama_model_not_found"
                        description_placeholders["missing_model"] = model
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_custom_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Stub: Plan 04 replaces this stub with the real CRUD menu."""
        return self.async_abort(reason="not_implemented")
