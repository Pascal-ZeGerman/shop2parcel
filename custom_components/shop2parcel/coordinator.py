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
- Phase 18: Stage2Job frozen dataclass + async_start_stage2/async_stop_stage2/_enqueue_stage2 base methods (QUE-01, QUE-03, QUE-06).
"""

from __future__ import annotations

import asyncio
import email as _email_stdlib
import logging
import re
import time as _time
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.carrier_codes import normalize_carrier
from .api.email_parser import ShipmentData
from .api.exceptions import (
    OllamaSchemaError,
    OllamaTransientError,
    ParcelAppAlreadyAddedError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from .api.ollama_client import OllamaClient
from .api.parcelapp import ParcelAppClient
from .const import (
    CONF_API_KEY,
    CONF_CUSTOM_FIELDS,
    CONF_DEBUG_MODE,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT,
    CONF_OLLAMA_URL,
    CONF_POLL_INTERVAL,
    CONF_QUEUE_MAXLEN,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_QUEUE_MAXLEN,
    DOMAIN,
    MAX_SUBMITTED_TRACKING_NUMBERS,
)
from .extractors.ollama_extractor import OllamaExtractor
from .merge import merge_llm_authoritative

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


@dataclass(frozen=True, slots=True)
class Stage2Job:
    """Immutable payload for Stage-2 queue items.

    storage_key: normalized tracking number (dedup key — mirrors _submitted_tracking_numbers).
        Per D-02: this is the normalized tracking number, NOT the composite coordinator.data key.
    shipment: Stage-1 ShipmentData for this email.
    html_body: raw HTML body for Ollama prompt construction (Phase 19 worker reads this).
    message_id: Gmail message ID or IMAP UID — for _emit_scan_event attribution (D-06).
    meta: Email metadata dict {'subject': str, 'from': str} — for _emit_scan_event
        attribution (D-06). Populated from _extract_email_meta / _extract_imap_email_meta.
    """

    storage_key: str
    shipment: ShipmentData
    html_body: str
    message_id: str
    meta: dict


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
    stage2_enabled: bool = False
    # Phase 17 D-05: derived at async_setup_entry time; False until Ollama URL is set.


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
        # Phase 18 CR-01: sentinel so async_stop_stage2 is safe to call before
        # async_start_stage2 (e.g. Phase 19 worker or a reload race).
        self._stage2_queue: asyncio.Queue[Stage2Job] | None = None
        self._stage2_enqueued_keys: set[str] = set()
        # Phase 19: worker task and extractor sentinels — None until async_start_stage2.
        self._stage2_worker_task: asyncio.Task | None = None
        self._extractor: OllamaExtractor | None = None
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

    async def async_start_stage2(self) -> None:
        """Construct the Stage-2 queue, build OllamaExtractor, and spawn the worker.

        Called from async_setup_entry when stage2_enabled=True.

        QUE-01: reads CONF_QUEUE_MAXLEN from config entry options, clamps to [1, 256],
        constructs self._stage2_queue and self._stage2_enqueued_keys.

        Phase 19 D-02: spawns the background worker via
        entry.async_create_background_task after queue and extractor are ready (QUE-04).
        Phase 19 D-03/D-04: builds OllamaClient and OllamaExtractor once per
        setup/reload cycle from entry.options; extractor is cached as self._extractor.
        """
        raw = self.config_entry.options.get(CONF_QUEUE_MAXLEN, DEFAULT_QUEUE_MAXLEN)
        maxlen = max(1, min(256, int(raw)))
        self._stage2_queue = asyncio.Queue(maxsize=maxlen)
        self._stage2_enqueued_keys = set()
        _LOGGER.debug("Stage-2 queue constructed with maxsize=%d", maxlen)

        # Phase 19 D-03: build extractor once per setup/reload cycle.
        session = async_get_clientsession(self.hass)
        url = self.config_entry.options[CONF_OLLAMA_URL]
        model = self.config_entry.options.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)
        timeout = float(self.config_entry.options.get(CONF_OLLAMA_TIMEOUT, DEFAULT_OLLAMA_TIMEOUT))
        client = OllamaClient(session=session, base_url=url, model=model, timeout=timeout)

        # Phase 19 D-04: parse field_list from options (once at setup; never per-job).
        raw_fields = self.config_entry.options.get(CONF_CUSTOM_FIELDS, [])
        field_list = [(f["name"], f.get("description")) for f in raw_fields if isinstance(f, dict)]
        self._extractor = OllamaExtractor(client=client, field_list=field_list)

        # Phase 19 D-02: spawn worker after queue and extractor are ready (QUE-04).
        # async_create_background_task auto-registers task in entry._background_tasks;
        # HA provides a 10-second hard-cancel fallback; our 5-second explicit cancel in
        # async_stop_stage2 fires first (config_entries.py _async_process_on_unload).
        self._stage2_worker_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_stage2_worker(),
            name="shop2parcel_stage2_worker",
        )
        _LOGGER.debug("Stage-2 worker spawned for entry %s", self.config_entry.entry_id)

    async def async_stop_stage2(self) -> None:
        """Cancel worker (bounded 5 s), drain queue, release extractor.

        D-01 sequence: (1) cancel worker task, (2) drain and reset queue.
        Cancelling before drain is critical — cancelling after could leave a
        worker that already received a get() result still running.

        QUE-05: 5-second bounded wait via asyncio.wait_for; both CancelledError
        and TimeoutError suppressed so on-unload never raises.
        """
        # Step 1 (Phase 19): cancel worker task — must precede queue drain (D-01).
        # QUE-05: 5-second bounded wait via asyncio.wait_for.
        # wait_for cancels the task internally when the timeout fires (Python 3.11+).
        # No explicit task.cancel() needed before wait_for — it would be redundant.
        # CancelledError and TimeoutError are suppressed so unload never raises (QUE-05).
        if self._stage2_worker_task is not None and not self._stage2_worker_task.done():
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._stage2_worker_task, timeout=5.0)
        self._stage2_worker_task = None
        self._extractor = None  # release OllamaClient / aiohttp session reference

        # Step 2 (Phase 18 CR-02): drain and reset queue, preserving maxsize.
        if self._stage2_queue is None:
            return
        prev_maxsize = self._stage2_queue.maxsize
        while not self._stage2_queue.empty():
            try:
                self._stage2_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # CR-02: preserve maxsize so backpressure invariant holds after a reload.
        self._stage2_queue = asyncio.Queue(maxsize=prev_maxsize)
        self._stage2_enqueued_keys.clear()
        _LOGGER.debug("Stage-2 queue stopped and cleared")

    def _enqueue_stage2(
        self,
        normalized_tn: str,
        storage_key: str,
        shipment: ShipmentData,
        html_body: str,
        *,
        message_id: str,
        meta: dict,
    ) -> None:
        """Enqueue a Stage-2 job with in-flight dedup and drop-newest backpressure.

        QUE-06: skips silently if normalized_tn already in _stage2_enqueued_keys.
        QUE-03: on QueueFull, logs warning + emits stage2_dropped_backpressure event,
                does NOT write to _submitted_tracking_numbers.
        Uses put_nowait (never await put) per QUE-07.

        The add to _stage2_enqueued_keys happens ONLY after successful put_nowait
        (Anti-Patterns §3) — if put_nowait raises QueueFull, the key is NOT added.
        """
        if normalized_tn in self._stage2_enqueued_keys:
            return  # silent skip — already in flight (QUE-06)
        job = Stage2Job(
            storage_key=storage_key,
            shipment=shipment,
            html_body=html_body,
            message_id=message_id,
            meta=meta,
        )
        try:
            self._stage2_queue.put_nowait(job)
        except asyncio.QueueFull:
            _LOGGER.warning(
                "Stage-2 queue full (%d/%d); dropping %s — will retry next poll",
                self._stage2_queue.qsize(),
                self._stage2_queue.maxsize,
                normalized_tn,
            )
            self._emit_scan_event(
                message_id=message_id,
                meta=meta,
                outcome="stage2_dropped_backpressure",
                tracking_number=normalized_tn,
            )
            return  # NO dedup write (QUE-03), NO add to enqueued_keys
        self._stage2_enqueued_keys.add(normalized_tn)  # add AFTER successful put (Anti-Patterns §3)

    async def _async_stage2_worker(self) -> None:
        """Single long-lived background worker draining _stage2_queue serially.

        QUE-02: runs until cancelled via task.cancel() from async_stop_stage2.
        Sole blocking point: await self._stage2_queue.get().

        CancelledError at any await point propagates to the outer try — never
        swallowed. Phase 21 owns loud failure surface (FAIL-01..05); Phase 19
        logs quietly and does NOT crash the worker on OllamaTransientError /
        OllamaSchemaError.

        task_done() in finally: ensures queue.join() (if used in tests) never hangs.
        """
        _LOGGER.debug("Stage-2 worker started for entry %s", self.config_entry.entry_id)
        try:
            while True:
                job: Stage2Job = await self._stage2_queue.get()
                normalized_tn = job.storage_key
                try:
                    await self._async_process_stage2_job(job)
                except asyncio.CancelledError:
                    self._stage2_enqueued_keys.discard(normalized_tn)  # prevent permanent key lock
                    raise  # propagate — shuts down the worker
                except Exception:  # noqa: BLE001
                    # Phase 21 owns notifications. Phase 19: log debug, discard in-flight key.
                    _LOGGER.debug(
                        "Stage-2 worker: error on job %s — discarding in-flight key; next poll retries",
                        normalized_tn,
                        exc_info=True,
                    )
                    self._stage2_enqueued_keys.discard(normalized_tn)
                finally:
                    self._stage2_queue.task_done()
        except asyncio.CancelledError:
            _LOGGER.debug("Stage-2 worker cancelled — exiting cleanly")
            raise

    async def _async_process_stage2_job(self, job: Stage2Job) -> None:
        """Process one Stage2Job: extract via Ollama, merge, POST to parcelapp, update state.

        Called by _async_stage2_worker for each drained job.

        Implements D-05 (per-job store save after POST), D-06 (snapshot pattern),
        MRG-01 (extractor called for every job), MRG-02 (merged shipment POSTed),
        MRG-03 (stage2_conflict event emitted when LLM disagrees with Stage-1),
        FAIL-03 (OllamaTransientError / OllamaSchemaError → no POST, no dedup write).

        Error hierarchy mirrors inline POST in gmail_coordinator.py lines 434-511:
          - ParcelAppAuthError  -> raise ConfigEntryAuthFailed (caught by worker BLE001)
          - ParcelAppQuotaError -> update _quota_exhausted_until + log warning
          - ParcelAppAlreadyAddedError / ParcelAppInvalidTrackingError -> dedup-write + continue
          - ParcelAppTransientError -> log warning, discard key, allow retry

        Note (T-19-08): ConfigEntryAuthFailed raised here is caught by _async_stage2_worker's
        except Exception (BLE001) — HA's reauth flow is NOT triggered from worker context.
        Auth failures degrade silently to key-discard + next-poll retry; reauth is triggered
        by the next _async_update_data poll cycle instead.
        """
        normalized_tn = job.storage_key

        # MRG-02: default to Stage-1 shipment; replaced by merged result after extraction.
        merged_shipment = job.shipment

        if self._extractor is not None:
            try:
                stage2_result = await self._extractor.async_extract(job.html_body, job.shipment)
            except (OllamaTransientError, OllamaSchemaError):  # fmt: skip
                # FAIL-03: Ollama error — no POST, no dedup write; next poll retries.
                _LOGGER.debug(
                    "Stage-2 worker: Ollama error on job %s — no POST, no dedup write; will retry next poll",
                    normalized_tn,
                    exc_info=True,
                )
                self._stage2_enqueued_keys.discard(normalized_tn)
                return

            # MRG-02: merge Stage-2 result into Stage-1 shipment.
            merged_shipment, conflicts = merge_llm_authoritative(job.shipment, stage2_result)

            # MRG-03: emit exactly one stage2_conflict event if LLM disagreed.
            if conflicts:
                self._emit_scan_event(
                    message_id=job.message_id,
                    meta=job.meta,
                    outcome="stage2_conflict",
                    tracking_number=normalized_tn,
                    extra={"conflicts": conflicts},
                )

        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )

        # Quota guard: skip POST and discard key if quota is exhausted.
        now = int(_time.time())
        if self._quota_exhausted_until is not None and now < self._quota_exhausted_until:
            _LOGGER.debug("Stage-2 worker: quota exhausted — skipping POST for %s", normalized_tn)
            self._stage2_enqueued_keys.discard(normalized_tn)
            return

        carrier_code = normalize_carrier(merged_shipment.carrier_name)
        try:
            await parcel_client.async_add_delivery(
                tracking_number=merged_shipment.tracking_number,
                carrier_code=carrier_code,
                description=merged_shipment.order_name or merged_shipment.tracking_number,
            )
        except ParcelAppAuthError as err:
            raise ConfigEntryAuthFailed("parcelapp.net auth error") from err
        except ParcelAppQuotaError as err:
            self._quota_exhausted_until = (
                err.reset_at if err.reset_at is not None else _next_midnight_utc()
            )
            _LOGGER.warning(
                "parcelapp.net daily quota exhausted (Stage-2 worker); forwarding paused: %s",
                str(err)[:100],
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return
        except (ParcelAppAlreadyAddedError, ParcelAppInvalidTrackingError):  # fmt: skip
            # Write dedup so next poll does not retry; discard in-flight key.
            self._submitted_tracking_numbers[normalized_tn] = None
            if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                self._submitted_tracking_numbers.popitem(last=False)
            self._stage2_enqueued_keys.discard(normalized_tn)
            base = self.data if self.data is not None else {}
            updated = {**base, job.storage_key: merged_shipment}
            self._pending_shipments = updated
            await self._async_save_store()
            return
        except ParcelAppTransientError as err:
            _LOGGER.warning(
                "Stage-2 worker: parcelapp transient error for %s: %s",
                normalized_tn,
                str(err)[:100],
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return

        # Success path (mirrors gmail_coordinator.py lines 513-526).
        self._submitted_tracking_numbers[normalized_tn] = None
        if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
            self._submitted_tracking_numbers.popitem(last=False)
        self._stage2_enqueued_keys.discard(normalized_tn)  # Phase 18 WR-01 fix

        # D-06: snapshot pattern — never mutate self.data directly.
        # Guard: self.data is None before the first coordinator refresh; use empty dict as base.
        # MRG-02: persist and surface the merged shipment so sensors reflect merged state.
        base = self.data if self.data is not None else {}
        updated = {**base, job.storage_key: merged_shipment}
        self._pending_shipments = updated  # D-05: assign before save
        await self._async_save_store()  # D-05: per-job save immediately
        self.async_set_updated_data(updated)  # D-06: triggers sensor creation
        _LOGGER.debug("Stage-2 worker: posted %s successfully", normalized_tn)

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
        except OSError as err:
            _LOGGER.error(
                "Failed to load Shop2Parcel store for entry %s (I/O error) — "
                "starting with empty state. Previously submitted tracking numbers "
                "may be re-submitted on the next poll, consuming ParcelApp quota: %s",
                self._store.key.removeprefix("shop2parcel."),
                err,
                exc_info=True,
            )
            stored = {}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Unexpected error loading Shop2Parcel store for entry %s — "
                "HA will retry setup with backoff: %s",
                self._store.key.removeprefix("shop2parcel."),
                err,
                exc_info=True,
            )
            # ConfigEntryNotReady signals HA to retry async_setup_entry with
            # exponential backoff rather than treating this as a polling failure.
            raise ConfigEntryNotReady(f"Shop2Parcel store load failed: {err}") from err
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
                k in entry and isinstance(entry[k], t) for k, t in _SHIPMENT_FIELD_TYPES.items()
            ):
                _LOGGER.warning("persisted_shipments entry for %r is invalid — skipping", msg_id)
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
        _LOGGER.debug("Restored %d persisted shipments from store", len(self._restored_shipments))

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

        asdict() calls are intentionally outside the try block so a serialization
        failure raises immediately (loud failure) rather than being silently swallowed
        and producing a stale snapshot at the next debounce fire.
        """
        snapshot_tracking = list(self._submitted_tracking_numbers.keys())
        snapshot_quota = self._quota_exhausted_until
        snapshot_shipments = {
            msg_id: asdict(shipment) for msg_id, shipment in self._pending_shipments.items()
        }
        try:
            self._store.async_delay_save(
                lambda: {
                    "submitted_tracking_numbers": snapshot_tracking,
                    "quota_exhausted_until": snapshot_quota,
                    "persisted_shipments": snapshot_shipments,
                },
                delay=5,
            )
            _LOGGER.debug(
                "Scheduled debounced save for %d submitted tracking numbers and %d persisted shipments",
                len(snapshot_tracking),
                len(snapshot_shipments),
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
            _LOGGER.error("Unexpected error during cleanup: %s", err, exc_info=True)
            return

        # D-11: O(1) reverse lookup {tracking_number: message_id}
        # Multi-shipment dedup invariant: storage keys are either a bare msg_id
        # (single-shipment emails) or f"{msg_id}::{tracking_number}" (multi-shipment
        # digests — see gmail_coordinator.py ~line 339, imap_coordinator.py ~line 284).
        # Because this dict is keyed on tracking_number (unique per shipment), each
        # composite key maps to a distinct entry — do NOT collapse composite keys back
        # to bare msg_id in future refactors; each composite key is a distinct HA entity.
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
