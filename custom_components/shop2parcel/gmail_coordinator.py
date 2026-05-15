"""GmailCoordinator — Gmail poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the Gmail OAuth2 + message-fetch + parse + forward cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from datetime import UTC, datetime
from typing import cast

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData
from .api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.gmail_client import GmailClient, extract_html_body, extract_text_body
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_ENABLE_BROAD_SCAN,
    CONF_GMAIL_QUERY,
    CONF_RESCAN_WINDOW_DAYS,
    DEFAULT_ENABLE_BROAD_SCAN,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_RESCAN_WINDOW_DAYS,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    normalize_tracking_number,
)
from .coordinator import (
    Shop2ParcelCoordinator,
    _extract_email_meta,
    _next_midnight_utc,
)

_LOGGER = logging.getLogger(__name__)


class GmailCoordinator(Shop2ParcelCoordinator):
    """Coordinator for Gmail-connected Shop2Parcel entries."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._email_client = GmailClient(hass.async_add_executor_job)

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
        try:
            await oauth_session.async_ensure_token_valid()
        except Exception as err:  # noqa: BLE001 — translate to HA exception
            raise ConfigEntryAuthFailed("Gmail token refresh failed") from err
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
        current_data: dict[str, ShipmentData] = dict(self.data or {})
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"gmail:{msg_id}",
                    "subject": email_meta.get("subject", ""),
                    "sender": email_meta.get("from", ""),
                    "strategy": None,
                    "tracking_number": None,
                    "outcome": "no_html_body",
                })
                d.scan_events_total += 1
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"gmail:{msg_id}",
                    "subject": email_meta.get("subject", ""),
                    "sender": email_meta.get("from", ""),
                    "strategy": "no_match",
                    "tracking_number": None,
                    "outcome": "error",
                    "error_type": type(parse_err).__name__,
                    "error_msg": str(parse_err)[:100],
                })
                d.scan_events_total += 1
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"gmail:{msg_id}",
                    "subject": email_meta.get("subject", ""),
                    "sender": email_meta.get("from", ""),
                    "strategy": result.strategy_used or "no_match",
                    "tracking_number": None,
                    "outcome": "no_match",
                })
                d.scan_events_total += 1
                _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "no_match")
                continue
            shipment = result.shipment

            # D-10: tracking-number dedup check (replaces message-ID skip gate).
            normalized = normalize_tracking_number(shipment.tracking_number)
            if normalized in self._submitted_tracking_numbers:
                d.last_poll_emails_skipped_dedup += 1
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"gmail:{msg_id}",
                    "subject": email_meta.get("subject", ""),
                    "sender": email_meta.get("from", ""),
                    "strategy": result.strategy_used,
                    "tracking_number": shipment.tracking_number,
                    "outcome": "skipped_dedup",
                })
                d.scan_events_total += 1
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

            # 5. Quota guard (D-05): when quota is exhausted, skip the POST.
            if quota_blocked:
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"gmail:{msg_id}",
                    "subject": email_meta.get("subject", ""),
                    "sender": email_meta.get("from", ""),
                    "strategy": result.strategy_used,
                    "tracking_number": shipment.tracking_number,
                    "outcome": "skipped_quota",
                })
                d.scan_events_total += 1
                _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "skipped_quota")
                continue

            carrier_code = normalize_carrier(shipment.carrier_name)
            try:
                await parcel_client.async_add_delivery(
                    tracking_number=shipment.tracking_number,
                    carrier_code=carrier_code,
                    description=shipment.order_name,
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
                quota_blocked = True
                continue
            except ParcelAppInvalidTrackingError as err:
                _LOGGER.error(
                    "Invalid tracking for message %s (permanent 400 — suppressing retries): %s",
                    msg_id,
                    err,
                )
                # Record normalized tracking number to suppress infinite retries.
                normalized_for_suppress = normalize_tracking_number(shipment.tracking_number)
                self._submitted_tracking_numbers[normalized_for_suppress] = None
                if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                    self._submitted_tracking_numbers.popitem(last=False)
                await self._async_save_store()
                continue
            except ParcelAppTransientError as err:
                _LOGGER.warning("parcelapp.net transient error for %s: %s", msg_id, err)
                continue

            # 6. Success — record tracking number dedup, save immediately (D-10/D-03).
            self._submitted_tracking_numbers[normalized] = None
            if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                self._submitted_tracking_numbers.popitem(last=False)
            await self._async_save_store()
            current_data[msg_id] = shipment
            d.scan_events.append({
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "message_id": f"gmail:{msg_id}",
                "subject": email_meta.get("subject", ""),
                "sender": email_meta.get("from", ""),
                "strategy": result.strategy_used,
                "tracking_number": shipment.tracking_number,
                "outcome": "posted",
            })
            d.scan_events_total += 1
            _LOGGER.debug("Gmail message %s outcome: %s", msg_id, "posted")

        # Phase 7: capture per-poll timing (D-04, Specifics).
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000
        d.submitted_tracking_count = len(self._submitted_tracking_numbers)

        # Clear stale quota block from Store once the window has expired.  Without
        # this, a past-epoch timestamp would accumulate across restarts indefinitely.
        # Skip when quota_blocked=True: the timestamp was just set this cycle and must
        # not be cleared in the same pass (even if reset_at is already in the past).
        if (
            not quota_blocked
            and self._quota_exhausted_until is not None
            and int(time.time()) >= self._quota_exhausted_until
        ):
            self._quota_exhausted_until = None
            await self._async_save_store()

        return current_data
