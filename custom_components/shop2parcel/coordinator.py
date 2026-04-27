"""Shop2Parcel DataUpdateCoordinator.

Phase 4: orchestrates Gmail polling, email parsing, parcelapp.net forwarding,
persistent deduplication, and quota backoff. All atomic behaviors live in
api/*.py — this module only sequences them.

Locked decisions (CONTEXT.md):
- D-01: data is dict[str, ShipmentData] keyed by Gmail message_id.
- D-02: data accumulates all ever-seen shipments — no status filtering here.
- D-04: Store schema {"forwarded_ids": [...], "quota_exhausted_until": int | None}.
- D-05: Quota exhausted -> Gmail polls continue, POST step skipped.
- D-06: quota_exhausted_until = err.reset_at OR next_midnight_utc().
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.carrier_codes import normalize_carrier
from .api.email_parser import EmailParser, ShipmentData
from .api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.gmail_client import GmailClient, extract_html_body
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_GMAIL_QUERY,
    CONF_POLL_INTERVAL,
    DEFAULT_GMAIL_QUERY,
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
    today_utc = datetime.now(timezone.utc).date()
    return int(
        datetime.combine(
            today_utc + timedelta(days=1),
            dt_time.min,
            tzinfo=timezone.utc,
        ).timestamp()
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
        self._entry = entry
        self._store: Store = Store(hass, version=STORAGE_VERSION, key=f"shop2parcel.{entry.entry_id}")
        self._forwarded_ids: set[str] = set()
        self._quota_exhausted_until: int | None = None
        self._last_email_timestamp: int | None = None

    async def async_load_store(self) -> None:
        """Hydrate dedup + quota state from Store.

        MUST be called before async_config_entry_first_refresh().  Failing to do so
        leaves _forwarded_ids empty, causing every previously forwarded shipment to be
        re-POSTed on startup (RESEARCH.md Pitfall 1).

        async_setup_entry in __init__.py is the canonical caller; do not call this
        method from any other site without careful thought about sequencing.
        """
        stored = await self._store.async_load() or {}
        self._forwarded_ids = set(stored.get("forwarded_ids", []))
        self._quota_exhausted_until = stored.get("quota_exhausted_until")

    async def _async_save_store(self) -> None:
        """Persist current dedup + quota state to Store."""
        await self._store.async_save(
            {
                "forwarded_ids": sorted(self._forwarded_ids),
                "quota_exhausted_until": self._quota_exhausted_until,
            }
        )

    async def _async_update_data(self) -> dict[str, ShipmentData]:
        """Run one poll cycle: list Gmail, parse new emails, forward to parcelapp."""
        # 1. Refresh OAuth2 token (HA framework owns the lifecycle).
        oauth_session = config_entry_oauth2_flow.OAuth2Session(self.hass, self._entry)
        try:
            await oauth_session.async_ensure_token_valid()
        except Exception as err:  # noqa: BLE001 — translate to HA exception
            raise ConfigEntryAuthFailed("Gmail token refresh failed") from err
        # Read token from the session object, not entry.data — async_ensure_token_valid
        # may create a new data dict on the config entry (HA 2024.x+), so self._entry.data
        # could still hold the pre-refresh snapshot.  The OAuth2Session already holds the
        # refreshed token after the await above.
        access_token: str = oauth_session.token["access_token"]

        # 2. List Gmail messages matching the configured query.
        gmail = GmailClient(self.hass.async_add_executor_job)
        query = self._entry.options.get(CONF_GMAIL_QUERY, DEFAULT_GMAIL_QUERY)
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
            api_key=self._entry.data["api_key"],
        )
        current_data: dict[str, ShipmentData] = dict(self.data or {})
        now = int(time.time())
        quota_blocked = (
            self._quota_exhausted_until is not None
            and now < self._quota_exhausted_until
        )
        max_email_date = self._last_email_timestamp or 0

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

            html = extract_html_body(msg.get("payload", {}))
            if not html:
                continue
            try:
                email_date = int(msg.get("internalDate", "0")) // 1000
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Unexpected internalDate value for message %s; skipping", msg_id
                )
                continue
            shipment = parser.parse(html, msg_id, email_date)
            if shipment is None:
                continue

            # 5. Quota guard (D-05): when quota is exhausted, do NOT add the shipment
            # to current_data and do NOT advance max_email_date.  Keeping the message
            # "unseen" ensures _last_email_timestamp is not advanced past it, so Gmail
            # will return it again on the next poll cycle once quota has reset — at which
            # point it will be properly POSTed and added to forwarded_ids.  Advancing
            # current_data or max_email_date here would cause the message to be skipped
            # by the after_timestamp filter and never forwarded (FWRD-02 violation).
            if quota_blocked:
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
                _LOGGER.error("Invalid tracking for message %s: %s", msg_id, err)
                # NOT added to forwarded_ids per CONTEXT.md "Claude's Discretion".
                continue
            except ParcelAppTransientError as err:
                _LOGGER.warning("parcelapp.net transient error for %s: %s", msg_id, err)
                continue

            # 6. Success — update data and persist (current_data deferred to here so
            # quota/error paths cannot produce a stale entry, satisfying FWRD-02).
            self._forwarded_ids.add(msg_id)
            current_data[msg_id] = shipment
            if email_date > max_email_date:
                max_email_date = email_date
            await self._async_save_store()

        if max_email_date > (self._last_email_timestamp or 0):
            self._last_email_timestamp = max_email_date

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

    async def async_cleanup_delivered(self, now: datetime) -> None:
        """Remove delivered shipments from coordinator.data and the entity registry.

        Phase 5 D-08/D-09/D-11: scheduled hourly via async_track_time_interval
        (24h period set in __init__.py). Match parcelapp deliveries to
        coordinator entries by tracking_number; status_code == 0 means
        Completed (parcelapp-api.md). Removal is immediate.

        The 'now' parameter is required by async_track_time_interval's callback
        signature even though we ignore it (RESEARCH.md Pitfall 2).

        Exceptions are caught + logged + return early — DO NOT raise
        ConfigEntryAuthFailed or UpdateFailed from here (RESEARCH.md Pitfall 5):
        those are only meaningful inside _async_update_data.
        """
        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self._entry.data["api_key"],
        )
        try:
            deliveries = await parcel_client.async_get_deliveries(filter_mode="recent")
        except ParcelAppAuthError as err:
            _LOGGER.error("parcelapp auth error during cleanup: %s", err)
            return
        except ParcelAppTransientError as err:
            _LOGGER.warning("parcelapp transient error during cleanup: %s", err)
            return

        if not self.data:
            return

        # D-11: O(1) reverse lookup {tracking_number: message_id}
        tracking_to_msg_id = {
            shipment.tracking_number: msg_id
            for msg_id, shipment in self.data.items()
        }
        # Use .get() — guard against missing 'status_code' (RESEARCH.md Security V5)
        delivered_tracking = {
            d["tracking_number"]
            for d in deliveries
            if d.get("status_code") == 0 and "tracking_number" in d
        }
        removed_ids = {
            tracking_to_msg_id[tn]
            for tn in delivered_tracking
            if tn in tracking_to_msg_id
        }
        if not removed_ids:
            return

        new_data = {k: v for k, v in self.data.items() if k not in removed_ids}
        # async_set_updated_data (NOT async_request_refresh) — externally-triggered
        # data change that bypasses the normal poll cycle (Claude's Discretion).
        self.async_set_updated_data(new_data)

        # Explicit entity registry removal — HA does NOT auto-remove entities
        # when their key disappears from coordinator.data (RESEARCH.md Pitfall 1).
        entity_registry = er.async_get(self.hass)
        entry_entities = entity_registry.entities.get_entries_for_config_entry_id(
            self._entry.entry_id
        )
        unique_id_to_entity_id = {
            e.unique_id: e.entity_id for e in entry_entities
        }
        for removed_id in removed_ids:
            target_uid = f"{DOMAIN}_{self._entry.entry_id}_{removed_id}"
            entity_id = unique_id_to_entity_id.get(target_uid)
            if entity_id is not None:
                entity_registry.async_remove(entity_id)
                _LOGGER.info("Removed delivered shipment entity: %s", entity_id)
