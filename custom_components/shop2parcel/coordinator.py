"""Shop2Parcel DataUpdateCoordinator — base class only.

Contains shared infrastructure: PollStats, Shop2ParcelStore, Shop2ParcelCoordinator
base class, and module-level helpers. Poll logic lives in the subclasses:
  - gmail_coordinator.GmailCoordinator  — Gmail OAuth2 + message-fetch + parse + forward
  - imap_coordinator.ImapCoordinator    — IMAP SINCE-date fetch + tracking-number dedup

Locked decisions (CONTEXT.md):
- D-01: data is dict[str, ShipmentData] keyed by Gmail message_id or IMAP UID.
- D-02: data accumulates all ever-seen shipments — no status filtering here.
- D-04: Store schema {"submitted_tracking_numbers": [...], "quota_exhausted_until": int | None, "persisted_shipments": {msg_id: ShipmentData-as-dict}}.
- D-05: Quota exhausted -> polls continue, POST step skipped.
- D-06: quota_exhausted_until = err.reset_at OR next_midnight_utc().
"""

from __future__ import annotations

import email as _email_stdlib
import logging
import re
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.email_parser import ShipmentData
from .api.exceptions import (
    ParcelAppAuthError,
    ParcelAppTransientError,
)
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_DEBUG_MODE,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 3

# Required fields and expected types for persisted_shipments store entries.
# Used by _async_load_store to validate each entry before reconstructing ShipmentData.
# Defined module-level for reuse and static-analysis visibility.
_SHIPMENT_FIELD_TYPES: dict[str, type] = {
    "tracking_number": str,
    "carrier_name": str,
    "order_name": str,
    "message_id": str,
    "email_date": int,
}


def _extract_email_meta(msg: dict) -> dict:
    """Extract subject, from, date, and snippet from a Gmail message dict.

    Returns safe defaults on any extraction failure (W10/P11-WR-05): a
    malformed encoded-word header can raise LookupError (unknown codec).
    Wrapping here prevents a single bad email from crashing the whole poll.
    """
    try:
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
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to extract email meta: %s", err)
        return {"subject": "", "from": "", "date": "", "snippet": ""}


def _extract_imap_email_meta(raw_bytes: bytes) -> dict:
    """Extract subject, from, and date from raw IMAP message bytes.

    Returns safe defaults on any extraction failure (W10/P11-WR-05): a
    malformed encoded-word header can raise LookupError (unknown codec).
    Wrapping here prevents a single bad email from crashing the whole poll.
    """
    try:
        msg = _email_stdlib.message_from_bytes(raw_bytes)
        return {
            "subject": msg.get("Subject", "") or "",
            "from": msg.get("From", "") or "",
            "date": msg.get("Date", "") or "",
            "snippet": "",
        }
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Failed to extract email meta: %s", err)
        return {"subject": "", "from": "", "date": "", "snippet": ""}


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


def _sanitise_parser_error(err: BaseException) -> str:
    """Return a safe, HTML-stripped, 100-char slice of a parser exception message.

    Closes W9/P11-WR-04: parser exceptions from BeautifulSoup can contain raw
    HTML excerpts from the offending email body.  Storing raw ``str(err)`` puts
    up to 100 chars of arbitrary email content (potentially PII) into the
    in-memory ring buffer and diagnostics JSON download.

    Steps:
      1. Strip all HTML tags (``<...>``) to prevent body content leakage.
      2. Collapse whitespace runs to single spaces for readability.
      3. Slice to 100 codepoints (not bytes) to prevent mid-grapheme truncation.
    """
    sanitised = re.sub(r"<[^>]*>", "", str(err))
    sanitised = re.sub(r"\s+", " ", sanitised).strip()
    return sanitised[:100]


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
    scan_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    scan_events_total: int = 0
    # ^ Cumulative count since HA restart. Intentionally NOT bounded by scan_events.maxlen.
    # After >50 events: scan_events_total > len(scan_events). This is correct —
    # scan_events_total is "all events ever" while scan_events is "most recent 50".


class Shop2ParcelStore(Store):
    """HA Store subclass for Shop2Parcel with v1→v2 and v2→v3 migration support.

    Overrides _async_migrate_func to drop the v1 forwarded_ids/last_imap_uid/
    last_email_timestamp schema and seed the v2 submitted_tracking_numbers schema.
    v2→v3 seeds persisted_shipments: {} to enable sensor restore across HA restarts;
    submitted_tracking_numbers and quota_exhausted_until are carried forward unchanged.
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
        if old_major_version == 2:
            entry_id = self.key.removeprefix("shop2parcel.")
            carried_tracking = old_data.get("submitted_tracking_numbers", [])
            if not isinstance(carried_tracking, list):
                _LOGGER.warning(
                    "v2 store for entry %s had corrupt submitted_tracking_numbers "
                    "(type=%s); resetting to empty during v3 migration.",
                    entry_id,
                    type(carried_tracking).__name__,
                )
                carried_tracking = []
            _LOGGER.warning(
                "Migrated Shop2Parcel Store to v3 for entry %s — "
                "persisted_shipments starts empty; sensors will restore after first poll.",
                entry_id,
            )
            return {
                "submitted_tracking_numbers": carried_tracking,
                "quota_exhausted_until": old_data.get("quota_exhausted_until"),
                "persisted_shipments": {},
            }
        if old_major_version > self.version:
            _LOGGER.warning(
                "Store contains data from a newer Shop2Parcel version (%d.%d > %d.%d) for entry %s; "
                "downgrade not supported, discarding unknown schema.",
                old_major_version,
                old_minor_version,
                self.version,
                self.minor_version,
                self.key.removeprefix("shop2parcel."),
            )
            return {"submitted_tracking_numbers": [], "quota_exhausted_until": None}
        # Same major, future minor — passthrough.  Minor-version changes are backward
        # compatible by convention so no migration is needed.
        return old_data


class Shop2ParcelCoordinator(DataUpdateCoordinator[dict[str, ShipmentData]]):
    """Base coordinator for Shop2Parcel.

    Provides shared infrastructure: store hydration, dedup state, quota tracking,
    diagnostics accumulator, and delivered-shipment cleanup. Poll logic lives in
    subclasses (GmailCoordinator, ImapCoordinator) which override _async_update_data
    and set self._email_client in their own __init__.
    """

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
        # Phase 13.1: shipment persistence across HA restarts.
        # _pending_shipments: snapshot of current live shipments written to store at poll end
        #   (and after cleanup). Always assigned immediately before _async_save_store() is called.
        # _restored_shipments: shipments loaded from store on startup (pre-first-poll).
        self._pending_shipments: dict[str, ShipmentData] = {}
        self._restored_shipments: dict[str, ShipmentData] = {}
        self._store_loaded: bool = False
        # NOTE: _email_client construction moves to subclass __init__
        # (GmailCoordinator sets GmailClient; ImapCoordinator sets ImapClient)

    @property
    def diagnostics(self) -> PollStats:
        """Public read-only view of in-memory poll diagnostics."""
        return self._diagnostics

    def _emit_scan_event(
        self,
        *,
        message_id: str,
        meta: dict,
        outcome: str,
        strategy: str | None = None,
        tracking_number: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Append a single scan event and bump scan_events_total. Single emission point.

        All scan-event dict literals across gmail_coordinator.py and
        imap_coordinator.py route through here so key order and shape are
        guaranteed consistent.  The ``extra`` kwarg merges after the standard
        keys so callers cannot accidentally rename contract keys via extra.

        Contract keys (in order): timestamp, message_id, subject, sender,
        strategy, tracking_number, outcome.  Optional extra keys (e.g.
        error_type, error_msg) are appended after outcome.
        """
        d = self._diagnostics
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message_id": message_id,
            "subject": meta.get("subject", ""),
            "sender": meta.get("from", ""),
            "strategy": strategy,
            "tracking_number": tracking_number,
            "outcome": outcome,
        }
        if extra:
            event.update(extra)
        d.scan_events.append(event)
        d.scan_events_total += 1

    async def _async_load_store(self) -> None:
        """Hydrate dedup, quota, and persisted shipments state from Store.

        MUST be called before async_config_entry_first_refresh(). Failing to do so
        leaves _submitted_tracking_numbers empty, causing every previously submitted
        tracking number to be re-POSTed on startup. It also leaves _restored_shipments
        empty, causing all previously persisted sensors to disappear until their email
        is re-scanned.

        async_setup_entry in __init__.py is the canonical caller; do not call this
        method from any other site without careful thought about sequencing.
        """
        try:
            stored = await self._store.async_load() or {}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to load Shop2Parcel store for entry %s — starting with empty state: %s",
                self._store.key.removeprefix("shop2parcel."),
                err,
                exc_info=True,
            )
            stored = {}
        stored_list = stored.get("submitted_tracking_numbers", [])
        if not isinstance(stored_list, list):
            _LOGGER.warning(
                "submitted_tracking_numbers in store is not a list (type=%s); "
                "treating as empty — dedup will repopulate from parcelapp 'already added' 400s.",
                type(stored_list).__name__,
            )
            stored_list = []
        self._submitted_tracking_numbers = OrderedDict(
            (tn, None) for tn in stored_list if isinstance(tn, str)
        )
        qe = stored.get("quota_exhausted_until")
        self._quota_exhausted_until = qe if isinstance(qe, int) else None
        _LOGGER.debug(
            "Loaded %d submitted tracking numbers from store",
            len(self._submitted_tracking_numbers),
        )
        # Phase 13.1 (R5): load persisted_shipments with per-entry type validation.
        # Each entry must be a dict with exactly the 5 fields in _SHIPMENT_FIELD_TYPES.
        # Invalid entries are skipped with a WARNING (T-13.1-04 / ASVS V5).
        restored: dict[str, ShipmentData] = {}
        raw_shipments = stored.get("persisted_shipments", {})
        if not isinstance(raw_shipments, dict):
            _LOGGER.warning(
                "persisted_shipments in store is not a dict (type=%s) for entry %s; "
                "treating as empty — all sensors will be empty until the next poll.",
                type(raw_shipments).__name__,
                self._store.key.removeprefix("shop2parcel."),
            )
            raw_shipments = {}
        for msg_id, entry in raw_shipments.items():
            if not isinstance(entry, dict) or not all(
                k in entry and isinstance(entry[k], t)
                for k, t in _SHIPMENT_FIELD_TYPES.items()
            ):
                _LOGGER.warning(
                    "persisted_shipments entry for %r is invalid — skipping", msg_id
                )
                continue
            try:
                restored[msg_id] = ShipmentData(**{k: entry[k] for k in _SHIPMENT_FIELD_TYPES})
            except TypeError as err:
                _LOGGER.warning(
                    "persisted_shipments entry for %r could not be reconstructed (%s) — skipping",
                    msg_id,
                    err,
                )
        self._restored_shipments = restored
        self._store_loaded = True
        _LOGGER.debug(
            "Restored %d persisted shipments from store", len(self._restored_shipments)
        )

    async def _async_save_store(self) -> None:
        """Schedule a debounced persist of current dedup, quota, and shipment state to Store.

        Uses async_delay_save (W1/P13-WR-06) so rapid per-message saves within
        a single poll are coalesced into one write. async_delay_save is
        synchronous — it schedules the write; it does NOT write immediately.

        The lambda captures _pending_shipments by reference; callers MUST assign
        self._pending_shipments before calling this method — the 5-second debounce
        means the value at fire-time is what gets stored.

        The try/except guards against AttributeError (uninitialized store/hass) and
        other unexpected scheduling failures; actual write errors surface in HA logs
        when the delayed timer fires.
        """
        try:
            self._store.async_delay_save(
                lambda: {
                    "submitted_tracking_numbers": list(self._submitted_tracking_numbers.keys()),
                    "quota_exhausted_until": self._quota_exhausted_until,
                    "persisted_shipments": {
                        msg_id: asdict(shipment)
                        for msg_id, shipment in self._pending_shipments.items()
                    },
                },
                delay=5,
            )
            _LOGGER.debug(
                "Scheduled debounced save for %d submitted tracking numbers and %d persisted shipments",
                len(self._submitted_tracking_numbers),
                len(self._pending_shipments),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to schedule dedup state save — dedup may re-submit on next restart: %s",
                err,
                exc_info=True,
            )

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

        if self.config_entry is None:
            _LOGGER.error("async_cleanup_delivered called with no config_entry — skipping")
            return
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
        # Phase 13.1 (R4): persist the post-cleanup state so delivered shipments are removed
        # from the store. Runs only when removed_ids was non-empty (the `if not removed_ids:
        # return` guard above short-circuits otherwise). Gated on debug_mode to honour the
        # DBG-03 contract (zero store writes in debug mode).
        debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)
        if not debug_mode:
            self._pending_shipments = new_data
            await self._async_save_store()
