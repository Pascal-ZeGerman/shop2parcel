"""ImapCoordinator — IMAP poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the IMAP SINCE-date fetch + tracking-number dedup cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from datetime import UTC, datetime
from typing import cast

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData
from .api.exceptions import (
    ImapAuthError,
    ImapTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.imap_client import ImapClient, extract_html_body_imap, extract_text_body_imap
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_ENABLE_BROAD_SCAN,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SEARCH,
    CONF_IMAP_TLS,
    CONF_IMAP_USERNAME,
    CONF_RESCAN_WINDOW_DAYS,
    DEFAULT_ENABLE_BROAD_SCAN,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_RESCAN_WINDOW_DAYS,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    normalize_tracking_number,
)
from .coordinator import (
    Shop2ParcelCoordinator,
    _extract_imap_email_meta,
    _next_midnight_utc,
)

_LOGGER = logging.getLogger(__name__)

# RFC 3501 requires English month abbreviations in IMAP SEARCH date strings.
# strftime('%b') is locale-dependent and must NOT be used here.
_IMAP_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class ImapCoordinator(Shop2ParcelCoordinator):
    """Coordinator for IMAP-connected Shop2Parcel entries."""

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._email_client = ImapClient(hass.async_add_executor_job)

    async def _async_update_data(self) -> dict[str, ShipmentData]:
        """IMAP poll path — uses SINCE-date fetch + tracking-number dedup.

        Phase 10 (D-11/D-12): fetch all emails in the rescan window on every poll.
        Dedup is now tracking-number-based (not UID-based). No _last_imap_uid field.
        Does NOT perform OAuth2 token refresh (IMAP uses entry.data credentials directly).
        """
        entry = self.config_entry
        if entry is None:
            raise UpdateFailed("config_entry is None — coordinator not properly initialized")
        imap_client = cast(ImapClient, self._email_client)

        # Phase 7 (D-06): reset last_poll_* fields at the top of every poll cycle.
        poll_start = time.time()
        d = self._diagnostics
        query = entry.options.get(CONF_IMAP_SEARCH, DEFAULT_IMAP_SEARCH)
        d.last_poll_emails_returned = 0
        d.last_poll_emails_skipped_dedup = 0
        d.last_poll_emails_scanned = 0
        d.last_poll_emails_matched = 0
        d.last_poll_skip_reasons = []
        d.last_poll_found = []
        d.last_poll_keyword_hits = 0
        d.last_poll_time = poll_start  # record attempt time even if poll fails mid-cycle
        d.last_poll_duration_ms = None
        d.last_poll_query = query
        # last_poll_effective_query not set for IMAP (no Gmail after: filter)

        # D-11: compute since_date from rescan_window_days (IMAP SEARCH date format).
        rescan_window_days = entry.options.get(CONF_RESCAN_WINDOW_DAYS, DEFAULT_RESCAN_WINDOW_DAYS)
        since_ts = int(time.time()) - rescan_window_days * 86400
        _since_dt = datetime.fromtimestamp(since_ts, tz=UTC)
        since_date = f"{_since_dt.day:02d}-{_IMAP_MONTH_ABBR[_since_dt.month - 1]}-{_since_dt.year}"
        _LOGGER.debug(
            "IMAP poll start — host: %s query: %s since: %s",
            entry.data[CONF_IMAP_HOST],
            query,
            since_date,
        )

        # Fetch messages from IMAP (whole session in one executor call per D-05/Pitfall 6).
        try:
            raw_messages = await imap_client.fetch_shipping_emails(
                host=entry.data[CONF_IMAP_HOST],
                port=entry.data[CONF_IMAP_PORT],
                username=entry.data[CONF_IMAP_USERNAME],
                password=entry.data[CONF_IMAP_PASSWORD],
                tls_mode=entry.data[CONF_IMAP_TLS],
                search_criteria=query,
                since_date=since_date,
            )
        except ImapAuthError as err:
            raise ConfigEntryAuthFailed("IMAP auth error") from err
        except ImapTransientError as err:
            raise UpdateFailed(f"IMAP transient error: {err}") from err

        d.emails_returned_total += len(raw_messages)
        d.last_poll_emails_returned = len(raw_messages)
        _LOGGER.debug("IMAP fetch returned %d messages", len(raw_messages))

        # Set up parser + parcelapp client (same as Gmail path).
        # PR4-C2: Tier 2 broad scan is opt-in (default OFF).
        parser = EmailParser(
            enable_broad_scan=entry.options.get(CONF_ENABLE_BROAD_SCAN, DEFAULT_ENABLE_BROAD_SCAN)
        )
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=entry.data[CONF_API_KEY],
        )
        current_data: dict[str, ShipmentData] = dict(self.data or {})
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None and now < self._quota_exhausted_until
        )

        for msg_info in raw_messages:
            uid_str = str(msg_info["uid"])

            raw_bytes: bytes = msg_info["raw"]
            imap_meta = _extract_imap_email_meta(raw_bytes)
            html = extract_html_body_imap(raw_bytes)
            if not html:
                text_body = extract_text_body_imap(raw_bytes)
                if text_body:
                    # PR4-I1: same escape+<pre> wrap as Gmail path.
                    html = f"<html><body><pre>{_html_stdlib.escape(text_body)}</pre></body></html>"
            if not html:
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": uid_str, "reason": "no_html_body", **imap_meta}
                )
                d.scan_events.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "message_id": f"imap:{uid_str}",
                        "subject": imap_meta.get("subject", ""),
                        "sender": imap_meta.get("from", ""),
                        "strategy": None,
                        "tracking_number": None,
                        "outcome": "no_html_body",
                    }
                )
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "no_html_body")
                continue

            # Assign a synthetic email_date (0 = unknown — IMAP does not guarantee internalDate).
            try:
                result: ParseResult = parser.parse(html, uid_str, 0)
            except Exception as parse_err:  # noqa: BLE001
                _LOGGER.error(
                    "Email parser raised an unexpected error for IMAP UID %s: %s",
                    uid_str,
                    parse_err,
                )
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": uid_str, "reason": "parse_exception", **imap_meta}
                )
                d.scan_events.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "message_id": f"imap:{uid_str}",
                        "subject": imap_meta.get("subject", ""),
                        "sender": imap_meta.get("from", ""),
                        "strategy": "no_match",
                        "tracking_number": None,
                        "outcome": "error",
                        "error_type": type(parse_err).__name__,
                        "error_msg": str(parse_err)[:100],
                    }
                )
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "error")
                continue
            d.emails_scanned_total += 1
            d.last_poll_emails_scanned += 1
            if result.shipment is None:
                d.last_poll_skip_reasons.append(
                    {
                        "message_id": uid_str,
                        "reason": result.skip_reason,
                        "candidates": result.candidate_tokens,
                        **imap_meta,
                    }
                )
            for key, hit in result.keyword_hits.items():
                if hit and key in d.keyword_hits_per_key:
                    d.keyword_hits_per_key[key] += 1
                    d.keyword_hits_total += 1
                    d.last_poll_keyword_hits += 1
            if result.shipment is None:
                d.scan_events.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "message_id": f"imap:{uid_str}",
                        "subject": imap_meta.get("subject", ""),
                        "sender": imap_meta.get("from", ""),
                        "strategy": result.strategy_used or "no_match",
                        "tracking_number": None,
                        "outcome": "no_match",
                    }
                )
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "no_match")
                continue
            shipment = result.shipment

            # D-10: tracking-number dedup check (replaces UID skip gate).
            normalized = normalize_tracking_number(shipment.tracking_number)
            if normalized in self._submitted_tracking_numbers:
                d.last_poll_emails_skipped_dedup += 1
                d.scan_events.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "message_id": f"imap:{uid_str}",
                        "subject": imap_meta.get("subject", ""),
                        "sender": imap_meta.get("from", ""),
                        "strategy": result.strategy_used,
                        "tracking_number": shipment.tracking_number,
                        "outcome": "skipped_dedup",
                    }
                )
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "skipped_dedup")
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
                    "message_id": uid_str,
                    "candidates": result.candidate_tokens,
                    **imap_meta,
                }
            )

            if quota_blocked:
                d.scan_events.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "message_id": f"imap:{uid_str}",
                        "subject": imap_meta.get("subject", ""),
                        "sender": imap_meta.get("from", ""),
                        "strategy": result.strategy_used,
                        "tracking_number": shipment.tracking_number,
                        "outcome": "skipped_quota",
                    }
                )
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "skipped_quota")
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
                    "Invalid tracking for IMAP UID %s (permanent 400 — suppressing retries): %s",
                    uid_str,
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
                _LOGGER.warning("parcelapp.net transient error for UID %s: %s", uid_str, err)
                continue

            # Success — record tracking number dedup, save immediately (D-10/D-03).
            self._submitted_tracking_numbers[normalized] = None
            if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                self._submitted_tracking_numbers.popitem(last=False)
            await self._async_save_store()
            current_data[uid_str] = shipment
            d.scan_events.append(
                {
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": result.strategy_used,
                    "tracking_number": shipment.tracking_number,
                    "outcome": "posted",
                }
            )
            d.scan_events_total += 1
            _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "posted")

        # Phase 7: capture per-poll timing.
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000
        d.submitted_tracking_count = len(self._submitted_tracking_numbers)

        # Clear stale quota block.
        if (
            not quota_blocked
            and self._quota_exhausted_until is not None
            and int(time.time()) >= self._quota_exhausted_until
        ):
            self._quota_exhausted_until = None
            await self._async_save_store()

        return current_data
