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

import logging
import time
from dataclasses import dataclass, field
from typing import cast
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time

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
from .api.gmail_client import GmailClient, extract_html_body
from .api.imap_client import ImapClient, extract_html_body_imap
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_CONNECTION_TYPE,
    CONF_GMAIL_QUERY,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SEARCH,
    CONF_IMAP_TLS,
    CONF_IMAP_USERNAME,
    CONF_POLL_INTERVAL,
    CONNECTION_TYPE_GMAIL,
    CONNECTION_TYPE_IMAP,
    DEFAULT_GMAIL_QUERY,
    DEFAULT_IMAP_SEARCH,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


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

    emails_scanned_total: int = 0
    emails_matched_total: int = 0
    tracking_numbers_found_total: int = 0
    keyword_hits_total: int = 0
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
        self._store: Store = Store(
            hass, version=STORAGE_VERSION, key=f"shop2parcel.{entry.entry_id}"
        )
        self._forwarded_ids: set[str] = set()
        self._quota_exhausted_until: int | None = None
        self._last_email_timestamp: int | None = None
        # Phase 7 (D-04): in-memory diagnostic accumulator. Resets on HA restart.
        self._diagnostics: PollStats = PollStats()
        # Phase 9 (D-05/D-10): dispatch to ImapClient or GmailClient based on connection type.
        conn_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)
        if conn_type == CONNECTION_TYPE_IMAP:
            self._email_client: ImapClient | GmailClient = ImapClient(hass.async_add_executor_job)
        else:
            self._email_client = GmailClient(hass.async_add_executor_job)
        # Phase 9 (D-08): IMAP UID deduplication — persisted in Store. None means first run.
        self._last_imap_uid: int | None = None

    async def _async_load_store(self) -> None:
        """Hydrate dedup + quota state from Store.

        MUST be called before async_config_entry_first_refresh().  Failing to do so
        leaves _forwarded_ids empty, causing every previously forwarded shipment to be
        re-POSTed on startup.

        async_setup_entry in __init__.py is the canonical caller; do not call this
        method from any other site without careful thought about sequencing.
        """
        stored = await self._store.async_load() or {}
        self._forwarded_ids = set(stored.get("forwarded_ids", []))
        self._quota_exhausted_until = stored.get("quota_exhausted_until")
        self._last_email_timestamp = stored.get("last_email_timestamp")
        self._last_imap_uid = stored.get("last_imap_uid")

    async def _async_save_store(self) -> None:
        """Persist current dedup + quota + timestamp + IMAP UID state to Store."""
        await self._store.async_save(
            {
                "forwarded_ids": sorted(self._forwarded_ids),
                "quota_exhausted_until": self._quota_exhausted_until,
                "last_email_timestamp": self._last_email_timestamp,
                "last_imap_uid": self._last_imap_uid,
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

        # Phase 7 (D-06): reset last_poll_* fields at the top of every poll cycle.
        poll_start = time.time()
        d = self._diagnostics
        d.last_poll_emails_scanned = 0
        d.last_poll_emails_matched = 0
        d.last_poll_skip_reasons = []
        d.last_poll_found = []
        d.last_poll_keyword_hits = 0
        d.last_poll_time = poll_start  # record attempt time even if poll fails mid-cycle
        d.last_poll_duration_ms = None
        d.last_poll_query = query

        try:
            messages = await gmail.async_list_messages(
                access_token, query, after_timestamp=self._last_email_timestamp
            )
        except GmailAuthError as err:
            raise ConfigEntryAuthFailed("Gmail auth error") from err
        except GmailTransientError as err:
            raise UpdateFailed(f"Gmail transient error: {err}") from err

        # 3. Set up parser + parcelapp client (session injection per HA quality rule).
        parser = EmailParser()
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )
        current_data: dict[str, ShipmentData] = dict(self.data or {})
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None and now < self._quota_exhausted_until
        )
        max_email_date = self._last_email_timestamp or 0
        any_forwarded = False

        # 4. Iterate messages — skip already-forwarded BEFORE fetching body (saves quota).
        for msg_meta in messages:
            msg_id = msg_meta["id"]
            if msg_id in self._forwarded_ids:
                continue

            try:
                msg = await gmail.async_get_message(access_token, msg_id)
            except GmailAuthError as err:
                raise ConfigEntryAuthFailed("Gmail auth error") from err
            except GmailTransientError as err:
                raise UpdateFailed(f"Gmail transient error: {err}") from err

            # Extract email_date early so it can be used to advance max_email_date
            # for skipped messages (no_html_body, parse failure) as well as forwarded ones.
            # This prevents permanently-unparseable messages from blocking
            # _last_email_timestamp indefinitely (WR-02 fix).
            try:
                email_date = int(msg.get("internalDate", "0")) // 1000
            except (ValueError, TypeError):
                _LOGGER.warning("Unexpected internalDate value for message %s; skipping", msg_id)
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append(
                    {"message_id": msg_id, "reason": "invalid_internal_date"}
                )
                continue

            html = extract_html_body(msg.get("payload", {}))
            if not html:
                # Phase 7 (D-02): no_html_body is set by the COORDINATOR — the parser
                # never sees this case because we don't call parser.parse on empty HTML.
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append({"message_id": msg_id, "reason": "no_html_body"})
                # Advance max_email_date so this un-parseable message does not block
                # _last_email_timestamp and cause infinite re-fetching on future polls.
                if email_date > max_email_date:
                    max_email_date = email_date
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
                d.last_poll_skip_reasons.append({"message_id": msg_id, "reason": "parse_exception"})
                if email_date > max_email_date:
                    max_email_date = email_date
                continue
            d.emails_scanned_total += 1
            d.last_poll_emails_scanned += 1
            if result.shipment is None:
                d.last_poll_skip_reasons.append(
                    {"message_id": msg_id, "reason": result.skip_reason}
                )
            else:
                d.emails_matched_total += 1
                d.last_poll_emails_matched += 1
                d.tracking_numbers_found_total += 1
                d.last_poll_found.append(
                    {
                        "tracking_number": result.shipment.tracking_number,
                        "carrier": result.shipment.carrier_name,
                        "order_name": result.shipment.order_name,
                        "message_id": msg_id,
                    }
                )
            # Keyword hit accumulation (D-08): always — HTML strategy gives all-False.
            for key, hit in result.keyword_hits.items():
                if hit and key in d.keyword_hits_per_key:
                    d.keyword_hits_per_key[key] += 1
                    d.keyword_hits_total += 1
                    d.last_poll_keyword_hits += 1
            if result.shipment is None:
                # Advance max_email_date for parse failures too — the message cannot
                # produce a shipment and should not be re-fetched indefinitely.
                if email_date > max_email_date:
                    max_email_date = email_date
                continue
            shipment = result.shipment

            # 5. Quota guard (D-05): when quota is exhausted, do NOT add the shipment
            # to current_data and do NOT advance max_email_date.  Keeping the message
            # "unseen" ensures _last_email_timestamp is not advanced past it, so Gmail
            # will return it again on the next poll cycle once quota has reset — at which
            # point it will be properly POSTed and added to forwarded_ids.  Advancing
            # current_data or max_email_date here would cause the message to be skipped
            # by the after_timestamp filter and never forwarded (FWRD-02 violation).
            if quota_blocked:
                _LOGGER.debug("Skipping forward of %s — quota exhausted", msg_id)
                continue

            carrier_code = normalize_carrier(shipment.carrier_name)
            try:
                await parcel_client.async_add_delivery(
                    tracking_number=shipment.tracking_number,
                    carrier_code=carrier_code,
                    description=shipment.order_name,
                )
            except ParcelAppAuthError as err:
                # Raising ConfigEntryAuthFailed mid-loop is safe: messages that were
                # successfully forwarded before this point already had their msg_ids
                # added to _forwarded_ids and persisted to Store (line ~210 below).
                # _last_email_timestamp is NOT yet advanced (that happens after the
                # loop), so after the user re-authenticates the next poll re-fetches
                # all messages from the last known timestamp.  Those already in
                # forwarded_ids are skipped; unforwarded ones are retried cleanly.
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
                # Add to forwarded_ids to prevent infinite retry loop draining quota.
                # The tracking data is invalid; re-POSTing will always fail with 400.
                self._forwarded_ids.add(msg_id)
                if email_date > max_email_date:
                    max_email_date = email_date
                any_forwarded = True  # triggers store save so suppression is persisted
                continue
            except ParcelAppTransientError as err:
                _LOGGER.warning("parcelapp.net transient error for %s: %s", msg_id, err)
                continue

            # 6. Success — update in-memory data (persist deferred to after the loop
            # so the store is written once for all successfully forwarded messages in
            # this poll cycle rather than once per message).
            self._forwarded_ids.add(msg_id)
            current_data[msg_id] = shipment
            if email_date > max_email_date:
                max_email_date = email_date
            any_forwarded = True

        # Phase 7: capture per-poll timing (D-04, Specifics).
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000

        timestamp_advanced = max_email_date > (self._last_email_timestamp or 0)
        if timestamp_advanced:
            self._last_email_timestamp = max_email_date
        # Persist once after the loop if any shipments were forwarded OR if the
        # email timestamp advanced (covers skipped messages that unblock _last_email_timestamp).
        if any_forwarded or timestamp_advanced:
            await self._async_save_store()

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
        """IMAP poll path — mirrors _async_update_data but uses ImapClient + UID dedup.

        Phase 9 (D-05/D-06/D-07/D-08): same post-fetch pipeline as Gmail (parse → forward).
        Does NOT perform OAuth2 token refresh (IMAP uses entry.data credentials directly).
        """
        entry = self.config_entry
        assert entry is not None
        imap_client = cast(ImapClient, self._email_client)

        # Phase 7 (D-06): reset last_poll_* fields at the top of every poll cycle.
        poll_start = time.time()
        d = self._diagnostics
        query = entry.options.get(CONF_IMAP_SEARCH, DEFAULT_IMAP_SEARCH)
        d.last_poll_emails_scanned = 0
        d.last_poll_emails_matched = 0
        d.last_poll_skip_reasons = []
        d.last_poll_found = []
        d.last_poll_keyword_hits = 0
        d.last_poll_time = poll_start  # record attempt time even if poll fails mid-cycle
        d.last_poll_duration_ms = None
        d.last_poll_query = query

        # Fetch messages from IMAP (whole session in one executor call per D-05/Pitfall 6).
        try:
            raw_messages, max_uid = await imap_client.fetch_shipping_emails(
                host=entry.data[CONF_IMAP_HOST],
                port=entry.data[CONF_IMAP_PORT],
                username=entry.data[CONF_IMAP_USERNAME],
                password=entry.data[CONF_IMAP_PASSWORD],
                tls_mode=entry.data[CONF_IMAP_TLS],
                search_criteria=query,
                since_uid=self._last_imap_uid,
            )
        except ImapAuthError as err:
            raise ConfigEntryAuthFailed("IMAP auth error") from err
        except ImapTransientError as err:
            raise UpdateFailed(f"IMAP transient error: {err}") from err

        # Set up parser + parcelapp client (same as Gmail path).
        parser = EmailParser()
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=entry.data[CONF_API_KEY],
        )
        current_data: dict[str, ShipmentData] = dict(self.data or {})
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None and now < self._quota_exhausted_until
        )
        any_forwarded = False
        any_quota_blocked = False
        any_transient_error = False

        for msg_info in raw_messages:
            # IMAP uid used as message_id for deduplication (D-08).
            uid_str = str(msg_info["uid"])
            if uid_str in self._forwarded_ids:
                continue

            raw_bytes: bytes = msg_info["raw"]
            html = extract_html_body_imap(raw_bytes)
            if not html:
                _LOGGER.debug("IMAP UID %s: no HTML body found, skipping", uid_str)
                d.emails_scanned_total += 1
                d.last_poll_emails_scanned += 1
                d.last_poll_skip_reasons.append({"message_id": uid_str, "reason": "no_html_body"})
                continue

            # Assign a synthetic email_date (0 = unknown — IMAP does not guarantee internalDate).
            # Phase 9 does not track IMAP email timestamps in _last_email_timestamp (that is
            # a Gmail-specific pattern). IMAP uses UID-based dedup instead.
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
                    {"message_id": uid_str, "reason": "parse_exception"}
                )
                continue
            d.emails_scanned_total += 1
            d.last_poll_emails_scanned += 1
            if result.shipment is None:
                d.last_poll_skip_reasons.append(
                    {"message_id": uid_str, "reason": result.skip_reason}
                )
            else:
                d.emails_matched_total += 1
                d.last_poll_emails_matched += 1
                d.tracking_numbers_found_total += 1
                d.last_poll_found.append(
                    {
                        "tracking_number": result.shipment.tracking_number,
                        "carrier": result.shipment.carrier_name,
                        "order_name": result.shipment.order_name,
                        "message_id": uid_str,
                    }
                )
            for key, hit in result.keyword_hits.items():
                if hit and key in d.keyword_hits_per_key:
                    d.keyword_hits_per_key[key] += 1
                    d.keyword_hits_total += 1
                    d.last_poll_keyword_hits += 1
            if result.shipment is None:
                continue
            shipment = result.shipment

            if quota_blocked:
                _LOGGER.debug("Skipping forward of IMAP UID %s — quota exhausted", uid_str)
                any_quota_blocked = True
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
                any_quota_blocked = True
                continue
            except ParcelAppInvalidTrackingError as err:
                _LOGGER.error(
                    "Invalid tracking for IMAP UID %s (permanent 400 — suppressing retries): %s",
                    uid_str,
                    err,
                )
                # Add to forwarded_ids to prevent infinite retry draining quota.
                self._forwarded_ids.add(uid_str)
                any_forwarded = True  # triggers store save so suppression is persisted
                continue
            except ParcelAppTransientError as err:
                _LOGGER.warning("parcelapp.net transient error for UID %s: %s", uid_str, err)
                any_transient_error = True
                continue

            self._forwarded_ids.add(uid_str)
            current_data[uid_str] = shipment
            any_forwarded = True

        # Phase 7: capture per-poll timing.
        d.last_poll_time = poll_start
        d.last_poll_duration_ms = (time.time() - poll_start) * 1000

        # Update last_imap_uid after successful fetch (D-08).
        # CRITICAL: Do NOT advance if any message was quota-blocked this cycle.
        # When quota-blocked, keep _last_imap_uid at its previous value so the UID
        # SEARCH on the next poll re-includes those messages — mirrors the Gmail
        # path which does not advance max_email_date when quota_blocked (line 310).
        store_dirty = any_forwarded
        if (
            not any_quota_blocked
            and not any_transient_error
            and max_uid is not None
            and (self._last_imap_uid is None or max_uid > self._last_imap_uid)
        ):
            self._last_imap_uid = max_uid
            store_dirty = True  # UID advanced — persist even if no shipments forwarded this cycle

        if store_dirty:
            await self._async_save_store()

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
