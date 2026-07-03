"""Options flow for Shop2Parcel — menu-first multi-step form.

Phase 17: Rewritten to a menu-first pattern (D-01). async_step_init now returns
a top-level menu with two options: 'settings' (main settings form) and
'custom_fields' (CRUD for custom Ollama extraction fields).

async_step_settings carries ALL existing settings (poll interval, Gmail/IMAP
query, rescan window, debug mode) PLUS all new Ollama fields (URL, model,
timeout, queue_maxlen). Saving with a non-empty Ollama URL validates the server
via GET /api/tags before persisting.

async_step_custom_fields returns a dynamic sub-menu listing current fields and
offering add/remove options. async_step_add_custom_field validates name via
_FIELD_NAME_RE (imported from ollama_extractor) and rejects locked-field
collisions. async_step_remove_custom_field shows a vol.In selector.

Locked decisions:
- D-01: async_step_init is a menu, not a form.
- D-02: Step IDs: init (menu), settings (form), custom_fields_menu (sub-menu),
        add_custom_field (form), remove_custom_field (form).
- D-03: Both Gmail and IMAP branches include Ollama fields (connection-type-agnostic).
- D-04: OllamaClient.async_get_tags is a @staticmethod; no instance needed.
- D-06: Name validated by the imported _FIELD_NAME_RE regex; collision checked
        against LOCKED_OLLAMA_FIELDS; description=None when absent or empty.
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
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api.exceptions import OllamaTransientError
from .api.ollama_client import OllamaClient
from .const import (
    CONF_CONNECTION_TYPE,
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
    CONF_STAGE2_ENABLED,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_IMAP_VERIFY_TLS,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUEUE_MAXLEN,
    DEFAULT_RESCAN_WINDOW_DAYS,
    LOCKED_OLLAMA_FIELDS,
    MAX_RESCAN_WINDOW_DAYS,
    MIN_RESCAN_WINDOW_DAYS,
)
from .extractors.ollama_extractor import _FIELD_NAME_RE

_OLLAMA_LOCALHOST_URL = "http://localhost:11434"
_OLLAMA_PROBE_TIMEOUT = 1.0


class OptionsFlowHandler(OptionsFlowWithReload):
    """Handle Shop2Parcel options flow.

    Subclassing OptionsFlowWithReload triggers automatic config entry reload
    on save — HA calls async_unload_entry + async_setup_entry with the new
    options dict, and the coordinator picks up the new poll interval.
    """

    async def _build_ollama_model_field(self, url: str = "") -> Any:
        """Return the voluptuous validator for CONF_OLLAMA_MODEL.

        When ``url`` is non-empty and /api/tags returns a non-empty list,
        returns a SelectSelector (dropdown) containing all available model tags.
        The currently-stored model is always included so a model not present on
        the live server (e.g. after a server restart) does not disappear from
        the selector.

        Falls back to bare ``str`` in any of these cases:
        - url is empty or whitespace-only
        - async_get_tags raises OllamaTransientError (server unreachable / non-200)
        - async_get_tags returns an empty list

        T-j6w-01: the stored CONF_OLLAMA_TIMEOUT bounds the fetch; a slow/
        unreachable server fails fast into the text fallback rather than hanging
        the HA options-flow render.
        """
        effective_url = url.strip()
        if not effective_url:
            return str

        session = async_get_clientsession(self.hass)
        timeout = self.config_entry.options.get(CONF_OLLAMA_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT)
        try:
            tags: list[str] = await OllamaClient.async_get_tags(session, effective_url, timeout)
        except OllamaTransientError:
            # T-j6w-03: swallow server error detail; surface only as text fallback.
            return str

        if not tags:
            return str

        # Ensure the currently-stored model is always in the options list so the
        # current value pre-selects correctly even when the server list has changed.
        stored_model = self.config_entry.options.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)
        if stored_model not in tags:
            tags = [stored_model, *tags]

        return SelectSelector(
            SelectSelectorConfig(
                options=sorted(tags),
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                sort=True,
            )
        )

    async def _probe_ollama_localhost(self) -> str:
        """Return _OLLAMA_LOCALHOST_URL if Ollama is listening there, else ''."""
        session = async_get_clientsession(self.hass)
        try:
            await OllamaClient.async_get_tags(session, _OLLAMA_LOCALHOST_URL, _OLLAMA_PROBE_TIMEOUT)
            return _OLLAMA_LOCALHOST_URL
        except OllamaTransientError:
            return ""

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

        The model field is rendered as a SelectSelector dropdown when the stored
        URL yields a live model list, and falls back to a free-text str field
        otherwise (see _build_ollama_model_field).
        """
        errors: dict[str, str] = {}
        stored_url = self.config_entry.options.get(CONF_OLLAMA_URL, "")
        if not stored_url and user_input is None:
            ollama_url_default = await self._probe_ollama_localhost()
        else:
            ollama_url_default = stored_url
        stage2_status = (
            "enabled" if ollama_url_default.strip() else "disabled (set Ollama URL to enable)"
        )
        description_placeholders: dict[str, str] = {
            "locked_fields": ", ".join(LOCKED_OLLAMA_FIELDS),
            "stage2_status": stage2_status,
        }

        conn_type = self.config_entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)

        # Build the model field once and reuse in both branches.
        model_field = await self._build_ollama_model_field(url=ollama_url_default)

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
                    # CR-01: TLS certificate verification opt-out (default True).
                    # Options value overrides the entry.data value written at setup —
                    # the coordinator reads options first, then data (see imap_coordinator).
                    vol.Required(
                        CONF_IMAP_VERIFY_TLS,
                        default=self.config_entry.options.get(
                            CONF_IMAP_VERIFY_TLS,
                            self.config_entry.data.get(
                                CONF_IMAP_VERIFY_TLS, DEFAULT_IMAP_VERIFY_TLS
                            ),
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_DEBUG_MODE,
                        default=self.config_entry.options.get(CONF_DEBUG_MODE, False),
                    ): bool,
                    vol.Optional(
                        CONF_OLLAMA_URL,
                        default=ollama_url_default,
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL
                        ),
                    ): model_field,
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
                    vol.Optional(
                        CONF_GMAIL_QUERY,
                        default=self.config_entry.options.get(
                            CONF_GMAIL_QUERY, DEFAULT_GMAIL_QUERY
                        ),
                        # Empty/whitespace submissions are coerced to DEFAULT_GMAIL_QUERY
                        # in the submit handler (see below) — preventing the full-inbox scan
                        # (DoS against Gmail API quota and HA event loop) at write time rather
                        # than schema time. max=500 mirrors Gmail's practical query length limit.
                    ): vol.All(str, vol.Length(max=500)),
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
                        default=ollama_url_default,
                    ): str,
                    vol.Required(
                        CONF_OLLAMA_MODEL,
                        default=self.config_entry.options.get(
                            CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL
                        ),
                    ): model_field,
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
            # WR-01: reject control characters (CR/LF/NUL, ...) in the IMAP search
            # string at entry time — imaplib performs no CRLF sanitization, so an
            # embedded \r\n would inject pipelined IMAP commands. The client
            # boundary re-checks in ImapClient._fetch_sync as defense-in-depth.
            imap_search = user_input.get(CONF_IMAP_SEARCH)
            if imap_search is not None and any(ord(c) < 32 or ord(c) == 127 for c in imap_search):
                errors[CONF_IMAP_SEARCH] = "invalid_imap_search"
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
                # Strip whitespace from URL before persisting (WR-01).
                user_input[CONF_OLLAMA_URL] = user_input.get(CONF_OLLAMA_URL, "").strip()
                # T-tfz-01: Coerce empty/whitespace gmail_query to DEFAULT_GMAIL_QUERY.
                # build_incremental_query("", n) yields " after:..." which scans the entire
                # inbox — exhausting Gmail API quota and blocking the HA event loop.
                # Only fires on the Gmail branch (IMAP submit path has no gmail_query key).
                if CONF_GMAIL_QUERY in user_input and not user_input[CONF_GMAIL_QUERY].strip():
                    user_input[CONF_GMAIL_QUERY] = DEFAULT_GMAIL_QUERY
                # Merge with existing options so CONF_CUSTOM_FIELDS is preserved (CR-01).
                new_options = dict(self.config_entry.options)
                new_options.update(user_input)
                # IN-01: drop the dead stage2_enabled key seeded by pre-1.5 entry
                # creation — Stage-2 enablement is derived from CONF_OLLAMA_URL in
                # async_setup_entry and nothing reads the stored key.
                new_options.pop(CONF_STAGE2_ENABLED, None)
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_custom_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sub-menu: list current custom fields; route to add or remove.

        'Remove custom field' is only shown when at least one field exists —
        an empty selector would be confusing (CONTEXT.md specifics).
        """
        existing = self.config_entry.options.get(CONF_CUSTOM_FIELDS, [])
        menu_options = ["add_custom_field"]
        if existing:
            menu_options.append("remove_custom_field")
        description_placeholders = {
            "current_fields": ", ".join(f["name"] for f in existing) or "none",
        }
        return self.async_show_menu(
            step_id="custom_fields",
            menu_options=menu_options,
            description_placeholders=description_placeholders,
        )

    async def async_step_add_custom_field(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new custom extraction field.

        Validates name with the imported _FIELD_NAME_RE regex (D-06) and
        rejects collisions with LOCKED_OLLAMA_FIELDS (FLD-01). Empty or absent
        description is stored as None (Pitfall 5 / T-17-04-05).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_FIELD_NAME, "")
            desc = user_input.get(CONF_FIELD_DESCRIPTION) or None  # "" → None

            # Order: regex check first, then locked-name check (FLD-01 / D-06)
            if not _FIELD_NAME_RE.fullmatch(name):
                errors[CONF_FIELD_NAME] = "invalid_field_name"
            elif name in LOCKED_OLLAMA_FIELDS:
                errors[CONF_FIELD_NAME] = "locked_field_collision"
            else:
                new_options = dict(self.config_entry.options)
                fields = list(new_options.get(CONF_CUSTOM_FIELDS, []))
                fields.append({"name": name, "description": desc})
                new_options[CONF_CUSTOM_FIELDS] = fields
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="add_custom_field",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FIELD_NAME): str,
                    vol.Optional(CONF_FIELD_DESCRIPTION): vol.Any(str, None),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_custom_field(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove an existing custom extraction field by name.

        The selector is constrained to existing field names via vol.In
        (CONTEXT.md Claude's Discretion remove selector).
        """
        existing = self.config_entry.options.get(CONF_CUSTOM_FIELDS, [])
        existing_names = [f["name"] for f in existing]

        if user_input is not None:
            name = user_input.get(CONF_FIELD_NAME)
            # Server-side guard: reject unknown names that bypass the vol.In UI check (WR-02).
            if name not in existing_names:
                return self.async_show_form(
                    step_id="remove_custom_field",
                    data_schema=vol.Schema({vol.Required(CONF_FIELD_NAME): vol.In(existing_names)}),
                    errors={"base": "invalid_field_name"},
                )
            new_options = dict(self.config_entry.options)
            new_options[CONF_CUSTOM_FIELDS] = [f for f in existing if f["name"] != name]
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="remove_custom_field",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FIELD_NAME): vol.In(existing_names),
                }
            ),
        )
