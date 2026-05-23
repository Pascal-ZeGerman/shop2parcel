"""GmailCoordinator — Gmail poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the Gmail OAuth2 + message-fetch + parse + forward cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from typing import cast

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData
from .api.exceptions import (
    GmailAuthError,
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

        # 1. Refresh OAuth2 token (HA framework owns the lifecycle).
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            self.hass, self.config_entry
        )
        oauth_session = config_entry_oauth2_flow.OAuth2Session(
            self.hass, self.config_entry, implementation
        )
        if not oauth_session.token.get("refresh_token"):
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
            raise ConfigEntryAuthFailed("Gmail auth error") from err
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
        current_data: dict[str, ShipmentData] = (
            dict(self.data) if self.data is not None else dict(self._restored_shipments)
        )
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None and now < self._quota_exhausted_until
        )

        # 4. Iterate messages — fetch body, parse, then dedup on tracking number.
        for msg_meta in messages:
            msg_id = msg_meta["id"]

            try:
                msg = await gmail.async_get_message(access_token, msg_id)
            except GmailAuthError as err:
                raise ConfigEntryAuthFailed("Gmail auth error") from err
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
                continue
            shipment = result.shipment

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
                continue

            carrier_code = normalize_carrier(shipment.carrier_name)
            try:
                await parcel_client.async_add_delivery(
                    tracking_number=shipment.tracking_number,
                    carrier_code=carrier_code,
                    description=shipment.order_name or shipment.tracking_number,
                )
            except ParcelAppAuthError as err:
                raise ConfigEntryAuthFailed("parcelapp.net auth error") from err
            except ParcelAppQuotaError as err:
                # D-06: prefer reset_at, else next midnight UTC.
                self._quota_exhausted_until = (
                    err.reset_at if err.reset_at is not None else _next_midnight_utc()
                )
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
                continue
            except ParcelAppAlreadyAddedError:
                self._submitted_tracking_numbers[normalized] = None
                if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                    self._submitted_tracking_numbers.popitem(last=False)
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
                continue

            # 6. Success — record tracking number dedup, save immediately (D-10/D-03).
            self._submitted_tracking_numbers[normalized] = None
            if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                self._submitted_tracking_numbers.popitem(last=False)
            await self._async_save_store()
            current_data[msg_id] = shipment
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
            await self._async_save_store()

        if not debug_mode:
            while len(current_data) > MAX_SUBMITTED_TRACKING_NUMBERS:
                del current_data[next(iter(current_data))]
            self._pending_shipments = current_data
            await self._async_save_store()

        return current_data
