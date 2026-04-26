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
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
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
    return int(
        datetime.combine(
            date.today() + timedelta(days=1),
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

    async def _async_load_store(self) -> None:
        """Hydrate dedup + quota state from Store. Call before async_config_entry_first_refresh."""
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
            email_date = int(msg.get("internalDate", "0")) // 1000
            shipment = parser.parse(html, msg_id, email_date)
            if shipment is None:
                continue

            current_data[msg_id] = shipment
            if email_date > max_email_date:
                max_email_date = email_date

            # 5. Quota guard (D-05): keep accumulating shipments, skip POST.
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

            # 6. Success — persist immediately (Pitfall 2 in RESEARCH.md).
            self._forwarded_ids.add(msg_id)
            await self._async_save_store()

        if max_email_date > (self._last_email_timestamp or 0):
            self._last_email_timestamp = max_email_date

        return current_data
