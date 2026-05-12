"""Shop2Parcel DataUpdateCoordinator.

Phase 4: orchestrates Gmail polling, email parsing, parcelapp.net forwarding,
persistent deduplication, and quota backoff. All atomic behaviors live in
api/*.py — this module only sequences them.

Locked decisions (CONTEXT.md):
- D-01: data is dict[str, ShipmentData] keyed by Gmail message_id.
- D-02: data accumulates all ever-seen shipments — no status filtering here.
- D-04: Store schema {"forwarded_ids": [...], "quota_exhausted_until": int | None, "last_email_timestamp": int | None, "last_imap_uid": int | None}.
- D-05: Quota exhausted -> Gmail polls continue, POST step skipped.
- D-06: quota_exhausted_until = err.reset_at OR next_midnight_utc().
"""

from __future__ import annotations

import email as _email_stdlib
import html as _html_stdlib
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ParseResult, ShipmentData
from .api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ImapAuthError,
    ImapTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.gmail_client import GmailClient, extract_html_body, extract_text_body
from .api.imap_client import ImapClient, extract_html_body_imap, extract_text_body_imap
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_CONNECTION_TYPE,
    CONF_ENABLE_BROAD_SCAN,
    CONF_GMAIL_QUERY,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SEARCH,
    CONF_IMAP_TLS,
    CONF_IMAP_USERNAME,
    CONF_POLL_INTERVAL,
    CONF_RESCAN_WINDOW_DAYS,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_ENABLE_BROAD_SCAN,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RESCAN_WINDOW_DAYS,
    DOMAIN,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    normalize_tracking_number,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2


def _extract_email_meta(msg: dict) -> dict:
    """Extract subject, from, date, and snippet from a Gmail message dict."""
    headers = {
        h["name"]: h["value"]
        for h in msg.get("payload", {}).get("headers", [])
        if "name" in h and "value" in h
    }
    return {
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
    }


def _extract_imap_email_meta(raw_bytes: bytes) -> dict:
    """Extract subject, from, and date from raw IMAP message bytes."""
    msg = _email_stdlib.message_from_bytes(raw_bytes)
    return {
        "subject": msg.get("Subject", "") or "",
        "from": msg.get("From", "") or "",
        "date": msg.get("Date", "") or "",
        "snippet": "",
    }


def _next_midnight_utc() -> int:
    """Compute epoch seconds for the next 00:00 UTC.

    Per CONTEXT.md D-06: when ParcelAppQuotaError.reset_at is None, fall back
    to the next UTC midnight so the backoff aligns with parcelapp's daily reset.
    """
    today_utc = datetime.now(UTC).date()
    return int(
        datetime.combine(
            today_utc + timedelta(days=1),
            dt_time.min,
            tzinfo=UTC,
        ).timestamp()
    )


@dataclass(slots=True)
class PollStats:
    """Phase 7 (DIAG-05..DIAG-12): in-memory diagnostic accumulator.

    Mutated in place by `_async_update_data`. NOT frozen (Pitfall 3) and NOT
    persisted (D-04) — counters reset to 0 on each HA restart, matching the
    ROADMAP spec ("cumulative since last HA restart").

    Field semantics:
      *_total fields: cumulative since coordinator construction (HA session lifetime).
      last_poll_* fields: reset at the top of every _async_update_data call (D-06).
    """

    emails_returned_total: int = 0
    emails_scanned_total: int = 0
    emails_matched_total: int = 0
    tracking_numbers_found_total: int = 0
    keyword_hits_total: int = 0
    last_poll_emails_returned: int = 0
    last_poll_emails_skipped_dedup: int = 0
    submitted_tracking_count: int = 0
    last_poll_effective_query: str | None = None
    last_poll_emails_scanned: int = 0
    last_poll_emails_matched: int = 0
    last_poll_time: float | None = None
    last_poll_duration_ms: float | None = None
    last_poll_query: str | None = None
    last_poll_skip_reasons: list[dict] = field(default_factory=list)
    last_poll_found: list[dict] = field(default_factory=list)
    last_poll_keyword_hits: int = 0
    keyword_hits_per_key: dict[str, int] = field(
        default_factory=lambda: {
            "tracking_regex": 0,
            "order_regex": 0,
            "carrier_regex": 0,
        }
    )
    scan_events: deque = field(default_factory=lambda: deque(maxlen=50))
    scan_events_total: int = 0


class Shop2ParcelStore(Store):
    """HA Store subclass for Shop2Parcel with v1→v2 migration support.

    Overrides _async_migrate_func to drop the v1 forwarded_ids/last_imap_uid/
    last_email_timestamp schema and seed the v2 submitted_tracking_numbers schema.
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict,
    ) -> dict:
        """Migrate stored data when STORAGE_VERSION bumps.

        v1 → v2: drop forwarded_ids, last_imap_uid, last_email_timestamp;
        seed submitted_tracking_numbers as empty list; preserve quota_exhausted_until.
        """
        if old_major_version == 1:
            entry_id = self.key.removeprefix("shop2parcel.")
            _LOGGER.warning(
                "Migrated Shop2Parcel Store to v2 for entry %s — "
                "submitted_tracking_numbers starts empty; first poll may re-submit "
                "tracking numbers already in parcelapp.net.",
                entry_id,
            )
            return {
                "submitted_tracking_numbers": [],
                "quota_exhausted_until": old_data.get("quota_exhausted_until"),
            }
        # Future major versions: return data unchanged (caller will handle).
        return old_data


class Shop2ParcelCoordinator(DataUpdateCoordinator[dict[str, ShipmentData]]):
    """Polls Gmail, parses shipping emails, forwards to parcelapp.net."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        poll_minutes = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=poll_minutes),
        )
        self._store: Shop2ParcelStore = Shop2ParcelStore(
            hass, version=STORAGE_VERSION, key=f"shop2parcel.{entry.entry_id}"
        )
        self._submitted_tracking_numbers: OrderedDict[str, None] = OrderedDict()
        self._quota_exhausted_until: int | None = None
        # Phase 7 (D-04): in-memory diagnostic accumulator. Resets on HA restart.
        self._diagnostics: PollStats = PollStats()
        # Phase 9 (D-05/D-10): dispatch to ImapClient or GmailClient based on connection type.
        conn_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)
        if conn_type == CONNECTION_TYPE_IMAP:
            self._email_client: ImapClient | GmailClient = ImapClient(hass.async_add_executor_job)
        else:
            self._email_client = GmailClient(hass.async_add_executor_job)

    @property
    def diagnostics(self) -> PollStats:
        """Public read-only view of in-memory poll diagnostics."""
        return self._diagnostics

    async def _async_load_store(self) -> None:
        """Hydrate dedup + quota state from Store.

        MUST be called before async_config_entry_first_refresh().  Failing to do so
        leaves _submitted_tracking_numbers empty, causing every previously submitted
        tracking number to be re-POSTed on startup.

        async_setup_entry in __init__.py is the canonical caller; do not call this
        method from any other site without careful thought about sequencing.
        """
        stored = await self._store.async_load() or {}
        stored_list = stored.get("submitted_tracking_numbers", [])
        self._submitted_tracking_numbers = OrderedDict((tn, None) for tn in stored_list)
        self._quota_exhausted_until = stored.get("quota_exhausted_until")

    async def _async_save_store(self) -> None:
        """Persist current dedup + quota state to Store."""
        await self._store.async_save(
            {
                "submitted_tracking_numbers": list(self._submitted_tracking_numbers.keys()),
                "quota_exhausted_until": self._quota_exhausted_until,
            }
        )

    async def _async_update_data(self) -> dict[str, ShipmentData]:
        """Run one poll cycle: list Gmail, parse new emails, forward to parcelapp."""
        assert self.config_entry is not None
        # Phase 9 (D-05): dispatch to IMAP path if connection_type == "imap".
        if self.config_entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_IMAP:
            return await self._async_update_data_imap()

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
                _LOGGER.debug("Skipping forward of %s — quota exhausted", msg_id)
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

    async def _async_update_data_imap(self) -> dict[str, ShipmentData]:
        """IMAP poll path — uses SINCE-date fetch + tracking-number dedup.

        Phase 10 (D-11/D-12): fetch all emails in the rescan window on every poll.
        Dedup is now tracking-number-based (not UID-based). No _last_imap_uid field.
        Does NOT perform OAuth2 token refresh (IMAP uses entry.data credentials directly).
        """
        entry = self.config_entry
        assert entry is not None
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
        since_date = (
            datetime.fromtimestamp(since_ts, tz=timezone.utc)
            .strftime("%d-%b-%Y")
            .lstrip("0")
        )
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": None,
                    "tracking_number": None,
                    "outcome": "no_html_body",
                })
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": "no_match",
                    "tracking_number": None,
                    "outcome": "error",
                    "error_type": type(parse_err).__name__,
                    "error_msg": str(parse_err)[:100],
                })
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
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": result.strategy_used or "no_match",
                    "tracking_number": None,
                    "outcome": "no_match",
                })
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "no_match")
                continue
            shipment = result.shipment

            # D-10: tracking-number dedup check (replaces UID skip gate).
            normalized = normalize_tracking_number(shipment.tracking_number)
            if normalized in self._submitted_tracking_numbers:
                d.last_poll_emails_skipped_dedup += 1
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": result.strategy_used,
                    "tracking_number": shipment.tracking_number,
                    "outcome": "skipped_dedup",
                })
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
                _LOGGER.debug("Skipping forward of IMAP UID %s — quota exhausted", uid_str)
                d.scan_events.append({
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "message_id": f"imap:{uid_str}",
                    "subject": imap_meta.get("subject", ""),
                    "sender": imap_meta.get("from", ""),
                    "strategy": result.strategy_used,
                    "tracking_number": shipment.tracking_number,
                    "outcome": "skipped_quota",
                })
                d.scan_events_total += 1
                _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "skipped_quota")
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
            d.scan_events.append({
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "message_id": f"imap:{uid_str}",
                "subject": imap_meta.get("subject", ""),
                "sender": imap_meta.get("from", ""),
                "strategy": result.strategy_used,
                "tracking_number": shipment.tracking_number,
                "outcome": "posted",
            })
            _LOGGER.debug("IMAP UID %s outcome: %s", uid_str, "posted")
            d.scan_events_total += 1

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

    async def async_cleanup_delivered(self, now: datetime) -> None:
        """Remove delivered shipments from coordinator.data and the entity registry.

        Phase 5 D-08/D-09/D-11: scheduled once daily via async_track_time_interval
        (24h period set in __init__.py). Match parcelapp deliveries to
        coordinator entries by tracking_number; status_code == 0 means
        Completed (parcelapp-api.md). Removal is immediate.

        The 'now' parameter is required by async_track_time_interval's callback
        signature even though we ignore it (required by async_track_time_interval contract).

        Exceptions are caught + logged + return early — DO NOT raise
        ConfigEntryAuthFailed or UpdateFailed from here:
        those are only meaningful inside _async_update_data.
        """
        if not self.data:
            return  # Nothing to clean up — skip the API call entirely

        assert self.config_entry is not None
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )
        try:
            deliveries = await parcel_client.async_get_deliveries(filter_mode="recent")
        except ParcelAppAuthError as err:
            _LOGGER.error("parcelapp auth error during cleanup: %s", err)
            return
        except ParcelAppTransientError as err:
            _LOGGER.warning("parcelapp transient error during cleanup: %s", err)
            return
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Unexpected error during cleanup: %s", err)
            return

        # D-11: O(1) reverse lookup {tracking_number: message_id}
        tracking_to_msg_id = {
            shipment.tracking_number: msg_id for msg_id, shipment in self.data.items()
        }
        # Use .get() — guard against missing 'status_code' in delivery objects
        delivered_tracking = {
            d["tracking_number"]
            for d in deliveries
            if d.get("status_code") == 0 and "tracking_number" in d
        }
        removed_ids = {
            tracking_to_msg_id[tn] for tn in delivered_tracking if tn in tracking_to_msg_id
        }
        if not removed_ids:
            return

        new_data = {k: v for k, v in self.data.items() if k not in removed_ids}
        # async_set_updated_data (NOT async_request_refresh) — externally-triggered
        # data change that bypasses the normal poll cycle (Claude's Discretion).
        self.async_set_updated_data(new_data)

        # Explicit entity registry removal — HA does NOT auto-remove entities
        # when their key disappears from coordinator.data.
        entity_registry = er.async_get(self.hass)
        entry_entities = entity_registry.entities.get_entries_for_config_entry_id(
            self.config_entry.entry_id
        )
        unique_id_to_entity_id = {e.unique_id: e.entity_id for e in entry_entities}
        for removed_id in removed_ids:
            target_uid = f"{DOMAIN}_{self.config_entry.entry_id}_{removed_id}"
            entity_id = unique_id_to_entity_id.get(target_uid)
            if entity_id is not None:
                entity_registry.async_remove(entity_id)
                _LOGGER.info("Removed delivered shipment entity: %s", entity_id)
