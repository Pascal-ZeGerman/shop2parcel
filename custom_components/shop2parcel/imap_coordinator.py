"""ImapCoordinator — IMAP poll path for Shop2Parcel.

Subclass of Shop2ParcelCoordinator. Overrides _async_update_data with
the IMAP SINCE-date fetch + tracking-number dedup cycle.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import time
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from typing import cast

from homeassistant.components import persistent_notification
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData, validate_carrier_format
from .api.exceptions import (
    ImapAuthError,
    ImapTransientError,
    ParcelAppAlreadyAddedError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.imap_client import ImapClient, extract_html_body_imap, extract_text_body_imap
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_DEBUG_MODE,
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
    MAX_RESCAN_WINDOW_DAYS,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    debug_mode_notification_id,
    normalize_tracking_number,
)
from .coordinator import (
    Shop2ParcelCoordinator,
    Stage2Job,  # noqa: F401 — type import; subclass calls _enqueue_stage2 which constructs Stage2Job
    _extract_imap_email_meta,
    _next_midnight_utc,
    _sanitise_parser_error,
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
        if not entry.options.get(CONF_DEBUG_MODE, False):
            persistent_notification.async_dismiss(
                hass, notification_id=debug_mode_notification_id(entry.entry_id)
            )

    async def _async_update_data(self) -> dict[str, ShipmentData]:
        """IMAP poll path — uses SINCE-date fetch + tracking-number dedup.

        Phase 10 (D-11/D-12): fetch all emails in the rescan window on every poll.
        Dedup is now tracking-number-based (not UID-based). No _last_imap_uid field.
        Does NOT perform OAuth2 token refresh (IMAP uses entry.data credentials directly).
        """
        entry = self.config_entry
        if entry is None:
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
        """Inner implementation of the IMAP poll cycle (called from _async_update_data)."""
        entry = self.config_entry
        assert entry is not None  # guaranteed by _async_update_data
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
        debug_mode = entry.options.get(CONF_DEBUG_MODE, False)
        if debug_mode:
            rescan_window_days = MAX_RESCAN_WINDOW_DAYS
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
            raise ConfigEntryAuthFailed(f"IMAP auth error: {err}") from err
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
                self._emit_scan_event(
                    message_id=f"imap:{uid_str}",
                    meta=imap_meta,
                    outcome="no_html_body",
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        imap_meta.get("subject", ""),
                        imap_meta.get("from", ""),
                        None,
                        "no_html_body",
                    )
                else:
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
                    exc_info=True,
                )
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": uid_str, "reason": "parse_exception", **imap_meta}
                )
                self._emit_scan_event(
                    message_id=f"imap:{uid_str}",
                    meta=imap_meta,
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
                        imap_meta.get("subject", ""),
                        imap_meta.get("from", ""),
                        None,
                        "error",
                    )
                else:
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
                self._emit_scan_event(
                    message_id=f"imap:{uid_str}",
                    meta=imap_meta,
                    outcome="no_match",
                    strategy=result.strategy_used or "no_match",
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        imap_meta.get("subject", ""),
                        imap_meta.get("from", ""),
                        result.candidate_tokens,
                        "no_match",
                    )
                else:
                    _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "no_match")
                continue
            # Iterate all shipments from this email. Single-shipment emails have
            # extra_shipments=[] so this loop runs once. Multi-package digests
            # (e.g. USPS Informed Delivery) may have 2+ shipments; each gets its
            # own storage key so coordinator.data creates a sensor per package.
            for _si, shipment in enumerate([result.shipment, *result.extra_shipments]):
                # First shipment uses uid_str for backward compat; extras get a
                # composite key so they create distinct entities.
                storage_key = uid_str if _si == 0 else f"{uid_str}::{shipment.tracking_number}"

                # D-10: tracking-number dedup check (replaces UID skip gate).
                normalized = normalize_tracking_number(shipment.tracking_number)
                if not debug_mode:
                    if normalized in self._submitted_tracking_numbers:
                        d.last_poll_emails_skipped_dedup += 1
                        self._emit_scan_event(
                            message_id=f"imap:{uid_str}",
                            meta=imap_meta,
                            outcome="skipped_dedup",
                            strategy=result.strategy_used or "unknown",
                            tracking_number=shipment.tracking_number,
                        )
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

                # Phase 18 D-03: route Stage-2-enabled entries to the queue; the entire inline
                # POST section (debug_mode, quota_blocked, parcel POST) is bypassed.
                if self._diagnostics.stage2_enabled:
                    self._enqueue_stage2(
                        normalized,
                        storage_key,
                        shipment,
                        html,
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
                    )
                    continue

                # DBG-04: suppress POST in debug mode; append dry_run_suppressed event and continue.
                if debug_mode:
                    self._emit_scan_event(
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
                        outcome="dry_run_suppressed",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        imap_meta.get("subject", ""),
                        imap_meta.get("from", ""),
                        result.candidate_tokens,
                        "dry_run_suppressed",
                    )
                    continue

                if quota_blocked:
                    self._emit_scan_event(
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
                        outcome="skipped_quota",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    if debug_mode:
                        _LOGGER.debug(
                            "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                            imap_meta.get("subject", ""),
                            imap_meta.get("from", ""),
                            result.candidate_tokens,
                            "skipped_quota",
                        )
                    else:
                        _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "skipped_quota")
                    continue

                # WR-02/WR-03 symmetry: Defensive carrier-format gate before the IMAP
                # Stage-1 inline POST. Mirrors the drain re-gate (coordinator.py ~L1216),
                # the worker re-gate (WR-02), and the Gmail inline gate (WR-03). A
                # carrier-invalid value is terminal — it can never pass. On reject: record
                # the counter, log at DEBUG only (D-07/T-28-09), and continue to the next
                # shipment. IMAP has no msg_pending_retry/msg_enqueued flags and no
                # post-loop _mark_message_seen call, so the handling is simply record + log
                # + continue (mirrors the existing IMAP quota-skip / dedup-skip branches).
                # On pass: rebind shipment to the gate-clean canonical form (D-03) so the
                # POST body and the success-path dedup write both use the separator-free string.
                im_clean, im_ok, im_reason = validate_carrier_format(shipment.tracking_number)
                if not im_ok:
                    self._diagnostics.record_carrier_format_rejection(
                        im_clean, im_reason or "no_carrier_match"
                    )
                    _LOGGER.debug(
                        "IMAP UID %s: carrier-format gate rejected tn='%s' (reason=%s)"
                        " — skipping inline POST (terminal)",
                        uid_str,
                        im_clean,
                        im_reason,
                    )
                    continue  # terminal — record + log + continue, no dedup write

                # Rebind shipment to the gate-clean canonical form (D-03).
                shipment = dc_replace(shipment, tracking_number=im_clean)  # type: ignore[arg-type]

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
                    self._arm_quota_expiry_timer()  # finding 3: refresh entities at expiry
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    _LOGGER.warning(
                        "parcelapp.net daily quota exhausted; forwarding paused until %s",
                        self._quota_exhausted_until,
                    )
                    # C2/P11-CR-01: emit event for the email that triggered quota exhaustion.
                    self._emit_scan_event(
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
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
                    self._pending_shipments = current_data
                    await self._async_save_store()
                    self._emit_scan_event(
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
                        outcome="already_added",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                    )
                    _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "already_added")
                    continue
                except ParcelAppInvalidTrackingError as err:
                    _LOGGER.error(
                        "Invalid tracking for IMAP UID %s (permanent 400 — suppressing retries): %s",
                        uid_str,
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
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
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
                    _LOGGER.warning("parcelapp.net transient error for UID %s: %s", uid_str, err)
                    # C2/P11-CR-01: emit event for transient errors.
                    self._emit_scan_event(
                        message_id=f"imap:{uid_str}",
                        meta=imap_meta,
                        outcome="transient_error",
                        strategy=result.strategy_used or "unknown",
                        tracking_number=shipment.tracking_number,
                        extra={
                            "error_type": type(err).__name__,
                            "error_msg": str(err)[:100],
                        },
                    )
                    continue

                # Success — record tracking number dedup, save immediately (D-10/D-03).
                self._submitted_tracking_numbers[normalized] = None
                if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                    self._submitted_tracking_numbers.popitem(last=False)
                self._record_forward()  # Phase 26: forward counter (genuine 2xx POST only)
                current_data[storage_key] = shipment
                self._pending_shipments = current_data
                await self._async_save_store(immediate=True)  # finding 12: durable forward
                self._emit_scan_event(
                    message_id=f"imap:{uid_str}",
                    meta=imap_meta,
                    outcome="posted",
                    strategy=result.strategy_used or "unknown",
                    tracking_number=shipment.tracking_number,
                )
                if debug_mode:
                    _LOGGER.debug(
                        "[Shop2Parcel DEBUG] subject=%r from=%r candidates=%s outcome=%s",
                        imap_meta.get("subject", ""),
                        imap_meta.get("from", ""),
                        result.candidate_tokens,
                        "posted",
                    )
                else:
                    _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "posted")

        # Phase 7: capture per-poll timing.
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000
        d.submitted_tracking_count = len(self._submitted_tracking_numbers)

        # DBG-06: persistent notification while debug mode is active.
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
                notification_id=debug_mode_notification_id(entry.entry_id),
            )

        # Clear stale quota block from Store once the window has expired.
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
