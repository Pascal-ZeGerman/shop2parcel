"""GmailCoordinator — Gmail poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the Gmail OAuth2 + message-fetch + parse + forward cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace as dc_replace
from typing import Any, TypeVar, cast

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData, validate_carrier_format
from .api.exceptions import (
    GmailAuthError,
    GmailStaleTokenError,
    GmailTransientError,
    ParcelAppAlreadyAddedError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.gmail_client import GmailClient, extract_html_body, extract_text_body
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_DEBUG_MODE,
    CONF_ENABLE_BROAD_SCAN,
    CONF_GMAIL_QUERY,
    CONF_RESCAN_WINDOW_DAYS,
    DEFAULT_ENABLE_BROAD_SCAN,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_RESCAN_WINDOW_DAYS,
    MAX_RESCAN_WINDOW_DAYS,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    debug_mode_notification_id,
    normalize_tracking_number,
)
from .coordinator import (
    Shop2ParcelCoordinator,
    Stage2Job,  # noqa: F401 — type import; subclass calls _enqueue_stage2 which constructs Stage2Job
    _extract_email_meta,
    _sanitise_parser_error,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


def _extract_gmail_html(payload: dict) -> str | None:
    """Executor-side body extraction: HTML part, else escaped text/plain wrap.

    WR-06: extract_html_body / extract_text_body walk the MIME tree and
    base64-decode potentially multi-hundred-KB bodies — CPU-bound work batched
    into ONE executor job per message (with the <pre> wrap) instead of running
    on the HA event loop.

    PR4-I1: the text fallback escapes angle brackets/ampersands so plain-text
    bodies with raw '<', '>', '&' don't produce malformed HTML for
    BeautifulSoup; <pre> preserves newlines for downstream regex/text scanning.
    """
    html = extract_html_body(payload)
    if not html:
        text_body = extract_text_body(payload)
        if text_body:
            html = f"<html><body><pre>{_html_stdlib.escape(text_body)}</pre></body></html>"
    return html


class GmailCoordinator(Shop2ParcelCoordinator):
    """Coordinator for Gmail-connected Shop2Parcel entries."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._email_client = GmailClient(hass.async_add_executor_job)
        # Current Gmail access_token for the in-progress poll. Set at poll start; a
        # stale-token force-refresh (self-healing retry) updates it so every subsequent
        # per-message async_get_message call in the same poll uses the refreshed token.
        self._gmail_access_token: str | None = None
        if not entry.options.get(CONF_DEBUG_MODE, False):
            persistent_notification.async_dismiss(
                hass, notification_id=debug_mode_notification_id(entry.entry_id)
            )

    async def _async_force_refresh_gmail_token(self, implementation) -> str:
        """Force a fresh OAuth access_token, persist it, and return the new access_token.

        A FORCED refresh (implementation.async_refresh_token) is used deliberately: HA's
        async_ensure_token_valid() may consider the stored token still-valid-by-expiry and
        refuse to refresh a token Google has already rejected with a 401. async_refresh_token
        hits Google's token endpoint unconditionally and mints a new access_token.

        The refreshed token dict is persisted to the config entry via async_update_entry using
        a NEW dict (the config-entry data dict is immutable-by-convention — never mutate in place).
        Security: never log the token values.
        """
        assert self.config_entry is not None  # guaranteed by the caller
        data = self.config_entry.data
        old_token = data.get("token")
        if not isinstance(old_token, dict):
            # Should never happen (caller validated it), but fail safely as a transient error.
            raise GmailTransientError("Gmail OAuth token missing during force-refresh")
        new_token = await implementation.async_refresh_token(old_token)
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**data, "token": new_token}
        )
        access_token = new_token.get("access_token")
        if not access_token:
            raise GmailTransientError("Forced Gmail token refresh returned no access_token")
        self._gmail_access_token = access_token
        return cast(str, access_token)

    async def _gmail_call_with_stale_token_retry(
        self,
        implementation,
        make_call: Callable[[str], Awaitable[_T]],
    ) -> _T:
        """Run a Gmail client call; on a stale-token 401 force-refresh and retry ONCE.

        ``make_call`` receives the access_token to use and returns the coroutine for the
        client call (async_list_messages / async_get_message). On GmailStaleTokenError the
        token is force-refreshed (self-healing — NOT reauth) and the SAME call is re-run once
        with the new token so the poll succeeds instead of skipping.

        Bounded to a single retry: if the retry also raises GmailStaleTokenError it is
        re-raised as a plain GmailTransientError so the poll degrades to the transient path
        (skips this cycle, recovers next). Never triggers reauth, never loops.
        """
        token = self._gmail_access_token
        assert token is not None  # set by _async_update_data_inner before any client call
        try:
            return await make_call(token)
        except GmailStaleTokenError as err:
            _LOGGER.info(
                "Gmail access token was rejected mid-poll (stale-token 401); forcing a token "
                "refresh and retrying the request once (self-healing — no reauth)."
            )
            try:
                fresh_token = await self._async_force_refresh_gmail_token(implementation)
            except GmailStaleTokenError:
                # A forced refresh should not itself surface as a stale-token error, but guard
                # so we never recurse into a second retry — degrade to the transient path.
                raise GmailTransientError(str(err)) from err
            try:
                return await make_call(fresh_token)
            except GmailStaleTokenError as retry_err:
                # Retry also hit a stale token — do not retry again. Surface as a plain
                # transient failure so the poll skips and recovers on the next cycle.
                raise GmailTransientError(str(retry_err)) from retry_err

    async def _async_update_data(self) -> dict[str, ShipmentData]:
        """Run one poll cycle: list Gmail, parse new emails, forward to parcelapp."""
        if self.config_entry is None:
            raise UpdateFailed("config_entry is None — coordinator not properly initialized")

        self._reset_stage2_poll_counters()  # Phase 20 MRG-05 / D-11: reset per-poll counters

        # M6A-01 / finding 7: signal poll-in-progress so EmailProcessingActiveBinarySensor
        # reads on for the duration of the poll, then flip it off afterwards — without the
        # redundant double/triple dispatch the old try/finally produced.
        self._poll_in_progress = True
        try:
            self.async_update_listeners()  # turn the sensor ON at poll start
            result = await self._async_update_data_inner()
        except BaseException:
            # Failure path: HA's base _async_refresh may re-raise (e.g. ConfigEntryAuthFailed)
            # before dispatching its own listener round, so reset the flag and notify here so
            # the sensor still flips OFF. Guard the dispatch so a listener error never masks
            # the original poll exception.
            self._poll_in_progress = False
            try:
                self.async_update_listeners()
            except Exception:  # noqa: BLE001 — never mask the poll exception with a listener error
                _LOGGER.debug("listener dispatch raised during poll-failure unwind", exc_info=True)
            raise
        # Success path: HA's base coordinator dispatches listeners after we return, so just
        # reset the flag here — no redundant dispatch (finding 7).
        self._poll_in_progress = False
        # Quick-260703-mac (C): mark the first refresh done on the success return only.
        # A poll that raises stays "first" until one clean pass completes, so the bootstrap
        # window guard remains active across any transient first-poll failures.
        self._first_refresh_done = True
        # D-01: persist the shared hub's dedup + quota state at the end of every
        # successful poll — unconditional (no dirty flag) so a crash/restart never
        # loses a mark made this poll. This is the FINAL await of a successful poll
        # (after all coordinator.py dedup writes for this poll have already happened
        # via check_and_mark). D-07: gated on debug mode — a dry-run poll must write
        # zero times to the shared store (P1).
        assert self._hub is not None  # attach() runs before any poll (__init__.py:181)
        if not self._debug_mode_active():
            await self._hub.async_save()
        return result

    async def _async_update_data_inner(self) -> dict[str, ShipmentData]:
        """Inner implementation of the poll cycle (called from _async_update_data)."""
        assert self.config_entry is not None  # guaranteed by _async_update_data None check
        assert self._hub is not None  # attach() runs before any poll (__init__.py:181)

        # WR-04: drain quota-deferred POSTs on EVERY poll, honouring the documented
        # _pending_posts contract ("drained on next quota-free poll"). Previously the
        # drain only ran when a NEW Stage-2 job arrived, so deferred forwards stalled
        # indefinitely with no new shipment emails. The drain self-guards (empty /
        # debug mode / quota still exhausted → no-op) and runs BEFORE the poll's
        # current_data snapshot so its publishes are included in it.
        await self._async_drain_pending_posts()

        # 1. Refresh OAuth2 token (HA framework owns the lifecycle).
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            self.hass, self.config_entry
        )
        oauth_session = config_entry_oauth2_flow.OAuth2Session(
            self.hass, self.config_entry, implementation
        )
        token_data = self.config_entry.data.get("token")
        if not isinstance(token_data, dict):
            raise ConfigEntryAuthFailed(
                "Gmail OAuth token is missing or corrupt in config entry — "
                "please re-authorize to restore a valid token."
            )
        if not token_data.get("refresh_token"):
            raise ConfigEntryAuthFailed(
                "Gmail OAuth refresh_token is missing — please re-authorize. "
                "This typically means the original sign-in did not grant offline access."
            )
        try:
            await oauth_session.async_ensure_token_valid()
        except aiohttp.ClientResponseError as err:
            # 4xx from Google's token endpoint → raise ConfigEntryAuthFailed to trigger reauth.
            # 400 invalid_grant: token expired or revoked (common in Google Testing mode where
            # refresh tokens expire after 7 days). 401: credentials rejected/revoked.
            if err.status == 400:
                raise ConfigEntryAuthFailed(
                    "Gmail OAuth token expired or revoked (HTTP 400). "
                    "If your Google Cloud project is in Testing mode, refresh tokens expire "
                    "after 7 days — please re-authorize."
                ) from err
            if err.status == 401:
                raise ConfigEntryAuthFailed(
                    "Gmail OAuth credentials rejected by Google (HTTP 401) — "
                    "token may have been revoked. Please re-authorize."
                ) from err
            if err.status is not None and err.status < 500:
                raise ConfigEntryAuthFailed(
                    f"Gmail OAuth token rejected by Google (HTTP {err.status})"
                ) from err
            raise UpdateFailed(f"Google token endpoint server error: {err}") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Network error during Gmail token refresh: {err}") from err
        except Exception as err:  # noqa: BLE001 — translate unexpected auth errors to HA exception
            _LOGGER.error("Gmail token refresh failed unexpectedly: %s", err, exc_info=True)
            raise ConfigEntryAuthFailed(
                f"Gmail token refresh failed unexpectedly ({type(err).__name__})"
            ) from err
        # Read access_token from the session's token property. oauth_session.token is
        # self.config_entry.data["token"] — after async_ensure_token_valid() updates
        # the config entry, both references reflect the refreshed token.
        access_token = oauth_session.token.get("access_token")
        if not access_token:
            raise ConfigEntryAuthFailed("OAuth2 token missing access_token field") from None
        # Seed the per-poll token used by _gmail_call_with_stale_token_retry. A stale-token
        # force-refresh during this poll updates this attribute so later async_get_message
        # calls automatically use the refreshed token.
        self._gmail_access_token = access_token

        # 2. List Gmail messages matching the configured query.
        gmail = cast(GmailClient, self._email_client)
        query = self.config_entry.options.get(CONF_GMAIL_QUERY, DEFAULT_GMAIL_QUERY)
        rescan_window_days = self.config_entry.options.get(
            CONF_RESCAN_WINDOW_DAYS, DEFAULT_RESCAN_WINDOW_DAYS
        )
        # DBG-02: override scan window to maximum in debug mode.
        debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)
        if debug_mode:
            rescan_window_days = MAX_RESCAN_WINDOW_DAYS

        # Phase 7 (D-06): reset last_poll_* fields at the top of every poll cycle.
        poll_start = time.time()
        d = self._diagnostics
        d.last_poll_emails_returned = 0
        d.last_poll_emails_skipped_dedup = 0
        d.last_poll_effective_query = None
        d.last_poll_emails_scanned = 0
        d.last_poll_emails_matched = 0
        d.last_poll_skip_reasons = []
        d.last_poll_found = []
        d.last_poll_keyword_hits = 0
        d.last_poll_time = poll_start  # record attempt time even if poll fails mid-cycle
        d.last_poll_duration_ms = None
        # Diagnostics only — see gmail-query-drops-emails: the configured keyword
        # OR-chain is recorded for UX/diagnostics visibility but is NOT sent to the
        # Gmail List API (see below).
        d.last_poll_query = query
        _LOGGER.debug(
            "Gmail poll start — query: %s rescan_window_days: %s", query, rescan_window_days
        )

        try:
            # gmail-query-drops-emails: Gmail's server-side search engine does not
            # reliably return the full union of results for a long chain of bare
            # `OR`-joined keyword terms — real shipment emails have been silently
            # dropped by CONF_GMAIL_QUERY's default 8-term OR-chain (confirmed via
            # direct Gmail UI reproduction). Pass an empty base query here so
            # build_incremental_query() yields a date-only `after:` filter; content
            # filtering happens locally in EmailParser's tiered pipeline (HTML
            # template -> Tier 1 regex -> optional Tier 2 broad scan), which already
            # runs on every fetched message regardless of how it was found.
            messages, effective_query = await self._gmail_call_with_stale_token_retry(
                implementation,
                lambda tok: gmail.async_list_messages(
                    tok,
                    "",
                    rescan_window_days=rescan_window_days,
                ),
            )
        except GmailAuthError as err:
            raise ConfigEntryAuthFailed(f"Gmail auth error: {err}") from err
        except GmailTransientError as err:
            raise UpdateFailed(f"Gmail transient error: {err}") from err

        d.emails_returned_total += len(messages)
        d.last_poll_emails_returned = len(messages)
        d.last_poll_effective_query = effective_query
        _LOGGER.debug("Gmail fetch returned %d messages", len(messages))

        # 3. Set up parser + parcelapp client (session injection per HA quality rule).
        # PR4-C2: Tier 2 broad scan is opt-in (default OFF) to prevent
        # forwarding false positives that consume ParcelApp's 20/day quota.
        parser = EmailParser(
            enable_broad_scan=self.config_entry.options.get(
                CONF_ENABLE_BROAD_SCAN, DEFAULT_ENABLE_BROAD_SCAN
            )
        )
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )
        # self.data is None only until the first _async_update_data() completes successfully
        # (DataUpdateCoordinator initialises data=None and only writes it on success).
        # On repeated failures self.data stays None, so we continue seeding from the
        # persisted store — correct, because the live set has not changed yet.
        current_data: dict[str, ShipmentData] = (
            dict(self.data) if self.data is not None else dict(self._restored_shipments)
        )
        # Phase 31 (D-08): read the shared hub's daily-budget gate directly — no
        # per-account quota timestamp remains on this subclass.
        quota_blocked = self._hub.quota_is_exhausted

        # Phase 27 Plan 02: Seen-ID gate — filter already-processed message IDs before
        # the per-message loop so async_get_message is never called for them. Filters BOTH the
        # persisted _seen_message_ids (terminal decisions) AND the in-memory
        # _inflight_message_ids (Stage-2 work in flight this session, or a transient inline
        # failure), so an enqueued message is not re-fetched / re-judged by Ollama while its
        # job drains (round-4 fix for the convergence re-fetch + re-extract cost).
        # DBG-02 mirror: skip the filter entirely in debug_mode so debug re-scans every
        # message regardless of prior processing.  Anti-pattern: filtering INSIDE the loop
        # (after async_get_message) wastes a Gmail API call per skip — filter BEFORE.
        if not debug_mode:
            pre_filter = len(messages)
            messages = [
                m
                for m in messages
                if m["id"] not in self._seen_message_ids
                and m["id"] not in self._inflight_message_ids
            ]
            skipped_seen = pre_filter - len(messages)
            if skipped_seen:
                # Finding #240: do NOT fold this into last_poll_emails_skipped_dedup — that
                # counter is for tracking-number dedup skips. Conflating the two makes the
                # 'skipped (dedup)' diagnostic uninterpretable. Log only.
                _LOGGER.debug(
                    "Gmail poll: skipped %d already-seen/in-flight message IDs (pre-loop gate)",
                    skipped_seen,
                )

        # 4. Iterate messages — fetch body, parse, then dedup on tracking number.
        for msg_meta in messages:
            msg_id = msg_meta["id"]

            try:
                # Explicitly-typed closure captures the current loop msg_id (no lambda
                # late-binding pitfall) and calls async_get_message with the message_id
                # POSITIONALLY — matching the client's (access_token, message_id) signature.
                def _get_message(tok: str, _mid: str = msg_id) -> Awaitable[dict[str, Any]]:
                    return gmail.async_get_message(tok, _mid)

                msg = await self._gmail_call_with_stale_token_retry(
                    implementation,
                    _get_message,
                )
            except GmailAuthError as err:
                raise ConfigEntryAuthFailed(f"Gmail auth error: {err}") from err
            except GmailTransientError as err:
                raise UpdateFailed(f"Gmail transient error: {err}") from err

            email_meta = _extract_email_meta(msg)

            try:
                email_date = int(msg.get("internalDate", "0")) // 1000
            except (ValueError, TypeError):  # fmt: skip
                # PR4-C3: both ValueError and TypeError share one handler.
                _LOGGER.warning("Unexpected internalDate value for message %s; skipping", msg_id)
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": msg_id, "reason": "invalid_internal_date", **email_meta}
                )
                # C2/P11-CR-01: emit scan event so activity log captures this exit.
                self._emit_scan_event(
                    message_id=f"gmail:{msg_id}",
                    meta=email_meta,
                    outcome="invalid_internal_date",
                )
                # Phase 27 (round-4 fix #353/#314): these are transient-ish inline failures
                # (bad internalDate, momentarily-missing body, parser crash). Use the in-memory
                # in-flight gate, NOT the persisted seen cache — skip the message this session
                # but re-evaluate after a restart / FIFO eviction so a transient failure or a
                # later parser fix can still recover the shipment instead of losing it forever.
                self._mark_inflight(msg_id)
                continue

            payload = msg.get("payload", {})
            # WR-06: MIME walk + base64 decode run in the executor (one job per
            # message), not on the event loop.
            html = await self.hass.async_add_executor_job(_extract_gmail_html, payload)
            if not html:
                # Phase 7 (D-02): no_html_body is set by the COORDINATOR — the parser
                # never sees this case because we don't call parser.parse on empty HTML.
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": msg_id, "reason": "no_html_body", **email_meta}
                )
                self._emit_scan_event(
                    message_id=f"gmail:{msg_id}",
                    meta=email_meta,
                    outcome="no_html_body",
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        email_meta.get("subject", ""),
                        email_meta.get("from", ""),
                        None,
                        "no_html_body",
                    )
                else:
                    _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "no_html_body")
                # Phase 27 (round-4 fix #353/#314): these are transient-ish inline failures
                # (bad internalDate, momentarily-missing body, parser crash). Use the in-memory
                # in-flight gate, NOT the persisted seen cache — skip the message this session
                # but re-evaluate after a restart / FIFO eviction so a transient failure or a
                # later parser fix can still recover the shipment instead of losing it forever.
                self._mark_inflight(msg_id)
                continue
            # Phase 7 (D-03): parse returns ParseResult; accumulate stats then continue
            # the existing forwarding flow with the unwrapped ShipmentData.
            # WR-06: parser.parse performs up to six BeautifulSoup/lxml passes per
            # email — CPU-bound work that must not block the HA event loop (HA's
            # asyncio guidelines require offloading; a blocked loop stalls every
            # other integration and the UI). One executor job per message.
            try:
                result: ParseResult = await self.hass.async_add_executor_job(
                    parser.parse, html, msg_id, email_date
                )
            except Exception as parse_err:  # noqa: BLE001
                _LOGGER.error(
                    "Email parser raised an unexpected error for message %s: %s",
                    msg_id,
                    parse_err,
                    exc_info=True,
                )
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": msg_id, "reason": "parse_exception", **email_meta}
                )
                self._emit_scan_event(
                    message_id=f"gmail:{msg_id}",
                    meta=email_meta,
                    outcome="error",
                    strategy="no_match",
                    extra={
                        "error_type": type(parse_err).__name__,
                        "error_msg": _sanitise_parser_error(parse_err),
                    },
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        email_meta.get("subject", ""),
                        email_meta.get("from", ""),
                        None,
                        "error",
                    )
                else:
                    _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "error")
                # Phase 27 (round-4 fix #353/#314): these are transient-ish inline failures
                # (bad internalDate, momentarily-missing body, parser crash). Use the in-memory
                # in-flight gate, NOT the persisted seen cache — skip the message this session
                # but re-evaluate after a restart / FIFO eviction so a transient failure or a
                # later parser fix can still recover the shipment instead of losing it forever.
                self._mark_inflight(msg_id)
                continue
            d.emails_scanned_total += 1
            d.last_poll_emails_scanned += 1
            if result.shipment is None:
                d.last_poll_skip_reasons.append(
                    {
                        "message_id": msg_id,
                        "reason": result.skip_reason,
                        "candidates": result.candidate_tokens,
                        **email_meta,
                    }
                )
            # Keyword hit accumulation (D-08): always — HTML strategy gives all-False.
            for key, hit in result.keyword_hits.items():
                if hit and key in d.keyword_hits_per_key:
                    d.keyword_hits_per_key[key] += 1
                    d.keyword_hits_total += 1
                    d.last_poll_keyword_hits += 1
            if result.shipment is None:
                self._emit_scan_event(
                    message_id=f"gmail:{msg_id}",
                    meta=email_meta,
                    outcome="no_match",
                    strategy=result.strategy_used or "no_match",
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        email_meta.get("subject", ""),
                        email_meta.get("from", ""),
                        result.candidate_tokens,
                        "no_match",
                    )
                else:
                    _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "no_match")

                await self._run_inline_fallback(
                    msg_key=msg_id,
                    prefix="gmail:",
                    html=html,
                    meta=email_meta,
                    email_date=email_date,
                    candidate_tokens=result.candidate_tokens,
                    debug_mode=debug_mode,
                )
                continue
            # Phase 27 fix (findings #1/#6/#594): per-message seen-marking bookkeeping for the
            # convergence model. The message is marked seen at end-of-loop ONLY when it is
            # fully terminal *inline* this poll — i.e. every shipment was dedup-skipped or
            # inline-forwarded, with nothing enqueued and nothing pending retry.
            # msg_pending_retry: a shipment is awaiting retry (quota-blocked or transient POST
            #   error) → leave the message re-fetchable.
            # msg_enqueued: at least one Stage-2 job was queued (or QueueFull-dropped) for this
            #   message → do NOT mark seen; the message is re-fetched next poll and converges to
            #   seen via the tracking-number dedup-skip branch once the worker has POSTed.
            msg_pending_retry = False
            msg_enqueued = False
            # Iterate all shipments from this email. Single-shipment emails have
            # extra_shipments=[] so this loop runs once. Multi-package digests
            # (e.g. USPS Informed Delivery) may have 2+ shipments; each gets its
            # own storage key so coordinator.data creates a sensor per package.
            for _si, shipment in enumerate([result.shipment, *result.extra_shipments]):
                # First shipment uses msg_id for backward compat; extras get a
                # composite key so they create distinct entities.
                storage_key = msg_id if _si == 0 else f"{msg_id}::{shipment.tracking_number}"

                # D-10: tracking-number dedup check (replaces message-ID skip gate).
                # DBG-03: skip dedup entirely in debug mode.
                normalized = normalize_tracking_number(shipment.tracking_number)
                if not debug_mode:
                    if self._hub.is_submitted(normalized):
                        d.last_poll_emails_skipped_dedup += 1
                        self._emit_scan_event(
                            message_id=f"gmail:{msg_id}",
                            meta=email_meta,
                            outcome="skipped_dedup",
                            strategy=result.strategy_used or "unknown",
                            tracking_number=shipment.tracking_number,
                        )
                        _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "skipped_dedup")
                        continue

                # Only increment match/found counters after dedup confirms this is a new tracking number.
                d.emails_matched_total += 1
                d.last_poll_emails_matched += 1
                d.tracking_numbers_found_total += 1
                d.last_poll_found.append(
                    {
                        "tracking_number": shipment.tracking_number,
                        "carrier": shipment.carrier_name,
                        "order_name": shipment.order_name,
                        "message_id": msg_id,
                        "candidates": result.candidate_tokens,
                        **email_meta,
                    }
                )

                # Phase 18 D-03: route Stage-2-enabled entries to the queue; the entire inline
                # POST section (debug_mode, quota_blocked, parcel POST) is bypassed.
                if self._diagnostics.stage2_enabled:
                    # Stage-1 uses PURE CONVERGENCE — NOT the in-flight gate (finding #1516).
                    # A Stage-1 email can yield multiple shipments (extra_shipments), so pinning
                    # the whole message in-flight when one sibling enqueues would permanently
                    # filter a QueueFull-dropped sibling once the first sibling's job succeeds
                    # (the worker can't release the message until ALL siblings are done). Stage-1
                    # re-fetch is cheap (Gmail fetch + regex parse, no Ollama), so we simply do
                    # not mark the message seen here: it is re-fetched next poll, already-POSTed
                    # siblings hit the dedup-skip branch, not-yet-done siblings re-enqueue, and
                    # the message converges to a persisted seen ID once all dedup-skip. No
                    # raw_msg_id (Stage-1 jobs do not participate in the in-flight gate).
                    self._enqueue_stage2(
                        normalized,
                        storage_key,
                        shipment,
                        html,
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                    )
                    msg_enqueued = True
                    continue

                # DBG-04: in debug mode, suppress POST and record dry_run_suppressed event.
                if debug_mode:
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="dry_run_suppressed",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        email_meta.get("subject", ""),
                        email_meta.get("from", ""),
                        result.candidate_tokens,
                        "dry_run_suppressed",
                    )
                    continue

                # 5. Quota guard (D-05): when quota is exhausted, skip the POST.
                if quota_blocked:
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="skipped_quota",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    if debug_mode:
                        _LOGGER.debug(
                            "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                            email_meta.get("subject", ""),
                            email_meta.get("from", ""),
                            result.candidate_tokens,
                            "skipped_quota",
                        )
                    else:
                        _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "skipped_quota")
                    # Finding #1/#6: quota-blocked POST is deferred — keep the message
                    # re-fetchable so it forwards once the quota window resets.
                    msg_pending_retry = True
                    continue

                # WR-03: Defensive carrier-format gate before the Gmail Stage-1 inline POST.
                # Mirrors the drain re-gate (coordinator.py ~L1216) and the worker re-gate (WR-02).
                # A carrier-invalid value is terminal (can never pass — a future merge/parser change
                # cannot slip a bad value through). On reject: record the counter, log at DEBUG only
                # (D-07/T-28-09 — no TN values at INFO/WARNING), emit a terminal scan event, and
                # continue to the next shipment WITHOUT setting msg_pending_retry (do NOT force a
                # retry of a value that can never pass). The message converges to seen via the
                # post-loop _mark_message_seen guard since msg_enqueued/msg_pending_retry stay False.
                # On pass: rebind shipment to the gate-clean canonical form (D-03) so the POST body
                # and the subsequent success-path dedup write both use the separator-free string.
                #
                # Residual FedEx risk (T-N3K-02): the FedEx pattern matches ANY bare 12/15/20-digit
                # number, so a body order/invoice/phone number of those lengths that Stage-1 matches
                # as FedEx WILL pass this gate and burn a quota slot. Tightening the FedEx pattern
                # is deferred to a future phase.
                gm_clean, gm_ok, gm_reason = validate_carrier_format(shipment.tracking_number)
                if not gm_ok:
                    self._diagnostics.record_carrier_format_rejection(
                        gm_clean, gm_reason or "no_carrier_match"
                    )
                    _LOGGER.debug(
                        "Gmail message %s: carrier-format gate rejected tn='%s' (reason=%s)"
                        " — skipping inline POST (terminal)",
                        msg_id,
                        gm_clean,
                        gm_reason,
                    )
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="stage2_no_data",
                        strategy=result.strategy_used or "unknown",
                    )
                    continue  # terminal — do not set msg_pending_retry; converges to seen

                # Rebind shipment to the gate-clean canonical form (D-03) so the POST body and
                # the success-path dedup write both use the identical separator-free string.
                shipment = dc_replace(shipment, tracking_number=gm_clean)  # type: ignore[arg-type]

                # Phase 31 (D-01/D-03): reserve a slot from the shared daily budget
                # immediately before the POST — this inline path has no per-poll cap,
                # only the daily budget. On exhaustion, treat exactly like the
                # quota-blocked skip above (re-fetchable, not terminal).
                if not self._hub.try_consume():
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="skipped_quota",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    msg_pending_retry = True
                    continue

                carrier_code = normalize_carrier(shipment.carrier_name)
                try:
                    await parcel_client.async_add_delivery(
                        tracking_number=shipment.tracking_number,
                        carrier_code=carrier_code,
                        description=shipment.order_summary
                        or shipment.order_name
                        or shipment.tracking_number,
                    )
                except ParcelAppAuthError as err:
                    raise ConfigEntryAuthFailed("parcelapp.net auth error") from err
                except ParcelAppQuotaError as err:
                    # D-06: prefer reset_at, else next midnight UTC (hub max-precedence merge).
                    # No refund (D-01) — the reserved slot stays consumed.
                    self._hub.record_quota_exhausted(err.reset_at)
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    _LOGGER.warning(
                        "parcelapp.net daily quota exhausted; forwarding paused until %s",
                        self._hub.quota_exhausted_until,
                    )
                    # C2/P11-CR-01: emit event for the email that triggered quota exhaustion.
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="quota_exhausted_now",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    quota_blocked = True
                    # Finding #1/#6: this shipment was not POSTed — keep the message
                    # re-fetchable so it forwards once the quota window resets.
                    msg_pending_retry = True
                    continue
                except ParcelAppAlreadyAddedError:
                    self._hub.check_and_mark(normalized)  # DEDUP-01: shared dedup-write
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="already_added",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "already_added")
                    continue
                except ParcelAppInvalidTrackingError as err:
                    _LOGGER.error(
                        "Invalid tracking for message %s (permanent 400 — suppressing retries): %s",
                        msg_id,
                        err,
                    )
                    # Record normalized tracking number to suppress infinite retries
                    # (DEDUP-01: shared dedup-write).
                    self._hub.check_and_mark(normalized)
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    # C2/P11-CR-01: emit event for invalid tracking (permanent 400).
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="invalid_tracking",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                        extra={
                            "error_type": type(err).__name__,
                            "error_msg": str(err)[:100],
                        },
                    )
                    continue
                except ParcelAppTransientError as err:
                    _LOGGER.warning("parcelapp.net transient error for %s: %s", msg_id, err)
                    # C2/P11-CR-01: emit event for transient errors.
                    self._emit_scan_event(
                        message_id=f"gmail:{msg_id}",
                        meta=email_meta,
                        outcome="transient_error",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                        extra={
                            "error_type": type(err).__name__,
                            "error_msg": str(err)[:100],
                        },
                    )
                    # D-01: return the reserved-but-unspent daily-budget slot.
                    self._hub.refund_consume()
                    # Finding #1/#6: transient POST failure — keep the message re-fetchable
                    # so it is retried next poll instead of being filtered out permanently.
                    msg_pending_retry = True
                    continue

                # 6. Success — record tracking number dedup, save immediately (D-10/D-03).
                self._hub.check_and_mark(normalized)  # DEDUP-01: shared dedup-write
                self._record_forward()  # Phase 26: forward counter (genuine 2xx POST only)
                current_data[storage_key] = shipment
                self._pending_shipments = current_data
                await self._async_save_store(immediate=True)  # finding 12: durable forward
                self._emit_scan_event(
                    message_id=f"gmail:{msg_id}",
                    meta=email_meta,
                    outcome="posted",
                    strategy=result.strategy_used or "unknown",
                    tracking_number=shipment.tracking_number,
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        email_meta.get("subject", ""),
                        email_meta.get("from", ""),
                        result.candidate_tokens,
                        "posted",
                    )
                else:
                    _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "posted")

            # Phase 27 fix (findings #1/#6/#594): all shipments of this message have now been
            # processed. Mark the message seen so future polls skip the Gmail fetch — but ONLY
            # for fully-terminal-inline messages (all dedup-skipped or inline-forwarded) with
            # nothing enqueued and nothing pending retry. Enqueued messages are deliberately
            # left unmarked here and converge to seen on a later poll via the dedup-skip branch
            # once the worker has POSTed their tracking numbers. Skipped in debug mode, which
            # re-scans every message each poll.
            if not debug_mode and not msg_enqueued and not msg_pending_retry:
                self._mark_message_seen(msg_id)

        # Phase 7: capture per-poll timing (D-04, Specifics).
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000
        d.submitted_tracking_count = self._hub.submitted_count

        # DBG-06: show persistent notification in HA UI after each debug poll.
        if debug_mode:
            message = (
                "⚠️ Shop2Parcel is in dry-run mode. No parcels will be sent to parcelapp.net.\n"
                f"Emails scanned this cycle: {d.last_poll_emails_scanned}.\n"
                "Disable in Settings → Integrations → Shop2Parcel → Configure."
            )
            persistent_notification.async_create(
                self.hass,
                message=message,
                title="Shop2Parcel Debug Mode",
                notification_id=debug_mode_notification_id(self.config_entry.entry_id),
            )

        # Phase 31 (D-08): the per-account stale-quota-clear block is removed — the
        # hub's own always-armed quota-expiry timer (31-03) owns clearing the shared
        # block, and hub.quota_is_exhausted already reads False once the window passes.

        # CR-01: re-merge onto the LIVE self.data before trim/save/return. The Stage-2
        # worker (and the pending-posts drain) publish merged shipments via
        # async_set_updated_data while this poll is awaiting; returning the stale
        # start-of-poll snapshot would overwrite self.data without those entries and
        # the poll-end save would drop them from the store — permanently, because the
        # tracking-number dedup + seen-ID gates prevent them from ever being re-added.
        # Merge direction: keys this poll wrote win on collision (the poll's writes
        # are newest for its own keys); worker-published keys survive via self.data.
        current_data = {**(self.data or {}), **current_data}

        if not debug_mode:
            # FIFO trim: current_data is a plain dict (not OrderedDict), so
            # popitem(last=False) is not available. next(iter(...)) yields the
            # insertion-order oldest key on CPython 3.7+ (guaranteed by PEP 468).
            pre_trim_count = len(current_data)
            while len(current_data) > MAX_SUBMITTED_TRACKING_NUMBERS:
                del current_data[next(iter(current_data))]
            trimmed = pre_trim_count - len(current_data)
            if trimmed:
                _LOGGER.warning(
                    "FIFO trim removed %d oldest shipment(s) — cap is %d. "
                    "Oldest tracked parcels are no longer visible in HA.",
                    trimmed,
                    MAX_SUBMITTED_TRACKING_NUMBERS,
                )
            self._pending_shipments = current_data
            await self._async_save_store()

        return current_data
