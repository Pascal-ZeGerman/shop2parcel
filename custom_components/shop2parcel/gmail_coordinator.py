"""GmailCoordinator — Gmail poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the Gmail OAuth2 + message-fetch + parse + forward cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from dataclasses import replace as dc_replace
from typing import cast

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
    GmailTransientError,
    OllamaSchemaError,
    OllamaTransientError,
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
    MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    debug_mode_notification_id,
    normalize_tracking_number,
)
from .coordinator import (
    Shop2ParcelCoordinator,
    Stage2Job,  # noqa: F401 — type import; subclass calls _enqueue_stage2 which constructs Stage2Job
    _extract_email_meta,
    _next_midnight_utc,
    _sanitise_parser_error,
)

_LOGGER = logging.getLogger(__name__)


class GmailCoordinator(Shop2ParcelCoordinator):
    """Coordinator for Gmail-connected Shop2Parcel entries."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._email_client = GmailClient(hass.async_add_executor_job)
        if not entry.options.get(CONF_DEBUG_MODE, False):
            persistent_notification.async_dismiss(
                hass, notification_id=debug_mode_notification_id(entry.entry_id)
            )

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
        return result

    async def _async_update_data_inner(self) -> dict[str, ShipmentData]:
        """Inner implementation of the poll cycle (called from _async_update_data)."""
        assert self.config_entry is not None  # guaranteed by _async_update_data None check

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
        d.last_poll_query = query
        _LOGGER.debug(
            "Gmail poll start — query: %s rescan_window_days: %s", query, rescan_window_days
        )

        try:
            messages, effective_query = await gmail.async_list_messages(
                access_token,
                query,
                rescan_window_days=rescan_window_days,
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
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None and now < self._quota_exhausted_until
        )

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
                msg = await gmail.async_get_message(access_token, msg_id)
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
            html = extract_html_body(payload)
            if not html:
                text_body = extract_text_body(payload)
                if text_body:
                    # PR4-I1: escape angle brackets/ampersands so plain-text
                    # bodies with raw '<', '>', '&' don't produce malformed
                    # HTML for BeautifulSoup. Use <pre> to preserve newlines
                    # for downstream regex/text scanning.
                    html = f"<html><body><pre>{_html_stdlib.escape(text_body)}</pre></body></html>"
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
            try:
                result: ParseResult = parser.parse(html, msg_id, email_date)
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

                # Phase 27 Plan 03: Ollama fallback gatekeeper on Stage-1 miss.
                # Runs only when stage2 is enabled, NOT in debug_mode, and a live extractor
                # exists. _extractor and stage2_enabled can diverge (async_stop_stage2 nulls
                # _extractor without clearing stage2_enabled), so the None guard both prevents
                # an AttributeError and routes the stop/reload-race case to the re-fetch
                # branch below (finding #523) rather than caching the message as a reject.
                #
                # Seen-ID model = convergence: a fallback that ENQUEUES does NOT mark the
                # message seen here. The message is re-fetched next poll; once the worker has
                # POSTed the tracking number, the re-fetch hits the _submitted_tracking_numbers
                # dedup guard and marks it seen then. Marking per-message at enqueue while work
                # is per-shipment is what lost deferred/QueueFull jobs (findings #1/#594), so
                # the tracking-number dedup converges the seen-ID instead of optimistic marking.
                if (
                    self._diagnostics.stage2_enabled
                    and not debug_mode
                    and self._extractor is not None
                ):
                    # Per-poll cap check (Design §4 / T-27-03-03 DoS guard): if we have
                    # already run MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL extractions this
                    # poll, skip WITHOUT caching the ID so it is retried next poll (Pitfall 6).
                    if (
                        self._stage2_fallback_extractions_this_poll
                        >= MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL
                    ):
                        _LOGGER.debug(
                            "Gmail message %s: fallback cap reached (%d/%d) — skipping this poll",
                            msg_id,
                            self._stage2_fallback_extractions_this_poll,
                            MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL,
                        )
                        continue  # do NOT cache (retry next poll)

                    # Run Ollama fallback extraction (Design §3 / Pattern 3 / T-27-03-01).
                    # Count the attempt regardless of outcome (increment before try so cap
                    # applies even if the extractor raises).
                    self._stage2_fallback_extractions_this_poll += 1
                    # Finding #4: count the LLM attempt before the call (parity with the
                    # worker) so the emails_sent_to_llm diagnostic includes fallback runs.
                    self._diagnostics.stage2_llm_attempts_total += 1
                    try:
                        result_fb = await self._extractor.async_extract(html, None)
                    except (OllamaTransientError, OllamaSchemaError) as fb_err:
                        # Finding #2: route fallback failures through the shared failure
                        # surface (the worker path's _record_stage2_failure equivalent) so a
                        # sustained Ollama outage increments the consecutive-failure streak
                        # and raises the persistent notification — instead of being swallowed
                        # at DEBUG. Finding #450: only the FIRST fallback failure of a poll
                        # escalates (bumps the streak / may notify); the rest are recorded
                        # without inflating the shared streak ~10x per poll on a no-match
                        # backlog. Still do NOT cache the ID: it is retried next poll.
                        escalate = not self._stage2_fallback_failed_this_poll
                        self._stage2_fallback_failed_this_poll = True
                        self._surface_stage2_failure(
                            meta=email_meta,
                            message_id=f"gmail:{msg_id}",
                            normalized_tn="",
                            err=fb_err,
                            escalate=escalate,
                        )
                        continue  # do NOT cache (retry next poll)
                    except Exception as fb_err:  # noqa: BLE001
                        # Finding #505: an unexpected (non-Ollama) exception must NOT abort the
                        # whole poll mid-iteration (which would skip every later message and
                        # drop the per-poll save). Surface it like a Stage-2 failure and move
                        # on; the message stays un-cached, so it is retried next poll. Finding
                        # #450: escalate at most once per poll (shared latch).
                        _LOGGER.error(
                            "Gmail message %s: unexpected error during Ollama fallback "
                            "extraction — skipping this message: %s",
                            msg_id,
                            fb_err,
                            exc_info=True,
                        )
                        escalate = not self._stage2_fallback_failed_this_poll
                        self._stage2_fallback_failed_this_poll = True
                        self._surface_stage2_failure(
                            meta=email_meta,
                            message_id=f"gmail:{msg_id}",
                            normalized_tn="",
                            err=fb_err,
                            escalate=escalate,
                        )
                        continue  # do NOT cache (retry next poll)

                    # Finding #4: record the successful extraction's latency (parity with the
                    # worker) so the LLM-call / parse-success-rate diagnostics stay accurate.
                    self._diagnostics.record_llm_call(
                        result_fb.latency_ms,
                        fence_retry=result_fb.passes_used == 2,
                    )

                    # Extract + validate the tracking number from the Ollama result.
                    # Phase 28 Plan 04 (R1/R2/R3): strict carrier-format gate via
                    # validate_carrier_format strips internal separators ([ -]) and
                    # uppercases before pattern-matching, then returns the canonical
                    # clean form used for dedup + enqueue + ShipmentData (D-03).
                    tn = (result_fb.locked.get("tracking_number") or "").strip()
                    fb_clean, fb_ok, fb_reason = validate_carrier_format(tn)
                    if not fb_ok:
                        # Hard reject: the fallback model returned a hallucinated or
                        # non-carrier string. Record the rejection (R3/D-06) and log at
                        # DEBUG only (D-07/T-28-09 — no INFO/WARNING leakage of TN values).
                        # This is a terminal decision (mirror the no-match branch below):
                        # mark seen so the gatekeeper does not re-run Ollama on this
                        # non-shipment email every poll.
                        self._diagnostics.record_carrier_format_rejection(
                            fb_clean, fb_reason or "no_carrier_match"
                        )
                        _LOGGER.debug(
                            "Gmail message %s: fallback carrier-format gate rejected '%s' "
                            "(reason=%s) — cached as rejected",
                            msg_id,
                            fb_clean,
                            fb_reason,
                        )
                        self._mark_message_seen(msg_id)
                        self._emit_scan_event(
                            message_id=f"gmail:{msg_id}",
                            meta=email_meta,
                            outcome="stage2_no_data",
                        )
                    elif fb_clean in self._submitted_tracking_numbers:
                        # Finding #5: mirror the main shipment loop's dedup guard. An
                        # already-forwarded tracking number is terminal — mark seen and do
                        # not re-enqueue (the worker only checks the in-flight set, not
                        # _submitted_tracking_numbers, so a re-POST would burn a quota slot).
                        self._mark_message_seen(msg_id)
                        _LOGGER.debug(
                            "Gmail message %s: fallback tracking %s already submitted — skipping",
                            msg_id,
                            fb_clean,
                        )
                    else:
                        # Gate pass, not-yet-forwarded: build ShipmentData using the gate
                        # clean canonical form (D-03 — separator-free, uppercased). Do NOT
                        # route through merge_llm_authoritative (Stage-1 is None here; Pattern 3).
                        # Do NOT mark seen — convergence re-fetch + dedup marks it once POSTed.
                        fb_shipment = ShipmentData(
                            tracking_number=fb_clean,
                            carrier_name=result_fb.locked.get("carrier_name") or "",
                            order_name=result_fb.locked.get("order_name") or "",
                            message_id=msg_id,
                            email_date=email_date,
                            custom_attributes=result_fb.custom,
                            order_summary=result_fb.locked.get("order_summary"),
                        )
                        enqueued = self._enqueue_stage2(
                            fb_clean,
                            storage_key=msg_id,
                            shipment=fb_shipment,
                            html_body=html,
                            message_id=f"gmail:{msg_id}",
                            meta=email_meta,
                            # Finding #3: hand the already-extracted result to the worker so
                            # it does NOT call Ollama a second time on the same body.
                            prefetched_result=result_fb,
                            raw_msg_id=msg_id,
                        )
                        if enqueued:
                            # Round-4 fix #512: add to the in-memory in-flight gate so the
                            # gatekeeper does NOT re-run Ollama on this message every poll
                            # until the worker POSTs. The worker releases it on a defer; it
                            # converges to a persisted seen ID via dedup once POSTed.
                            self._mark_inflight(msg_id)
                            _LOGGER.debug(
                                "Gmail message %s: Ollama fallback found tracking %s — "
                                "enqueued (re-fetch converges the seen-ID)",
                                msg_id,
                                fb_clean,
                            )
                        else:
                            _LOGGER.warning(
                                "Gmail message %s: Stage-2 queue full — fallback tracking %s "
                                "not enqueued; will retry next poll",
                                msg_id,
                                fb_clean,
                            )
                    # Note: empty or non-carrier strings are handled by the `if not fb_ok:`
                    # branch above (reason="empty" or "no_carrier_match") — no separate
                    # soft-reject else-branch is needed.
                    continue

                # stage2 enabled but the extractor is transiently unavailable (stop/reload
                # race): the fallback could not judge this message. Do NOT mark it seen —
                # leave it re-fetchable so the gatekeeper runs once the extractor is restored
                # (finding #523).
                if self._diagnostics.stage2_enabled and not debug_mode:
                    _LOGGER.debug(
                        "Gmail message %s: stage2 enabled but extractor unavailable — "
                        "leaving un-cached for retry",
                        msg_id,
                    )
                    continue

                # stage2 disabled: no fallback will ever run for this message, so a Stage-1
                # miss is a terminal no-match — mark seen. Finding #749: do NOT mark in
                # debug_mode — that writes the PERSISTED seen cache during a dry-run, so an
                # email scanned in debug (possibly a real shipment the regex missed) would be
                # filtered out once debug is disabled, before the fallback could judge it.
                if not debug_mode:
                    self._mark_message_seen(msg_id)
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
                    if normalized in self._submitted_tracking_numbers:
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
                    # D-06: prefer reset_at, else next midnight UTC.
                    self._quota_exhausted_until = (
                        err.reset_at if err.reset_at is not None else _next_midnight_utc()
                    )
                    self._arm_quota_expiry_timer()  # finding 3: refresh entities at expiry
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    _LOGGER.warning(
                        "parcelapp.net daily quota exhausted; forwarding paused until %s",
                        self._quota_exhausted_until,
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
                    self._submitted_tracking_numbers[normalized] = None
                    if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                        self._submitted_tracking_numbers.popitem(last=False)
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
                    # Record normalized tracking number to suppress infinite retries.
                    self._submitted_tracking_numbers[normalized] = None
                    if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                        self._submitted_tracking_numbers.popitem(last=False)
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
                    # Finding #1/#6: transient POST failure — keep the message re-fetchable
                    # so it is retried next poll instead of being filtered out permanently.
                    msg_pending_retry = True
                    continue

                # 6. Success — record tracking number dedup, save immediately (D-10/D-03).
                self._submitted_tracking_numbers[normalized] = None
                if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                    self._submitted_tracking_numbers.popitem(last=False)
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
        d.submitted_tracking_count = len(self._submitted_tracking_numbers)

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

        # Clear stale quota block from Store once the window has expired.  Without
        # this, a past-epoch timestamp would accumulate across restarts indefinitely.
        # Skip when quota_blocked=True: the timestamp was just set this cycle and must
        # not be cleared in the same pass (even if reset_at is already in the past).
        # W17/P14-WR-02: skip in debug mode — zero store writes is the DBG-03 contract.
        if (
            not debug_mode
            and not quota_blocked
            and self._quota_exhausted_until is not None
            and int(time.time()) >= self._quota_exhausted_until
        ):
            self._quota_exhausted_until = None
            self._arm_quota_expiry_timer()  # finding 3: cancel the now-obsolete expiry timer
            await self._async_save_store()

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
