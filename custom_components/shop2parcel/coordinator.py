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

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
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
    MAX_STAGE2_POSTS_PER_POLL,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    SEEN_MESSAGE_IDS_MAXLEN,
    STAGE2_NOTIFY_COOLDOWN_S,
    STAGE2_NOTIFY_THRESHOLD,
    normalize_tracking_number,
    stage2_cap_notification_id,
    stage2_failing_notification_id,
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


def _safe_custom_attributes(entry: dict) -> dict[str, str | None]:
    """Return custom_attributes from a persisted_shipments entry with wrong-type guard.

    FLD-03 / D-10 / D-11: Returns entry.get("custom_attributes", {}) if the value is a
    dict. If the value has the wrong type (e.g. list, str — from hand-editing or a
    downgrade scenario), emits a WARNING and returns {} so locked fields restore
    correctly and the integration continues operating (ASVS V5 input validation).

    Placed here immediately after _SHIPMENT_FIELD_TYPES (per 21-RESEARCH.md Open
    Question #2) so it is adjacent to the dict it reads from and mirrors the
    existing module-level helper pattern (_extract_email_meta).
    """
    val = entry.get("custom_attributes", {})
    if not isinstance(val, dict):
        _LOGGER.warning(
            "persisted_shipments entry has wrong type for custom_attributes (type=%s); "
            "returning empty dict.",
            type(val).__name__,
        )
        return {}
    return val


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


def _today_utc_str() -> str:
    """Return today's UTC date as 'YYYY-MM-DD' string.

    Used for UTC date-rollover check in _maybe_reset_used_today.
    UTC has no DST so the rollover is always at exactly 00:00 UTC.
    (Phase 26 RESEARCH Pitfall 3 — always use UTC, not local time.)
    """
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _valid_nonneg_int(value: object) -> bool:
    """True only for a genuine non-negative int.

    Excludes bool (an int subclass — `isinstance(True, int)` is True) and negatives,
    so corrupt or hand-edited operational counters in the store cannot inflate state
    (T-26-01). Used to guard total_forwarded / used_today / last_forwarded_ts on load.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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

    storage_key: coordinator.data entity key (Gmail message ID or composite key such as
        "17a3f4c8b::1Z999AA10123456784"). Used as the dict key when writing to coordinator.data.
    normalized_tn: normalized tracking number — the dedup key that mirrors
        _submitted_tracking_numbers and _stage2_enqueued_keys. This is the value passed as
        the first positional argument to _enqueue_stage2.
    shipment: Stage-1 ShipmentData for this email.
    html_body: raw HTML body for Ollama prompt construction (Phase 19 worker reads this).
    message_id: Gmail message ID or IMAP UID — for _emit_scan_event attribution (D-06).
    meta: Email metadata dict {'subject': str, 'from': str} — for _emit_scan_event
        attribution (D-06). Populated from _extract_email_meta / _extract_imap_email_meta.
    raw_msg_id: Phase 27 fix — the unprefixed seen-ID cache key (Gmail message ID) for the
        message that produced this job, or None for paths that do not participate in the
        seen-ID gate (e.g. IMAP). When set, the worker un-marks this ID from
        _seen_message_ids on any retry-discard exit so a deferred job is re-fetched and
        re-enqueued next poll instead of being permanently filtered out (the message was
        optimistically marked seen at enqueue time).
    prefetched_result: Phase 27 fix — a Stage2Result already produced by the Ollama
        fallback gatekeeper (gmail_coordinator). When set, the worker reuses it instead of
        calling the extractor a second time on the same HTML (avoids double extraction).
    """

    storage_key: str
    normalized_tn: str
    shipment: ShipmentData
    html_body: str
    message_id: str
    meta: dict
    raw_msg_id: str | None = None
    prefetched_result: Any | None = None


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
    # Phase 21 DIAG-02: lifetime Stage-2 counters; auto-included in asdict() diagnostics download.
    stage2_enqueued_total: int = 0
    stage2_succeeded_total: int = 0
    stage2_failed_total: int = 0
    stage2_dropped_backpressure_total: int = 0
    stage2_schema_error_total: int = 0
    stage2_conflict_total: int = 0
    stage2_quota_skipped_total: int = 0
    stage2_cap_skip_total: int = 0
    stage2_already_added_total: int = 0
    stage2_transient_error_total: int = 0
    # LLM performance counters (populated by _async_process_stage2_job on each successful
    # extractor call; used by OllamaLatencySensor and OllamaParseQualitySensor).
    stage2_llm_attempts_total: int = 0
    stage2_llm_calls_total: int = 0
    stage2_llm_latency_ms_sum: float = 0.0
    stage2_llm_latency_ms_last: float | None = None
    stage2_llm_latency_ms_min: float | None = None
    stage2_llm_latency_ms_max: float | None = None
    stage2_fence_retry_total: int = 0

    def record_llm_call(self, latency_ms: float, *, fence_retry: bool) -> None:
        """Accumulate one successful LLM call into the latency and retry counters."""
        self.stage2_llm_calls_total += 1
        self.stage2_llm_latency_ms_sum += latency_ms
        self.stage2_llm_latency_ms_last = latency_ms
        if self.stage2_llm_latency_ms_min is None or latency_ms < self.stage2_llm_latency_ms_min:
            self.stage2_llm_latency_ms_min = latency_ms
        if self.stage2_llm_latency_ms_max is None or latency_ms > self.stage2_llm_latency_ms_max:
            self.stage2_llm_latency_ms_max = latency_ms
        if fence_retry:
            self.stage2_fence_retry_total += 1


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
            return {
                "submitted_tracking_numbers": [],
                "quota_exhausted_until": None,
                "persisted_shipments": {},
            }
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
        # Phase 27 Plan 02: seen-message-ID cache (mirrors _submitted_tracking_numbers).
        # Tracks Gmail message IDs already processed (any outcome) in a prior poll so
        # they are skipped before async_get_message is called.  FIFO-bounded at
        # SEEN_MESSAGE_IDS_MAXLEN (10 000) to cap memory; oldest ID evicted on overflow.
        # Persisted via additive store key "seen_message_ids" — no STORAGE_VERSION bump.
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._quota_exhausted_until: int | None = None
        # Phase 7 (D-04): in-memory diagnostic accumulator. Resets on HA restart.
        self._diagnostics: PollStats = PollStats()
        # Phase 13.1: shipment persistence across HA restarts.
        # _pending_shipments: snapshot of current live shipments written to store at poll end
        #   (and after cleanup). Always assigned immediately before _async_save_store() is called.
        # _restored_shipments: shipments loaded from store on startup (pre-first-poll).
        self._pending_shipments: dict[str, ShipmentData] = {}
        self._restored_shipments: dict[str, ShipmentData] = {}
        # Phase 23 D-03 / LD-05: post-deferred merged shipments awaiting POSTing to ParcelApp.
        # Populated when quota is exhausted and LLM extraction succeeds; drained on next
        # quota-free poll. Persisted to the 'pending_posts' store key so entries survive
        # HA restarts (quota may clear between restart and next poll).
        # NOT reset on _reset_stage2_poll_counters — must survive across polls.
        self._pending_posts: dict[str, ShipmentData] = {}
        # Phase 26: operational-health persisted counters.
        # Persisted across HA restarts via additive store keys (no STORAGE_VERSION bump).
        # Incremented only on genuine 2xx POST-success via _record_forward().
        # _used_today_date holds the UTC date string when _used_today was last reset.
        self._total_forwarded: int = 0
        self._last_forwarded_ts: int | None = None
        self._used_today: int = 0
        self._used_today_date: str = ""
        self._store_loaded: bool = False
        # Phase 18 CR-01: sentinel so async_stop_stage2 is safe to call before
        # async_start_stage2 (e.g. Phase 19 worker or a reload race).
        self._stage2_queue: asyncio.Queue[Stage2Job] | None = None
        self._stage2_enqueued_keys: set[str] = set()
        # Phase 19: worker task and extractor sentinels — None until async_start_stage2.
        self._stage2_worker_task: asyncio.Task | None = None
        self._extractor: OllamaExtractor | None = None
        # Phase 20 MRG-05 / D-11: per-poll Stage-2 POST counters.
        # Both are reset at the top of each poll cycle via _reset_stage2_poll_counters().
        # _stage2_posts_this_poll: count of successful Stage-2 POSTs in the current poll.
        # _stage2_cap_notified_this_poll: True once the cap notification has been fired
        #   this poll — ensures at most one HA notification per poll (D-10 / T-20-03-02).
        self._stage2_posts_this_poll: int = 0
        self._stage2_cap_notified_this_poll: bool = False
        # Phase 23 AC-8: throttles the quota-skip WARNING to at most one per poll
        # (mirrors _stage2_cap_notified_this_poll). Reset in _reset_stage2_poll_counters.
        self._stage2_quota_warned_this_poll: bool = False
        # Phase 27 Plan 03: per-poll fallback extraction counter — how many Ollama fallback
        # extractions have run on Stage-1-miss emails this poll. Capped at
        # MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL (10). Reset each poll via
        # _reset_stage2_poll_counters (mirrors _stage2_posts_this_poll).
        self._stage2_fallback_extractions_this_poll: int = 0
        # M6A-01: poll-in-progress flag — set True at the top of each _async_update_data
        # call in GmailCoordinator and ImapCoordinator (after _reset_stage2_poll_counters),
        # then reset to False in the finally block before returning.  Surfaced via the
        # email_processing_active property.  Never modified inside _async_update_data itself —
        # only the subclass poll methods touch this flag.
        self._poll_in_progress: bool = False
        # Phase 21 FAIL-04/05: consecutive-failure streak across polls + notification cooldown timestamp.
        # Persist across polls; reset only on real success (line 710) or async_stop_stage2 (SPEC Req #5).
        self._stage2_consecutive_failures: int = 0
        self._stage2_last_notify_ts: float | None = None
        # Finding 3: time-boundary refresh timers for the should_poll=False operational
        # entities. quota_is_exhausted / used_today flip by the passage of time (no event),
        # so without these the Problem + Quota sensors stay stale until the next poll.
        # Gated by _operational_timers_enabled (set True only from async_setup_entry via
        # enable_operational_timers) so bare-coordinator unit tests that assign
        # _quota_exhausted_until directly never schedule real (lingering) timers.
        self._operational_timers_enabled: bool = False
        self._quota_expiry_unsub: CALLBACK_TYPE | None = None
        self._midnight_unsub: CALLBACK_TYPE | None = None
        # NOTE: _email_client construction moves to subclass __init__
        # (GmailCoordinator sets GmailClient; ImapCoordinator sets ImapClient)

    def _reset_stage2_poll_counters(self) -> None:
        """Reset per-poll Stage-2 POST counters to their defaults.

        Must be called at the top of each poll cycle (in GmailCoordinator and
        ImapCoordinator _async_update_data) BEFORE any email scanning so that
        counters reflect only the current poll's activity (MRG-05 / D-11).

        Defined here on the base class (DRY) so both subclasses share a single
        implementation without duplicating the attribute reset logic.
        """
        self._stage2_posts_this_poll = 0
        self._stage2_cap_notified_this_poll = False
        self._stage2_quota_warned_this_poll = False
        # Phase 27 Plan 03: reset fallback extraction counter (mirrors _stage2_posts_this_poll).
        self._stage2_fallback_extractions_this_poll = 0
        if self.config_entry is not None:
            persistent_notification.async_dismiss(
                self.hass,
                notification_id=stage2_cap_notification_id(self.config_entry.entry_id),
            )

    def _mark_message_seen(self, msg_id: str) -> None:
        """Record a Gmail message ID as processed (seen) so it is skipped on future polls.

        Idempotent: re-marking an existing ID is a no-op (the key already exists in the
        OrderedDict; no re-ordering of existing entries occurs).  After adding, the cache
        is trimmed FIFO if it exceeds SEEN_MESSAGE_IDS_MAXLEN — mirrors the
        _submitted_tracking_numbers trim at coordinator lines 1114-1116.

        Phase 27 Plan 02 (seen-ID gate).
        """
        self._seen_message_ids[msg_id] = None
        while len(self._seen_message_ids) > SEEN_MESSAGE_IDS_MAXLEN:
            self._seen_message_ids.popitem(last=False)

    def _unmark_seen_for_retry(self, job: Stage2Job) -> None:
        """Phase 27 fix: un-mark a job's message as seen on any retry-discard exit.

        Stage-2 jobs are enqueued only after the Gmail poll has optimistically marked the
        source message seen (so the happy path never re-fetches it). When the worker defers
        a job for retry next poll — per-poll POST cap, transient Ollama/parcelapp error,
        QueueFull-on-re-enqueue, or an unexpected worker error — the message MUST be made
        re-fetchable again, otherwise the persisted seen-ID gate filters it out forever and
        the deferred shipment is silently lost. Idempotent and a no-op for jobs that carry
        no raw_msg_id (e.g. IMAP, which has no seen-ID gate).
        """
        if job.raw_msg_id is not None:
            self._seen_message_ids.pop(job.raw_msg_id, None)

    def _maybe_reset_used_today(self) -> None:
        """Reset used_today to 0 on UTC date rollover.

        Called at the start of _record_forward and from the used_today property read
        path so the ParcelApp Quota sensor always reflects the current day's count.
        UTC has no DST — rollover is deterministic at 00:00 UTC.
        (Phase 26 RESEARCH Pattern 5 / Pitfall 3.)
        """
        today = _today_utc_str()
        if self._used_today_date != today:
            self._used_today = 0
            self._used_today_date = today
            # Finding 5: persist the rollover so a restart before the next forward does
            # not restore yesterday's count. Fires only on an actual rollover (at most
            # once per UTC day); _persist_state is await-free so it is safe here even
            # when reached via the synchronous used_today property read path.
            self._persist_state()

    def _record_forward(self) -> None:
        """Record one genuine 2xx POST success to ParcelApp.

        Increments total_forwarded, used_today (with UTC date-rollover reset),
        and updates last_forwarded_ts to the current epoch second.

        MUST be called ONLY on the genuine 2xx success path — never on
        ParcelAppAlreadyAddedError or ParcelAppInvalidTrackingError, which do NOT
        consume ParcelApp quota and do NOT represent forwarding a new shipment.
        (Phase 26 RESEARCH Pitfall 1 / Pitfall 6 / STRIDE T-26-02.)

        Does NOT call _async_save_store; the existing save at each call site handles
        persistence so we avoid an extra debounce scheduling.
        """
        self._maybe_reset_used_today()
        self._total_forwarded += 1
        self._last_forwarded_ts = int(_time.time())
        self._used_today += 1

    def _record_stage2_failure(self, job: Stage2Job, err: Exception) -> None:
        """Centralize loud-surface side effects for a Stage-2 Ollama or worker-outer failure.

        Implements FAIL-01 (error log), FAIL-02 (activity event), FAIL-04 (consecutive-failure
        counter + threshold notification with 1-hour cooldown) per D-01..D-09.

        Pure-surface contract (D-04): does NOT touch control flow — callers must still
        call self._stage2_enqueued_keys.discard() and return/raise as appropriate.

        Counter scope (D-05): ONLY called from Ollama except and worker-outer except sites —
        ParcelApp errors (Auth/Quota/AlreadyAdded/InvalidTracking/Transient) are handled
        inline and never routed through this helper, so the counter tracks Ollama failures only.

        Persistence (D-07): counter persists across polls; reset only on real success
        (_record_stage2_success, D-06) or async_stop_stage2 (SPEC Req #5).

        Thin wrapper over _surface_stage2_failure (Phase 27): the Gmail Ollama fallback
        gatekeeper has no Stage2Job at failure time, so the side-effect body lives in the
        kwargs-based helper and both call sites share one consecutive-failure streak.
        """
        self._surface_stage2_failure(
            meta=job.meta,
            message_id=job.message_id,
            normalized_tn=job.normalized_tn,
            err=err,
        )

    def _surface_stage2_failure(
        self,
        *,
        meta: dict,
        message_id: str,
        normalized_tn: str,
        err: Exception,
    ) -> None:
        """Kwargs-based core of _record_stage2_failure (Phase 27 fix for finding #2).

        Called both from the Stage-2 worker (via _record_stage2_failure with a Stage2Job)
        and from the Gmail Ollama fallback gatekeeper (which fails before any job exists).
        Routing fallback failures here means a sustained Ollama outage escalates and notifies
        the same way worker-path failures do — instead of being swallowed at DEBUG level.
        """
        # FAIL-01: loud error log with 4 required fields (replaces Phase 19 _LOGGER.debug).
        _LOGGER.error(
            "Stage-2 worker: %s (%s) for '%s' from '%s'",
            type(err).__name__,
            err,
            meta.get("subject", ""),
            meta.get("from", ""),
        )

        # FAIL-02: append stage2_failed activity event with error metadata.
        # Use kwarg tracking_number= (not normalized_tn=) per _emit_scan_event signature (Pitfall 5).
        self._emit_scan_event(
            message_id=message_id,
            meta=meta,
            outcome="stage2_failed",
            tracking_number=normalized_tn,
            extra={"error_type": type(err).__name__, "error_msg": str(err)},
        )

        # FAIL-04: consecutive-failure counter increment + threshold/cooldown notification gate.
        self._stage2_consecutive_failures += 1

        # DIAG-02: lifetime failure counters; stage2_schema_error_total is a sub-counter.
        self._diagnostics.stage2_failed_total += 1
        if isinstance(err, OllamaSchemaError):
            self._diagnostics.stage2_schema_error_total += 1

        # D-09 cooldown state machine: fire if at/past threshold AND (never fired OR cooldown elapsed).
        if self._stage2_consecutive_failures >= STAGE2_NOTIFY_THRESHOLD and (
            self._stage2_last_notify_ts is None
            or _time.time() - self._stage2_last_notify_ts >= STAGE2_NOTIFY_COOLDOWN_S
        ):
            # D-08: notification body mentions failure count + Stage-1 still working callout.
            persistent_notification.async_create(
                self.hass,
                message=(
                    f"Stage-2 extraction has failed {self._stage2_consecutive_failures} times in a row. "
                    f"Check that the Ollama server is reachable and the configured model is pulled. "
                    f"The integration continues running with Stage-1 results."
                ),
                title="Shop2Parcel Stage-2 Failing",
                notification_id=stage2_failing_notification_id(self.config_entry.entry_id),  # type: ignore[union-attr]
            )
            # Update cooldown timestamp to gate re-fires (D-09).
            self._stage2_last_notify_ts = _time.time()

        # Finding 2: this runs in the background Stage-2 worker, outside any poll's
        # listener dispatch. Push the updated streak to subscribers so ProblemBinarySensor
        # re-evaluates immediately when it crosses STAGE2_NOTIFY_THRESHOLD, instead of
        # lagging by a full poll interval. (Recovery is already surfaced via the success
        # path's async_set_updated_data.) async_update_listeners is a sync callback — safe
        # to call from the worker coroutine.
        self.async_update_listeners()

    def _record_stage2_success(self) -> None:
        """Centralize loud-surface side effects for a Stage-2 successful POST.

        Implements FAIL-05 (counter reset + notification dismiss) per D-03/D-06.

        Called ONLY from the success path at line 710 (after _stage2_posts_this_poll += 1)
        — D-06 defines 'success' as a real 2xx POST to parcelapp.net. Graceful rejections
        (AlreadyAdded, InvalidTracking), cap-skip, and quota-exhausted-skip do NOT call this.

        The async_dismiss is unconditional (D-04): HA no-ops on unknown notification IDs
        (Assumption A1 in 21-RESEARCH.md), so no _stage2_consecutive_failures > 0 guard is needed.
        Note (D-03): stage2_succeeded_total increment is Plan 03's job.
        """
        self._stage2_consecutive_failures = 0
        # DIAG-02: lifetime success counter; incremented only on real 2xx POST (D-06).
        self._diagnostics.stage2_succeeded_total += 1
        # FAIL-05: unconditional dismiss — HA is a no-op when the ID is unknown.
        persistent_notification.async_dismiss(
            self.hass,
            notification_id=stage2_failing_notification_id(self.config_entry.entry_id),  # type: ignore[union-attr]
        )

    @property
    def diagnostics(self) -> PollStats:
        """Public read-only view of in-memory poll diagnostics."""
        return self._diagnostics

    @property
    def stage2_queue_depth(self) -> int:
        """Current Stage-2 queue depth; 0 when stage2 is disabled."""
        if self._stage2_queue is None:
            return 0
        return self._stage2_queue.qsize()

    @property
    def stage2_consecutive_failures(self) -> int:
        """Current run of back-to-back Stage-2 failures; resets to 0 on any success."""
        return self._stage2_consecutive_failures

    @property
    def email_processing_active(self) -> bool:
        """True while a poll is fetching/parsing emails OR the Stage-2 queue still has items to drain."""
        return self._poll_in_progress or self.stage2_queue_depth > 0

    @property
    def pending_posts_depth(self) -> int:
        """Count of quota-deferred merged shipments awaiting the ParcelApp POST step (Phase 23 _pending_posts)."""
        return len(self._pending_posts)

    @property
    def pending_posts_entries(self) -> list[ShipmentData]:
        """Public read-only view of quota-deferred shipments (values of _pending_posts)."""
        return list(self._pending_posts.values())

    # Phase 26: public read-only properties for operational-health entities.
    # Entities must NEVER access private attributes directly (RESEARCH Pitfall 4).

    @property
    def total_forwarded(self) -> int:
        """Lifetime count of genuine 2xx POSTs to ParcelApp; persisted across restarts."""
        return self._total_forwarded

    @property
    def last_forwarded_ts(self) -> int | None:
        """Unix epoch of the most recent successful POST; None before the first forwarding."""
        return self._last_forwarded_ts

    @property
    def used_today(self) -> int:
        """Estimated ParcelApp POSTs made today (UTC day); auto-resets on UTC date rollover.

        Calls _maybe_reset_used_today() on each read so the ParcelApp Quota sensor
        never shows a stale prior-day count even if no forwarding has happened today
        yet. (Phase 26 RESEARCH Pattern 5.)
        """
        self._maybe_reset_used_today()
        return self._used_today

    @property
    def currently_tracked_count(self) -> int:
        """Count of live in-memory shipments from the current session (len(coordinator.data)).

        Resets on HA restart (since coordinator.data rehydrates from persisted_shipments
        on next poll). Use as 'currently active shipments' — not a lifetime total.
        (Phase 26 RESEARCH Open Question 1 resolution / Assumption A4.)
        """
        return len(self.data or {})

    @property
    def quota_is_exhausted(self) -> bool:
        """True when ParcelApp quota is exhausted and the cooldown window has not yet elapsed.

        Public accessor so entities never read the private _quota_exhausted_until attribute.
        (Phase 26 RESEARCH Pitfall 4 / STRIDE T-26-02.)
        """
        return (
            self._quota_exhausted_until is not None
            and int(_time.time()) < self._quota_exhausted_until
        )

    # ------------------------------------------------------------------
    # Finding 3: time-boundary refresh timers
    # ------------------------------------------------------------------

    def enable_operational_timers(self) -> None:
        """Enable the time-boundary refresh timers (finding 3).

        Called once from async_setup_entry after the first refresh. Gated behind a flag
        so unit tests that construct the coordinator bare (and poke _quota_exhausted_until
        directly) never schedule real timers — only a fully set-up entry arms them. Arms
        the quota-expiry timer (in case store hydration loaded a still-future window) and
        the daily UTC-midnight used_today refresh. Pair with _cancel_operational_timers
        registered via entry.async_on_unload.
        """
        self._operational_timers_enabled = True
        self._arm_quota_expiry_timer()
        self._schedule_midnight_refresh()

    def _cancel_operational_timers(self) -> None:
        """Cancel both refresh timers (registered via entry.async_on_unload).

        Idempotent — safe on every teardown path (clean unload, exception, HA shutdown).
        """
        if self._quota_expiry_unsub is not None:
            self._quota_expiry_unsub()
            self._quota_expiry_unsub = None
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None

    def _arm_quota_expiry_timer(self) -> None:
        """(Re)schedule the one-shot timer that refreshes entities when the ParcelApp
        quota window expires (finding 3).

        Called after every _quota_exhausted_until mutation in the poll/drain paths.
        No-op unless operational timers are enabled, so direct attribute assignment in
        tests never leaks a timer. Always cancels any prior quota timer first; schedules
        a new one only when _quota_exhausted_until is still in the future (a past value
        is already reported as not-exhausted, and the poll-time clear persists it).
        """
        if self._quota_expiry_unsub is not None:
            self._quota_expiry_unsub()
            self._quota_expiry_unsub = None
        if not self._operational_timers_enabled:
            return
        until = self._quota_exhausted_until
        if until is None or until <= int(_time.time()):
            return
        self._quota_expiry_unsub = async_track_point_in_time(
            self.hass, self._on_quota_expiry, datetime.fromtimestamp(until, tz=UTC)
        )

    @callback
    def _on_quota_expiry(self, _now: datetime) -> None:
        """Quota window elapsed: clear the stale block, persist, and refresh entities.

        The timer is re-armed on every _quota_exhausted_until change (set or clear), so
        when it fires it always corresponds to the current window — clear unconditionally.
        Clearing in memory makes quota_is_exhausted read False immediately; persisting it
        means the cleared state survives a restart (mirrors the poll-time stale-quota clear).
        """
        self._quota_expiry_unsub = None
        if self._quota_exhausted_until is not None:
            self._quota_exhausted_until = None
            self._persist_state()
        self.async_update_listeners()

    def _schedule_midnight_refresh(self) -> None:
        """(Re)schedule the one-shot timer at the next 00:00 UTC that refreshes used_today
        (finding 3).

        used_today auto-resets on UTC date rollover but only when the property is read;
        with should_poll=False nothing reads it at midnight, so the Quota sensor can show
        the prior day's count until the next poll. No-op unless operational timers are
        enabled. Reschedules itself each midnight.
        """
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        if not self._operational_timers_enabled:
            return
        self._midnight_unsub = async_track_point_in_time(
            self.hass, self._on_midnight, datetime.fromtimestamp(_next_midnight_utc(), tz=UTC)
        )

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        """UTC midnight: force the used_today rollover reset, refresh entities, reschedule."""
        self._midnight_unsub = None
        self._maybe_reset_used_today()
        self.async_update_listeners()
        self._schedule_midnight_refresh()

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
        assert self.config_entry is not None
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
        # Phase 21 SPEC Req #5: reset failure streak on stage2 stop/reload.
        # Placed at the TOP of async_stop_stage2 (before any early-return) so the reset
        # fires unconditionally — even when _stage2_queue is None (CR-01 sentinel path where
        # async_start_stage2 was never called). A naive 'append at end' would miss this path.
        self._stage2_consecutive_failures = 0
        self._stage2_last_notify_ts = None

        # Step 1 (Phase 19): cancel worker task — must precede queue drain (D-01).
        # QUE-05: 5-second bounded wait via asyncio.wait_for.
        # Explicit task.cancel() is required before wait_for: without it, wait_for on a
        # not-yet-cancelled task simply waits for natural completion. An idle worker blocked
        # on queue.get() never completes on its own, causing a 5-second delay on every clean
        # shutdown. Cancelling first allows the idle worker to exit immediately; the 5-second
        # backstop handles workers that are mid-operation when cancelled.
        # CancelledError and TimeoutError are suppressed so unload never raises (QUE-05).
        if self._stage2_worker_task is not None and not self._stage2_worker_task.done():
            self._stage2_worker_task.cancel()
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
                self._stage2_queue.task_done()
            except asyncio.QueueEmpty:
                break
        # CR-02: preserve maxsize so backpressure invariant holds after a reload.
        self._stage2_queue = asyncio.Queue(maxsize=prev_maxsize)
        self._stage2_enqueued_keys.clear()
        if self._store_loaded:
            await self._async_save_store()
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
        raw_msg_id: str | None = None,
        prefetched_result: Any | None = None,
    ) -> bool:
        """Enqueue a Stage-2 job with in-flight dedup and drop-newest backpressure.

        QUE-06: skips silently if normalized_tn already in _stage2_enqueued_keys.
        QUE-03: on QueueFull, logs warning + emits stage2_dropped_backpressure event,
                does NOT write to _submitted_tracking_numbers.
        Uses put_nowait (never await put) per QUE-07.

        The add to _stage2_enqueued_keys happens ONLY after successful put_nowait
        (Anti-Patterns §3) — if put_nowait raises QueueFull, the key is NOT added.

        Phase 27 fix: returns True when the message is considered handled by the queue
        (successful put OR an in-flight-skip — an existing job for the same tracking number
        will complete it), and False only when QueueFull dropped the job. The Gmail caller
        uses the False return to avoid marking the message seen, so a backpressure-dropped
        message is re-fetched and re-enqueued next poll instead of being lost. raw_msg_id /
        prefetched_result are forwarded onto the Stage2Job (see its docstring).
        """
        if normalized_tn in self._stage2_enqueued_keys:
            return True  # silent skip — already in flight (QUE-06); the in-flight job handles it
        job = Stage2Job(
            storage_key=storage_key,
            normalized_tn=normalized_tn,
            shipment=shipment,
            html_body=html_body,
            message_id=message_id,
            meta=meta,
            raw_msg_id=raw_msg_id,
            prefetched_result=prefetched_result,
        )
        assert self._stage2_queue is not None
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
            self._diagnostics.stage2_dropped_backpressure_total += 1  # DIAG-02
            return (
                False  # NO dedup write (QUE-03), NO add to enqueued_keys; caller must not mark seen
            )
        self._stage2_enqueued_keys.add(normalized_tn)  # add AFTER successful put (Anti-Patterns §3)
        self._diagnostics.stage2_enqueued_total += 1  # DIAG-02: only on successful put_nowait
        return True

    async def _async_stage2_worker(self) -> None:
        """Single long-lived background worker draining _stage2_queue serially.

        QUE-02: runs until cancelled via task.cancel() from async_stop_stage2.
        Sole blocking point: await self._stage2_queue.get().

        CancelledError at any await point propagates to the outer try — never
        swallowed. Phase 21 (FAIL-01..05) implemented loud failure surface via
        _record_stage2_failure/_record_stage2_success; worker-outer Exception
        calls _record_stage2_failure and does NOT crash the worker.

        task_done() in finally: ensures queue.join() (if used in tests) never hangs.
        """
        assert self.config_entry is not None
        assert self._stage2_queue is not None
        _LOGGER.debug("Stage-2 worker started for entry %s", self.config_entry.entry_id)
        try:
            while True:
                job: Stage2Job = await self._stage2_queue.get()
                normalized_tn = job.normalized_tn
                try:
                    # D-07 / IMAP parity: drain runs on the base class — ImapCoordinator
                    # inherits this worker and therefore gets drain for free (no imap_coordinator.py
                    # change needed). CancelledError from the drain re-raises below (worker shuts down).
                    await self._async_drain_pending_posts()
                    await self._async_process_stage2_job(job)
                except asyncio.CancelledError:
                    self._stage2_enqueued_keys.discard(normalized_tn)  # prevent permanent key lock
                    self._unmark_seen_for_retry(job)  # Phase 27: re-fetch on next start (not lost)
                    raise  # propagate — shuts down the worker
                except Exception as err:  # noqa: BLE001
                    # FAIL-01/02/04: surface the failure loudly via the centralized helper (D-01..D-04).
                    self._record_stage2_failure(job, err)
                    self._stage2_enqueued_keys.discard(normalized_tn)
                    self._unmark_seen_for_retry(job)  # Phase 27: re-fetch next poll (not lost)
                finally:
                    self._stage2_queue.task_done()
        except asyncio.CancelledError:
            _LOGGER.debug("Stage-2 worker cancelled — exiting cleanly")
            raise

    async def _async_drain_pending_posts(self) -> None:
        """Drain pending posts from prior quota-blocked extraction cycles.

        Runs before each new extraction job to opportunistically flush the backlog
        when quota/cap conditions allow. Respects MAX_STAGE2_POSTS_PER_POLL cap and
        _quota_exhausted_until timestamp.

        Guard order:
          1. Empty _pending_posts → no-op.
          2. debug_mode True → no-op (debug never accumulates _pending_posts; DBG-03).
          3. Quota still exhausted → no-op.
          Then: POSTs each pending item WITHOUT re-invoking the extractor (LD-05 / AC-3).

        IMAP parity (D-07): this method lives on the base class; ImapCoordinator
        inherits _async_stage2_worker and therefore this drain automatically — no
        imap_coordinator.py changes needed.

        Error handling mirrors _async_process_stage2_job:
          - ParcelAppAuthError  → raise ConfigEntryAuthFailed
          - ParcelAppQuotaError → set _quota_exhausted_until and BREAK (items remain)
          - AlreadyAdded / InvalidTracking → treat as success (dedup write + remove)
          - ParcelAppTransientError → continue (item stays for next poll — Pitfall 2)
        """
        if not self._pending_posts:
            return
        assert self.config_entry is not None
        debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)
        if debug_mode:
            return  # debug mode never accumulates _pending_posts; early exit is safe (Pitfall 1)
        now = int(_time.time())
        if self._quota_exhausted_until is not None and now < self._quota_exhausted_until:
            return  # quota still exhausted; drain blocked until the window resets

        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )

        # Iterate a COPY so dict mutation during iteration is safe (RESEARCH constraint).
        for storage_key, merged_shipment in list(self._pending_posts.items()):
            # AC-4 / Pitfall 4: shared counter covers drain + new-extraction POSTs this poll.
            if self._stage2_posts_this_poll >= MAX_STAGE2_POSTS_PER_POLL:
                break  # cap reached; remaining items wait for next poll

            normalized_tn = normalize_tracking_number(merged_shipment.tracking_number)
            carrier_code = normalize_carrier(merged_shipment.carrier_name)

            _LOGGER.debug(
                "Stage-2 drain: POSTing deferred tn=%s carrier=%s to parcelapp",
                normalized_tn,
                merged_shipment.carrier_name,
            )

            # Phase 26 RESEARCH Pitfall 6: posted_2xx flag distinguishes genuine 2xx from
            # AlreadyAdded/InvalidTracking fall-through. _record_forward ONLY fires on 2xx.
            posted_2xx = False
            try:
                await parcel_client.async_add_delivery(
                    tracking_number=merged_shipment.tracking_number,
                    carrier_code=carrier_code,
                    description=merged_shipment.order_name or merged_shipment.tracking_number,
                )
                posted_2xx = True  # genuine 2xx response — gate for _record_forward
            except ParcelAppAuthError as err:
                raise ConfigEntryAuthFailed("parcelapp.net auth error (drain)") from err
            except ParcelAppQuotaError as err:
                # Quota re-exhausted mid-drain; set the block and stop draining.
                # Remaining items stay in _pending_posts for the next quota window.
                self._quota_exhausted_until = (
                    err.reset_at if err.reset_at is not None else _next_midnight_utc()
                )
                self._arm_quota_expiry_timer()  # finding 3: refresh entities when it expires
                _LOGGER.warning(
                    "Stage-2 drain: parcelapp quota hit mid-drain — deferring remaining "
                    "pending items until quota resets"
                )
                break
            except (ParcelAppAlreadyAddedError, ParcelAppInvalidTrackingError):  # fmt: skip
                # Treat as success for dedup purposes — fall through to bookkeeping below.
                # posted_2xx stays False: AlreadyAdded/InvalidTracking do NOT consume quota
                # and do NOT represent forwarding a new shipment (RESEARCH Pitfall 6 / T-26-02).
                _LOGGER.debug(
                    "Stage-2 drain: tn=%s already known to parcelapp (AlreadyAdded/InvalidTracking)",
                    normalized_tn,
                )

            except ParcelAppTransientError as err:
                # Leave item in _pending_posts for retry next poll (Pitfall 2).
                _LOGGER.debug(
                    "Stage-2 drain: transient error for tn=%s — item stays for retry: %s",
                    normalized_tn,
                    str(err)[:100],
                )
                continue

            # Success or AlreadyAdded/InvalidTracking: bookkeeping (Pitfall 3: call
            # _record_stage2_success on drain POSTs too — they are real POSTs).
            self._stage2_posts_this_poll += 1  # Pitfall 4: shared counter
            self._record_stage2_success()
            if posted_2xx:
                self._record_forward()  # Phase 26: forward counter (genuine 2xx only, not AlreadyAdded)
            # Write dedup so next poll does not retry this TN.
            self._submitted_tracking_numbers[normalized_tn] = None
            if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
                self._submitted_tracking_numbers.popitem(last=False)
            # Pitfall 2: remove ONLY after a successful POST (never before).
            del self._pending_posts[storage_key]
            # Pitfall 5: re-snapshot immediately before async_set_updated_data to avoid
            # stale-base race (S2 bug pattern from Phase 18).
            self._pending_shipments = {**(self.data or {}), storage_key: merged_shipment}
            self.async_set_updated_data({**(self.data or {}), storage_key: merged_shipment})
            _LOGGER.debug("Stage-2 drain: posted deferred tn=%s successfully", normalized_tn)

        # Single save at end of drain loop — Pitfall 2 aligned (save covers all mutations above).
        # immediate=True: drained items include genuine forwards, so persist durably (finding 12).
        await self._async_save_store(immediate=True)

    async def _async_process_stage2_job(self, job: Stage2Job) -> None:
        """Process one Stage2Job: extract via Ollama, merge, POST to parcelapp, update state.

        Called by _async_stage2_worker for each drained job.

        Implements D-05 (per-job store save after POST), D-06 (snapshot pattern),
        MRG-01 (extractor called for every job), MRG-02 (merged shipment POSTed),
        MRG-03 (stage2_conflict event emitted when LLM disagrees with Stage-1),
        MRG-05 (per-poll POST cap gate + once-per-poll notification + D-12 counter),
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
        assert self.config_entry is not None
        normalized_tn = job.normalized_tn
        _LOGGER.debug(
            "Stage-2: dequeued job tn=%s extractor=%s",
            normalized_tn,
            "on" if self._extractor is not None else "off",
        )

        # MRG-05: per-poll POST cap gate (D-09). Checked BEFORE the extractor call so
        # cap-skipped jobs never reach Ollama (avoids wasted inference cost during cap-hit polls).
        if self._stage2_posts_this_poll >= MAX_STAGE2_POSTS_PER_POLL:
            if not self._stage2_cap_notified_this_poll:
                # D-10: fire exactly one notification per poll (first cap-hit only).
                self._stage2_cap_notified_this_poll = True
                persistent_notification.async_create(
                    self.hass,
                    message=(
                        f"Shop2Parcel Stage-2 cap hit: {MAX_STAGE2_POSTS_PER_POLL} POST(s) "
                        f"already sent this poll cycle. Remaining items will retry on the "
                        f"next poll cycle."
                    ),
                    title="Shop2Parcel Stage-2 Cap Hit",
                    notification_id=stage2_cap_notification_id(self.config_entry.entry_id),
                )
            self._diagnostics.stage2_cap_skip_total += 1
            _LOGGER.debug(
                "Stage-2 worker: cap hit (%d/%d) — skipping POST for %s; will retry next poll",
                self._stage2_posts_this_poll,
                MAX_STAGE2_POSTS_PER_POLL,
                normalized_tn,
            )
            self._stage2_enqueued_keys.discard(normalized_tn)  # allow re-enqueue next poll
            self._unmark_seen_for_retry(
                job
            )  # Phase 27: re-fetch next poll (cap-deferred, not lost)
            return  # no extractor, no POST, no dedup write

        # MRG-02: default to Stage-1 shipment; replaced by merged result after extraction.
        merged_shipment = job.shipment

        if job.prefetched_result is not None:
            # Phase 27 fix (finding #3): the Gmail Ollama fallback gatekeeper already ran the
            # extractor on this HTML and built job.shipment from the result. Re-extracting here
            # would call Ollama a second time on the same body, doubling GPU load and defeating
            # the per-poll fallback cap. The fallback already counted the attempt and recorded
            # the latency, so just reuse the merged shipment as-is — no extract, no re-merge.
            _LOGGER.debug(
                "Stage-2: reusing fallback-prefetched extraction for %s (no re-extract)",
                normalized_tn,
            )
        elif self._extractor is not None:
            self._diagnostics.stage2_llm_attempts_total += 1
            try:
                stage2_result = await self._extractor.async_extract(job.html_body, job.shipment)
            except (OllamaTransientError, OllamaSchemaError) as err:  # fmt: skip
                # FAIL-01/02/04: surface the Ollama failure loudly via helper (D-01..D-04, D-05 scope).
                # FAIL-03: no POST, no dedup write; next poll retries (D-04 pure-surface: helper does
                # not touch control flow; existing discard+return below remain unchanged).
                self._record_stage2_failure(job, err)
                self._stage2_enqueued_keys.discard(normalized_tn)
                self._unmark_seen_for_retry(job)  # Phase 27: re-fetch next poll (not lost)
                return

            self._diagnostics.record_llm_call(
                stage2_result.latency_ms,
                fence_retry=stage2_result.passes_used == 2,
            )

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
                self._diagnostics.stage2_conflict_total += 1  # DIAG-02

        else:
            _LOGGER.debug(
                "Stage-2: extractor not configured — skipping LLM extraction for %s",
                normalized_tn,
            )

        # Phase 23 LD-02/D-05: Debug-mode dry-run branch — MUST come before any POST,
        # dedup write, _pending_posts write, or store save (Pitfall 1 / DBG-03 / T-23-03-02).
        # Extraction has already run above so dry-run still exercises the real Ollama path.
        debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)
        if debug_mode:
            self._emit_scan_event(
                message_id=job.message_id,
                meta=job.meta,
                outcome="dry_run_suppressed",
                tracking_number=normalized_tn,
            )
            _LOGGER.debug(
                "[Shop2Parcel DEBUG] Stage-2 dry-run: extracted tn=%s, no POST",
                normalized_tn,
            )
            self._stage2_enqueued_keys.discard(normalized_tn)  # LD-01: allow re-enqueue next poll
            return  # no POST, no dedup write, no pending_posts write, no store save

        # Phase 27 Design §3 skip-POST gate: if the post-merge tracking number is None
        # (possible when the fallback path enqueues a job whose second extraction + merge
        # yields nothing), emit stage2_no_data, discard the in-flight key, and return
        # without POSTing, writing dedup, writing _pending_posts, or saving the store.
        if merged_shipment.tracking_number is None:
            self._emit_scan_event(
                message_id=job.message_id,
                meta=job.meta,
                outcome="stage2_no_data",
                tracking_number=None,
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return  # no POST, no dedup write, no _pending_posts write, no store save

        # Phase 23 LD-03/LD-05: Quota guard moved to AFTER extraction+merge so Ollama always
        # runs for every dequeued job. When quota is exhausted, persist the already-merged
        # shipment to _pending_posts so the drain (plan 04) can POST it later without
        # re-invoking Ollama (no wasted GPU on re-runs).
        now = int(_time.time())
        if self._quota_exhausted_until is not None and now < self._quota_exhausted_until:
            self._pending_posts[job.storage_key] = merged_shipment
            await self._async_save_store()
            self._diagnostics.stage2_quota_skipped_total += 1  # DIAG-02: "extracted, POST deferred"
            if not self._stage2_quota_warned_this_poll:
                # AC-8: throttle WARNING to once per poll; subsequent skips log at DEBUG.
                self._stage2_quota_warned_this_poll = True
                _LOGGER.warning(
                    "Stage-2 worker: parcelapp quota exhausted — extracted tn=%s, POST deferred"
                    " to _pending_posts; will POST when quota resets",
                    normalized_tn,
                )
            else:
                _LOGGER.debug(
                    "Stage-2 worker: parcelapp quota exhausted — extracted tn=%s, POST deferred"
                    " (warning already emitted this poll)",
                    normalized_tn,
                )
            self._stage2_enqueued_keys.discard(normalized_tn)  # allow re-enqueue next poll
            return  # no POST, no dedup write; drain handles the pending item

        parcel_client = ParcelAppClient(
            session=async_get_clientsession(self.hass),
            api_key=self.config_entry.data[CONF_API_KEY],
        )

        _LOGGER.debug(
            "Stage-2: POSTing tn=%s carrier=%s to parcelapp",
            normalized_tn,
            merged_shipment.carrier_name,
        )
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
            self._arm_quota_expiry_timer()  # finding 3: refresh entities when it expires
            _LOGGER.warning(
                "parcelapp.net daily quota exhausted (Stage-2 worker); forwarding paused: %s",
                str(err)[:100],
            )
            # Pitfall 2 (RESEARCH): persist merged_shipment so the item is not lost — the
            # drain will POST it once quota resets without re-running Ollama (LD-05).
            self._pending_posts[job.storage_key] = merged_shipment
            await self._async_save_store()
            self._stage2_enqueued_keys.discard(normalized_tn)
            return
        except (ParcelAppAlreadyAddedError, ParcelAppInvalidTrackingError):  # fmt: skip
            _LOGGER.debug(
                "Stage-2: tn=%s already known to parcelapp (AlreadyAdded/InvalidTracking)",
                normalized_tn,
            )
            self._diagnostics.stage2_already_added_total += 1
            self._stage2_posts_this_poll += 1
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
            self._diagnostics.stage2_transient_error_total += 1
            self._stage2_enqueued_keys.discard(normalized_tn)
            self._unmark_seen_for_retry(job)  # Phase 27: re-fetch next poll (transient, not lost)
            return

        # Success path (mirrors gmail_coordinator.py lines 513-526).
        self._stage2_posts_this_poll += 1  # MRG-05 D-12: increment only on successful POST
        self._record_stage2_success()  # FAIL-05: dismiss failing-notification + reset streak on real 2xx POST (D-03/D-06).
        self._record_forward()  # Phase 26: forward counter (genuine 2xx POST only)
        self._submitted_tracking_numbers[normalized_tn] = None
        if len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
            self._submitted_tracking_numbers.popitem(last=False)
        self._stage2_enqueued_keys.discard(normalized_tn)  # Phase 18 WR-01 fix

        # D-06: snapshot pattern — never mutate self.data directly.
        # Guard: self.data is None before the first coordinator refresh; use empty dict as base.
        # MRG-02: persist and surface the merged shipment so sensors reflect merged state.
        self._pending_shipments = {**(self.data or {}), job.storage_key: merged_shipment}
        await self._async_save_store(immediate=True)  # D-05 / finding 12: durable per-job forward
        # Re-snapshot self.data immediately before publish to avoid stale-base race (S2).
        self.async_set_updated_data({**(self.data or {}), job.storage_key: merged_shipment})
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
        # Phase 27 Plan 02: hydrate seen-message-ID cache (additive store key).
        # A store without the key (e.g. a v3 store written before Plan 02) loads
        # cleanly with an empty cache — backward-compatible; no STORAGE_VERSION bump.
        # ASVS V5 / T-27-02-01: non-list values reset to empty with WARNING;
        # non-str items are dropped (mirrors submitted_tracking_numbers guard above).
        raw_seen = stored.get("seen_message_ids", [])
        if not isinstance(raw_seen, list):
            _LOGGER.warning(
                "seen_message_ids in store is not a list (type=%s); "
                "treating as empty — seen-ID cache will repopulate over time.",
                type(raw_seen).__name__,
            )
            raw_seen = []
        self._seen_message_ids = OrderedDict(
            (mid, None) for mid in raw_seen if isinstance(mid, str)
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
                restored[msg_id] = ShipmentData(
                    **{k: entry[k] for k in _SHIPMENT_FIELD_TYPES},
                    custom_attributes=_safe_custom_attributes(entry),
                )
            except TypeError as err:
                _LOGGER.warning(
                    "persisted_shipments entry for %r could not be reconstructed (%s) — skipping",
                    msg_id,
                    err,
                )
        self._restored_shipments = restored
        # Phase 23 D-03 / LD-05: hydrate pending_posts — post-deferred merged shipments
        # that survived an HA restart while ParcelApp quota was exhausted.
        # Uses stored.get("pending_posts", {}) so v3 stores without this key load cleanly
        # (backward-compatible additive key — no STORAGE_VERSION bump needed).
        # Applies the same _SHIPMENT_FIELD_TYPES per-entry validation used for
        # persisted_shipments above (T-23-02-01 / ASVS V5 input validation).
        restored_pending: dict[str, ShipmentData] = {}
        raw_pending = stored.get("pending_posts", {})
        if not isinstance(raw_pending, dict):
            _LOGGER.warning(
                "pending_posts in store is not a dict (type=%s) for entry %s; "
                "treating as empty — pending posts will be re-queued after next LLM extraction.",
                type(raw_pending).__name__,
                self._store.key.removeprefix("shop2parcel."),
            )
            raw_pending = {}
        for storage_key, entry in raw_pending.items():
            if not isinstance(entry, dict) or not all(
                k in entry and isinstance(entry[k], t) for k, t in _SHIPMENT_FIELD_TYPES.items()
            ):
                _LOGGER.warning("pending_posts entry for %r is invalid — skipping", storage_key)
                continue
            try:
                restored_pending[storage_key] = ShipmentData(
                    **{k: entry[k] for k in _SHIPMENT_FIELD_TYPES},
                    custom_attributes=_safe_custom_attributes(entry),
                )
            except TypeError as err:
                _LOGGER.warning(
                    "pending_posts entry for %r could not be reconstructed (%s) — skipping",
                    storage_key,
                    err,
                )
        self._pending_posts = restored_pending
        # Phase 26: hydrate operational-health counters with type-guarded defaults.
        # Additive keys — no STORAGE_VERSION bump (same pattern as pending_posts above).
        # ASVS V5 / T-26-01: non-int values reset to 0/None with WARNING to prevent counter
        # inflation from corrupt or hand-edited store data.
        raw_tf = stored.get("total_forwarded", 0)
        if _valid_nonneg_int(raw_tf):
            self._total_forwarded = raw_tf
        else:
            _LOGGER.warning(
                "total_forwarded in store is not a non-negative int (type=%s); resetting to 0",
                type(raw_tf).__name__,
            )
            self._total_forwarded = 0
        # last_forwarded_ts is optional (None before the first forward). A missing key
        # is normal; a present-but-corrupt value is surfaced with a WARNING, symmetric
        # with the counters above (previously coerced to None silently — finding 4).
        raw_lf = stored.get("last_forwarded_ts")
        if raw_lf is None:
            self._last_forwarded_ts = None
        elif _valid_nonneg_int(raw_lf):
            self._last_forwarded_ts = raw_lf
        else:
            _LOGGER.warning(
                "last_forwarded_ts in store is not a non-negative int (type=%s); resetting to None",
                type(raw_lf).__name__,
            )
            self._last_forwarded_ts = None
        raw_ut = stored.get("used_today", 0)
        if _valid_nonneg_int(raw_ut):
            self._used_today = raw_ut
        else:
            _LOGGER.warning(
                "used_today in store is not a non-negative int (type=%s); resetting to 0",
                type(raw_ut).__name__,
            )
            self._used_today = 0
        self._used_today_date = str(stored.get("used_today_date", ""))
        self._store_loaded = True
        _LOGGER.debug(
            "Restored %d persisted shipments and %d pending posts from store",
            len(self._restored_shipments),
            len(self._pending_posts),
        )

    async def _async_save_store(self, *, immediate: bool = False) -> None:
        """Persist dedup, quota, shipment, and operational-counter state to Store.

        Default (immediate=False): schedule a debounced write via the sync _persist_state()
        — rapid per-message saves within a poll coalesce into one disk write. The existing
        awaited call sites keep working unchanged.

        immediate=True: write synchronously via Store.async_save, bypassing the 5s debounce,
        so the forward counters survive an HA crash within the debounce window (finding 12).
        Forwards are capped at ~20/day, so the extra immediate writes are negligible.
        """
        if immediate:
            await self._async_save_store_now()
        else:
            self._persist_state()

    def _store_snapshot(self) -> dict:
        """Build the full persisted-state dict from current in-memory state.

        asdict() runs here (before any try block at the call site) so a serialization
        failure surfaces immediately (loud-failure convention) rather than producing a
        stale snapshot. Shared by the debounced (_persist_state) and immediate
        (_async_save_store_now) write paths so the two can never drift.
        """
        return {
            "submitted_tracking_numbers": list(self._submitted_tracking_numbers.keys()),
            "quota_exhausted_until": self._quota_exhausted_until,
            "persisted_shipments": {
                msg_id: asdict(shipment) for msg_id, shipment in self._pending_shipments.items()
            },
            # Phase 23 D-03 / LD-05: persist post-deferred merged shipments so they survive restarts.
            "pending_posts": {
                key: asdict(shipment) for key, shipment in self._pending_posts.items()
            },
            # Phase 26: operational-health counters (additive keys — no STORAGE_VERSION bump).
            "total_forwarded": self._total_forwarded,
            "last_forwarded_ts": self._last_forwarded_ts,
            "used_today": self._used_today,
            "used_today_date": self._used_today_date,
            # Phase 27 Plan 02: seen-message-ID cache (additive key — no STORAGE_VERSION bump).
            # Stored as an ordered list of IDs (insertion order preserved by list(keys())).
            "seen_message_ids": list(self._seen_message_ids.keys()),
        }

    def _persist_state(self) -> None:
        """Schedule a debounced persist of current state to Store.

        Uses async_delay_save (W1/P13-WR-06) so rapid per-message saves within
        a single poll are coalesced into one write. async_delay_save is
        synchronous — it schedules the write; it does NOT write immediately.

        The snapshot is built BEFORE the lambda (stable values) — callers MUST assign
        self._pending_shipments before calling this method. The try/except guards against
        AttributeError (uninitialized store/hass) and other scheduling failures; actual
        write errors surface in HA logs when the delayed timer fires.
        """
        snapshot = self._store_snapshot()  # asdict() outside try → loud serialization failure
        try:
            self._store.async_delay_save(lambda: snapshot, delay=5)
            _LOGGER.debug(
                "Scheduled debounced save for %d submitted tracking numbers, "
                "%d persisted shipments, and %d pending posts",
                len(snapshot["submitted_tracking_numbers"]),
                len(snapshot["persisted_shipments"]),
                len(snapshot["pending_posts"]),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to schedule dedup state save — dedup may re-submit on next restart: %s",
                err,
                exc_info=True,
            )

    async def _async_save_store_now(self) -> None:
        """Persist current state immediately (no debounce) so forward counters survive a
        crash within the 5s debounce window (finding 12).

        asdict() runs in _store_snapshot() before the try (loud-failure convention); the
        write itself is guarded so a transient Store failure logs rather than crashing the
        poll (the next debounced/immediate save will retry).
        """
        snapshot = self._store_snapshot()
        try:
            await self._store.async_save(snapshot)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Failed to persist forward state immediately — counters may revert on a "
                "crash before the next save: %s",
                err,
                exc_info=True,
            )

    async def async_cleanup_delivered(self, now: datetime) -> None:
        """Drop delivered shipments from coordinator.data (keeps currently_tracked_count
        accurate and the store from growing unbounded).

        Phase 5 D-08/D-09/D-11: scheduled once daily via async_track_time_interval
        (24h period set in __init__.py). Match parcelapp deliveries to
        coordinator entries by tracking_number; status_code == 0 means
        Completed (parcelapp-api.md). Removal is immediate.

        Phase 26 (finding 10): the explicit entity-registry-removal loop was removed —
        ShipmentSensor and the per-message dynamic-add machinery are gone (sensor.py),
        so no {DOMAIN}_{entry_id}_{msg_id} entities exist for it to delete. coordinator.data
        is still consumed by ShipmentsForwardedSensor.currently_tracked_count, so trimming
        it here remains meaningful.

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

        # Phase 13.1 (R4): persist the post-cleanup state so delivered shipments are removed
        # from the store. Runs only when removed_ids was non-empty (the `if not removed_ids:
        # return` guard above short-circuits otherwise). Gated on debug_mode to honour the
        # DBG-03 contract (zero store writes in debug mode).
        debug_mode = self.config_entry.options.get(CONF_DEBUG_MODE, False)
        if not debug_mode:
            self._pending_shipments = new_data
            await self._async_save_store()
