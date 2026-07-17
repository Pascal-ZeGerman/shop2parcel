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
from dataclasses import asdict, dataclass, field
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.carrier_codes import normalize_carrier
from .api.email_parser import ShipmentData, validate_carrier_format
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
    MAX_STAGE2_FALLBACK_INLINE_SECONDS,
    MAX_STAGE2_POSTS_PER_POLL,
    SEEN_MESSAGE_IDS_MAXLEN,
    STAGE2_MSG_QUARANTINE_THRESHOLD,
    STAGE2_NOTIFY_COOLDOWN_S,
    STAGE2_NOTIFY_THRESHOLD,
    normalize_tracking_number,
    stage2_cap_notification_id,
    stage2_failing_notification_id,
)
from .extractors.ollama_extractor import OllamaExtractor, preprocess_html
from .merge import merge_llm_authoritative_with_grounding

if TYPE_CHECKING:
    # Phase 30-03: hub.py imports coordinator.py, so a module-level runtime import
    # here would create a circular import. TYPE_CHECKING-only import lets self._hub
    # be typed without one.
    from .hub import Shop2ParcelHub

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 3

# IN-07: bound for the fallback-prefetch cache (cap-deferred Stage2Results held
# across polls so the gatekeeper does not re-run Ollama on the same body).
# Cap-skips per poll are bounded by the queue size (≤ 256), so a small FIFO
# bound is ample; entries are popped on reuse.
_FALLBACK_PREFETCH_CACHE_MAXLEN = 100

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
    prefetched_result: Phase 27 fix — a Stage2Result already produced by the Ollama
        fallback gatekeeper (gmail_coordinator). When set, the worker reuses it instead of
        calling the extractor a second time on the same HTML (avoids double extraction).
    raw_msg_id: Phase 27 — the unprefixed Gmail message ID for the in-flight gate. The poll
        adds it to the in-memory _inflight_message_ids set on enqueue so the message is not
        re-fetched (and not re-judged by Ollama) while its Stage-2 work is in flight. The
        worker RELEASES it from that set on any retry-discard exit so a deferred job is
        re-fetched and re-enqueued next poll. None for paths with no seen/in-flight gate (IMAP).
    """

    storage_key: str
    normalized_tn: str
    shipment: ShipmentData
    html_body: str
    message_id: str
    meta: dict
    prefetched_result: Any | None = None
    raw_msg_id: str | None = None


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
    # Phase 28 Plan 03 (R3/D-08): carrier-format rejection counter. In-memory,
    # not persisted — resets to 0 on HA restart (same lifecycle as all other *_total fields).
    carrier_format_rejected_total: int = 0
    last_carrier_format_rejected_value: str | None = None  # CLEANED canonical form (D-06)
    last_carrier_format_rejected_reason: str | None = None
    # Phase 35 Plan 03 (MRG-05): grounding-gate rejection counter. In-memory,
    # not persisted — resets to 0 on HA restart (same lifecycle as
    # carrier_format_rejected_total). Deliberately SEPARATE from that counter
    # (RESEARCH.md Pitfall 3) so CarrierFormatRejectionsSensor's existing
    # semantics (tracking-number format rejections only) stay intact.
    grounding_rejected_total: int = 0
    last_grounding_rejected_value: str | None = None
    last_grounding_rejected_reason: str | None = None

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

    def record_carrier_format_rejection(self, clean_value: str, reason: str) -> None:
        """Increment the carrier-format rejection counter and record the last cleaned value/reason.

        Must only be called from HA-holding callers (coordinator.py) — not from merge.py (D-02).
        Mirrors record_llm_call: in-memory accumulator, non-persisted (D-08).
        """
        self.carrier_format_rejected_total += 1
        self.last_carrier_format_rejected_value = clean_value
        self.last_carrier_format_rejected_reason = reason

    def record_grounding_rejection(self, clean_value: str, reason: str) -> None:
        """Increment the MRG-05 grounding-rejection counter and record the last value/reason.

        Must only be called from HA-holding callers (coordinator.py) — not from merge.py (D-02).
        In-memory only, no STORAGE_VERSION bump — mirrors record_carrier_format_rejection but
        stays on its own counter (RESEARCH.md Pitfall 3): a grounding rejection (fabricated
        order_name/order_summary) must never be conflated with a carrier-format (tracking-number)
        rejection, which CarrierFormatRejectionsSensor's semantics specifically describe.
        """
        self.grounding_rejected_total += 1
        self.last_grounding_rejected_value = clean_value
        self.last_grounding_rejected_reason = reason


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
        # Phase 30-03 (DEDUP-01..03): assigned by Shop2ParcelHub.attach() before
        # _async_load_store and before any poll (__init__.py:181-182). All dedup
        # reads/writes route through this shared hub — see _hub.is_submitted /
        # _hub.check_and_mark call sites below.
        self._hub: Shop2ParcelHub | None = None
        # RETAINED (SPEC out-of-scope): vestigial — no longer read/written for dedup.
        self._submitted_tracking_numbers: OrderedDict[str, None] = OrderedDict()
        # Phase 27 Plan 02: seen-message-ID cache (mirrors _submitted_tracking_numbers).
        # Tracks Gmail message IDs already processed (any outcome) in a prior poll so
        # they are skipped before async_get_message is called.  FIFO-bounded at
        # SEEN_MESSAGE_IDS_MAXLEN (10 000) to cap memory; oldest ID evicted on overflow.
        # Persisted via additive store key "seen_message_ids" — no STORAGE_VERSION bump.
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        # Phase 27 (round-4 fix): in-memory, NON-persisted, FIFO-bounded set of message IDs
        # whose Stage-2 work is in flight this session, or which hit a transient inline
        # failure (parse crash / momentarily-missing body). The poll's seen-ID gate also
        # filters these, so an enqueued message is not re-fetched / re-judged by Ollama while
        # its job drains (fixes the convergence re-fetch + re-extract cost). It is deliberately
        # NOT persisted: a restart re-evaluates these messages, which recovers transient
        # failures and re-enqueues anything the (also-lost) queue had pending. The worker
        # RELEASES an entry on any retry-discard so a deferred job is re-fetched next poll.
        self._inflight_message_ids: OrderedDict[str, None] = OrderedDict()
        # Poison-message quarantine: per-message consecutive Stage-2 failure counts,
        # keyed by raw Gmail message ID when present, else normalized_tn (WR-03 — the
        # Gmail Stage-1 and IMAP enqueue paths pass raw_msg_id=None). In-memory only
        # (session-scoped). When a count reaches STAGE2_MSG_QUARANTINE_THRESHOLD the
        # worker stops releasing that message for re-fetch AND the tracking number is
        # added to _stage2_quarantined_tns, breaking the observed infinite retry loop
        # on ALL enqueue paths. FIFO-bounded so a long run of distinct failing messages
        # cannot grow it without bound.
        self._stage2_msg_failures: OrderedDict[str, int] = OrderedDict()
        # WR-03: session-scoped skip set of quarantined tracking numbers, consulted by
        # _enqueue_stage2. The in-flight-gate quarantine only covers Gmail fallback jobs
        # (the only path that sets raw_msg_id); Gmail Stage-1 and IMAP jobs re-enqueue
        # from every poll's re-parse, so blocking at the enqueue seam is what actually
        # stops their retry loop. In-memory only — a restart clears it (self-healing).
        self._stage2_quarantined_tns: OrderedDict[str, None] = OrderedDict()
        # ollama-fallback-retry-loop: per-message consecutive OllamaSchemaError count for the
        # INLINE Gmail fallback gatekeeper, keyed by raw Gmail message ID. The worker path uses
        # _stage2_msg_failures + _stage2_quarantined_tns (tn-keyed) but the inline gatekeeper
        # fails BEFORE any tracking number exists, so it needs its own msg_id-keyed counter and
        # a terminal action that does not depend on a tn (it marks the message seen). Only
        # OllamaSchemaError (deterministic — same body always fails) is counted here; a
        # OllamaTransientError (network/5xx) is never counted, so a transient outage keeps
        # retrying and never marks a legitimate shipment email seen (finding #594 constraint).
        # In-memory only (session-scoped); a restart clears it and re-evaluates the message.
        # FIFO-bounded so a long run of distinct schema-failing messages cannot grow it unbounded.
        self._stage2_inline_schema_failures: OrderedDict[str, int] = OrderedDict()
        # IN-07: session-scoped cache of fallback-prefetched Stage2Results whose job
        # the worker cap-skipped, keyed by raw Gmail message ID. The gatekeeper pops
        # this cache before calling the extractor, so a cap-deferred fallback job is
        # NOT re-run through Ollama on the re-fetch poll (previously the
        # prefetched_result was simply discarded — doubling inference per capped
        # job). FIFO-bounded at _FALLBACK_PREFETCH_CACHE_MAXLEN; in-memory only.
        self._fallback_prefetch_cache: OrderedDict[str, Any] = OrderedDict()
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
        # Phase 27 finding #450: True once a fallback extraction failure has escalated this
        # poll. Subsequent fallback failures in the same poll record without bumping the
        # shared consecutive-failure streak / firing the notification. Reset each poll.
        self._stage2_fallback_failed_this_poll: bool = False
        # Quick-260703-mac fix: in-memory flag set True at the end of the first successful
        # poll (in GmailCoordinator._async_update_data). Guards the inline Ollama fallback
        # gatekeeper so the bootstrap first refresh never awaits async_extract. Per-instance
        # so parallel entries and reloads each start False. NEVER persisted to the store.
        self._first_refresh_done: bool = False
        # Per-poll monotonic deadline for the inline Stage-1-miss Ollama fallback budget.
        # Computed in _reset_stage2_poll_counters each poll. None until the first poll reset.
        self._stage2_fallback_inline_deadline: float | None = None
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
        # Phase 27 finding #450: reset the per-poll fallback-escalation latch.
        self._stage2_fallback_failed_this_poll = False
        # Quick-260703-mac: reset the per-poll inline-fallback wall-clock deadline.
        # _first_refresh_done is NOT reset here — it persists across polls once set True.
        self._stage2_fallback_inline_deadline = (
            _time.monotonic() + MAX_STAGE2_FALLBACK_INLINE_SECONDS
        )
        if self.config_entry is not None:
            persistent_notification.async_dismiss(
                self.hass,
                notification_id=stage2_cap_notification_id(self.config_entry.entry_id),
            )

    def _mark_message_seen(self, msg_id: str) -> None:
        """Record a Gmail message ID as processed (seen) so it is skipped on future polls.

        Idempotent: re-marking an existing ID is a no-op (the key already exists in the
        OrderedDict; no re-ordering of existing entries occurs).  After adding, the cache
        is trimmed FIFO if it exceeds SEEN_MESSAGE_IDS_MAXLEN — mirrors the shared hub's
        _trim_submitted_tns policy (Shop2ParcelHub, hub.py).

        Phase 27 Plan 02 (seen-ID gate).
        """
        self._seen_message_ids[msg_id] = None
        while len(self._seen_message_ids) > SEEN_MESSAGE_IDS_MAXLEN:
            self._seen_message_ids.popitem(last=False)

    def _mark_inflight(self, msg_id: str) -> None:
        """Record a message ID as in-flight (or transiently skipped) for the current session.

        FIFO-bounded at SEEN_MESSAGE_IDS_MAXLEN. In-flight IDs are filtered by the poll gate
        (like seen IDs) so the message is not re-fetched / re-judged while its Stage-2 job
        drains, but are NEVER persisted — a restart re-evaluates them. Eviction (or restart)
        lets an already-forwarded entry converge to a persisted seen ID via the
        tracking-number dedup-skip, and lets a transiently-failed entry be retried.
        """
        self._inflight_message_ids[msg_id] = None
        while len(self._inflight_message_ids) > SEEN_MESSAGE_IDS_MAXLEN:
            self._inflight_message_ids.popitem(last=False)

    def _release_inflight(self, job: Stage2Job) -> None:
        """Drop a job's message from the in-flight set so the next poll re-fetches it.

        Called from every worker retry-discard exit (per-poll cap, transient Ollama/parcelapp
        error, unexpected worker error, cancellation). Pairs with the existing
        _stage2_enqueued_keys.discard at those sites: the key discard allows re-enqueue and
        this release allows the re-fetch that produces it. No-op for jobs without a raw_msg_id
        (IMAP) or already released. In-memory only — never persists.
        """
        if job.raw_msg_id is not None:
            self._inflight_message_ids.pop(job.raw_msg_id, None)

    @staticmethod
    def _stage2_failure_key(job: Stage2Job) -> str:
        """Quarantine-counter key for a job (WR-03).

        raw_msg_id when present (Gmail fallback jobs — one message, one key), else
        normalized_tn, which exists for EVERY job type (Gmail Stage-1 and IMAP
        enqueues pass raw_msg_id=None).
        """
        return job.raw_msg_id if job.raw_msg_id is not None else job.normalized_tn

    def _register_stage2_msg_failure(self, job: Stage2Job) -> bool:
        """Track consecutive Stage-2 failures per message; return True to quarantine.

        Increments the per-message failure count (keyed via _stage2_failure_key so
        ALL job types are covered — WR-03). Once it reaches
        STAGE2_MSG_QUARANTINE_THRESHOLD:
          - the job's normalized_tn is added to _stage2_quarantined_tns, which
            _enqueue_stage2 consults — stopping the poll→re-enqueue→re-fail loop on
            the Gmail Stage-1 and IMAP paths that have no in-flight gate;
          - the caller must NOT release the message's in-flight entry (no-op for
            raw_msg_id=None jobs), so a Gmail fallback message also stops being
            re-fetched by the poll gate.
        This breaks the observed infinite retry loop where one pathological email
        re-failed every poll cycle for hours.

        Returns False (keep retrying) for messages still under the threshold. The
        counter and skip set are in-memory only, so a restart clears them and a
        transient Ollama outage self-heals instead of permanently poisoning
        legitimate shipment emails.
        """
        key = self._stage2_failure_key(job)
        count = self._stage2_msg_failures.get(key, 0) + 1
        if count >= STAGE2_MSG_QUARANTINE_THRESHOLD:
            self._stage2_msg_failures.pop(key, None)
            self._stage2_quarantined_tns[job.normalized_tn] = None
            while len(self._stage2_quarantined_tns) > SEEN_MESSAGE_IDS_MAXLEN:
                self._stage2_quarantined_tns.popitem(last=False)
            _LOGGER.warning(
                "Stage-2 worker: quarantining %s ('%s' from '%s') after %d "
                "consecutive extraction failures; skipping it until restart",
                key,
                job.meta.get("subject", ""),
                job.meta.get("from", ""),
                count,
            )
            return True
        self._stage2_msg_failures[key] = count
        self._stage2_msg_failures.move_to_end(key)
        while len(self._stage2_msg_failures) > SEEN_MESSAGE_IDS_MAXLEN:
            self._stage2_msg_failures.popitem(last=False)
        return False

    def _register_inline_schema_failure(self, msg_id: str) -> bool:
        """Track consecutive inline-fallback OllamaSchemaError failures per message.

        ollama-fallback-retry-loop: the inline Gmail fallback gatekeeper has no Stage2Job
        and no tracking number at failure time (extraction itself failed), so it cannot use
        the worker path's tn-keyed quarantine (_register_stage2_msg_failure /
        _stage2_quarantined_tns). This helper is the inline-path equivalent: it increments a
        msg_id-keyed counter and returns True once the count reaches
        STAGE2_MSG_QUARANTINE_THRESHOLD, at which point the caller marks the message SEEN
        (terminal) so the poll gate stops re-fetching it — breaking the infinite per-poll
        re-inference loop observed on a deterministically-unparseable email (a USPS Informed
        Delivery digest re-failed 93x over ~15h).

        ONLY OllamaSchemaError is routed here. A deterministic schema failure repeats with
        the same body every poll, so N repeats is strong evidence the model cannot parse it.
        OllamaTransientError (network/5xx) is deliberately NOT counted by the caller, so a
        transient outage keeps retrying and never marks a legitimate shipment email seen
        (findings #1/#594: optimistic seen-marking of transiently-failing messages loses
        deferred jobs).

        The counter is in-memory only (session-scoped): a restart clears it and re-evaluates
        the message, so a genuinely transient blip that happened to surface as a schema error
        self-heals. FIFO-bounded at SEEN_MESSAGE_IDS_MAXLEN. Returns False (keep retrying) for
        messages still under the threshold.
        """
        count = self._stage2_inline_schema_failures.get(msg_id, 0) + 1
        if count >= STAGE2_MSG_QUARANTINE_THRESHOLD:
            self._stage2_inline_schema_failures.pop(msg_id, None)
            return True
        self._stage2_inline_schema_failures[msg_id] = count
        self._stage2_inline_schema_failures.move_to_end(msg_id)
        while len(self._stage2_inline_schema_failures) > SEEN_MESSAGE_IDS_MAXLEN:
            self._stage2_inline_schema_failures.popitem(last=False)
        return False

    def _debug_mode_active(self) -> bool:
        """True when this entry is in debug/dry-run mode.

        CR-02: single helper for the DBG-03 "zero store writes in debug mode"
        contract, so timer-driven persist paths (_maybe_reset_used_today,
        _on_quota_expiry, async_stop_stage2) apply the same gate the poll paths
        already honour. Safe on bare-coordinator tests: returns False when no
        config_entry is attached.
        """
        return self.config_entry is not None and bool(
            self.config_entry.options.get(CONF_DEBUG_MODE, False)
        )

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
            # CR-02: skipped in debug mode (DBG-03 zero-store-writes) — previously this
            # timer-driven persist snapshotted the empty debug-mode _pending_shipments
            # and wiped persisted_shipments from disk at the first UTC midnight.
            if not self._debug_mode_active():
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
        escalate: bool = True,
    ) -> None:
        """Kwargs-based core of _record_stage2_failure (Phase 27 fix for finding #2).

        Called both from the Stage-2 worker (via _record_stage2_failure with a Stage2Job)
        and from the Gmail Ollama fallback gatekeeper (which fails before any job exists).
        Routing fallback failures here means a sustained Ollama outage escalates and notifies
        the same way worker-path failures do — instead of being swallowed at DEBUG level.

        escalate (finding #450): when True, also bump the consecutive-failure streak, run the
        threshold/cooldown notification gate, and dispatch listeners. The Gmail fallback passes
        escalate=False for every failure after the first in a poll, so a backlog of no-match
        emails during a brief Ollama blip records each failure (log + event + lifetime counter)
        WITHOUT inflating the shared streak ~10x per poll and firing a false 'Stage-2 Failing'
        alarm. The worker path and the first fallback failure of each poll still escalate.
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
        # WR-03: sanitize like every other error_msg site — OllamaSchemaError
        # messages can embed raw model output derived from email bodies, and
        # scan events land in recorder-persisted sensor attributes and the
        # diagnostics download (same class of leak as W9/P11-WR-04).
        self._emit_scan_event(
            message_id=message_id,
            meta=meta,
            outcome="stage2_failed",
            tracking_number=normalized_tn,
            extra={"error_type": type(err).__name__, "error_msg": _sanitise_parser_error(err)},
        )

        # DIAG-02: lifetime failure counters; stage2_schema_error_total is a sub-counter.
        # Always recorded (per-failure), independent of escalation.
        self._diagnostics.stage2_failed_total += 1
        if isinstance(err, OllamaSchemaError):
            self._diagnostics.stage2_schema_error_total += 1

        # Finding #450: non-escalating failures (repeat fallback failures within one poll) stop
        # here — no streak bump, no notification, no listener churn.
        if not escalate:
            return

        # FAIL-04: consecutive-failure counter increment + threshold/cooldown notification gate.
        self._stage2_consecutive_failures += 1

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
            # CR-02: honour DBG-03 (zero store writes in debug mode) — the in-memory
            # clear still happens so quota_is_exhausted reads False immediately.
            if not self._debug_mode_active():
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
        try:
            maxlen = max(1, min(256, int(raw)))
        except (TypeError, ValueError):  # fmt: skip
            # IN-05: a corrupt option (non-numeric string, None, list, ...) must not
            # abort entry setup — fall back to the default with a WARNING, mirroring
            # the _valid_nonneg_int discipline used for store hydration (ASVS V5).
            _LOGGER.warning(
                "queue_maxlen option is not a valid integer (type=%s); using default %d",
                type(raw).__name__,
                DEFAULT_QUEUE_MAXLEN,
            )
            maxlen = DEFAULT_QUEUE_MAXLEN
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
        # IN-05: type-guard the whole structure — a non-list option or a dict entry
        # missing 'name' previously raised (TypeError iterating / KeyError on
        # f["name"]) and aborted entry setup; skip malformed entries with a WARNING
        # instead. Non-str names are dropped downstream by
        # OllamaExtractor._validate_fields (api-review IN-05).
        raw_fields = self.config_entry.options.get(CONF_CUSTOM_FIELDS, [])
        if not isinstance(raw_fields, list):
            _LOGGER.warning(
                "custom_fields option is not a list (type=%s); ignoring custom fields",
                type(raw_fields).__name__,
            )
            raw_fields = []
        field_list: list[tuple[str, str | None]] = []
        for f in raw_fields:
            if not isinstance(f, dict) or "name" not in f:
                _LOGGER.warning(
                    "Skipping malformed custom field entry (type=%s, missing 'name')",
                    type(f).__name__,
                )
                continue
            field_list.append((f["name"], f.get("description")))
        # WR-06: inject the HA executor so the extractor's preprocess_html
        # (BeautifulSoup/lxml pass) runs off the event loop.
        self._extractor = OllamaExtractor(
            client=client,
            field_list=field_list,
            async_add_executor_job=self.hass.async_add_executor_job,
        )

        # Phase 19 D-02: spawn worker after queue and extractor are ready (QUE-04).
        # async_create_background_task auto-registers task in entry._background_tasks;
        # HA provides a 10-second hard-cancel fallback; our 5-second explicit cancel in
        # async_stop_stage2 fires first (config_entries.py _async_process_on_unload).
        self._stage2_worker_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_stage2_worker(),
            name="shop2parcel_stage2_worker",
        )
        # IN-06 follow-up: retrieve a crashed worker's exception at completion time.
        # async_stop_stage2 only awaits tasks that are still running — a worker that
        # died BEFORE stop would otherwise leave its exception unretrieved, surfacing
        # only as asyncio's "Task exception was never retrieved" at GC (and silently
        # in production until then).
        self._stage2_worker_task.add_done_callback(self._log_stage2_worker_crash)
        _LOGGER.debug("Stage-2 worker spawned for entry %s", self.config_entry.entry_id)

    @callback
    def _log_stage2_worker_crash(self, task: asyncio.Task) -> None:
        """Retrieve and log a Stage-2 worker exception the moment the task ends.

        Marks the exception as retrieved (task.exception()) so asyncio never emits
        an unretrieved-exception warning, and makes an unexpected worker death loud
        in the HA log instead of silent until unload.
        """
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            _LOGGER.error(
                "Stage-2 worker crashed; Stage-2 extraction is stopped until the "
                "integration is reloaded: %s",
                err,
                exc_info=err,
            )

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
            try:
                await asyncio.wait_for(self._stage2_worker_task, timeout=5.0)
            except (asyncio.CancelledError, TimeoutError):  # fmt: skip
                pass
            except Exception as err:  # noqa: BLE001
                # IN-06: a worker that died with a non-CancelledError exception
                # re-raises here when awaited; the QUE-05 contract says on-unload
                # never raises, so log loudly and continue the teardown.
                _LOGGER.error(
                    "Stage-2 worker raised during shutdown (suppressed to honour "
                    "QUE-05 'unload never raises'): %s",
                    err,
                    exc_info=True,
                )
        self._stage2_worker_task = None
        self._extractor = None  # release OllamaClient / aiohttp session reference

        # IN-03: clear the enabled flag FIRST so a poll racing this teardown routes
        # away from the enqueue seam, and so diagnostics never report Stage-2 as
        # enabled on a stopped coordinator. A reload constructs a fresh coordinator
        # and async_setup_entry re-derives the flag from options.
        self._diagnostics.stage2_enabled = False

        # Step 2 (Phase 18 CR-02, reworked per IN-03): drain the queue, then null it.
        # Recreating a live workerless queue here let a poll that raced teardown keep
        # enqueueing jobs nothing would ever drain, and email_processing_active
        # (queue depth > 0) read True indefinitely. The None sentinel is handled by
        # stage2_queue_depth and _enqueue_stage2; the old "preserve maxsize" recreate
        # is obsolete — a reload rebuilds the queue in async_start_stage2 from options.
        if self._stage2_queue is None:
            return
        while not self._stage2_queue.empty():
            try:
                self._stage2_queue.get_nowait()
                self._stage2_queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._stage2_queue = None
        self._stage2_enqueued_keys.clear()
        # CR-02: gate on debug mode too (DBG-03 zero-store-writes) — this save fires
        # on every unload path, including a failed-first-refresh teardown.
        if self._store_loaded and not self._debug_mode_active():
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
        prefetched_result: Any | None = None,
        raw_msg_id: str | None = None,
    ) -> bool:
        """Enqueue a Stage-2 job with in-flight dedup and drop-newest backpressure.

        QUE-06: skips silently if normalized_tn already in _stage2_enqueued_keys.
        QUE-03: on QueueFull, logs warning + emits stage2_dropped_backpressure event,
                does NOT write to the shared hub's dedup set.
        Uses put_nowait (never await put) per QUE-07.

        The add to _stage2_enqueued_keys happens ONLY after successful put_nowait
        (Anti-Patterns §3) — if put_nowait raises QueueFull, the key is NOT added.

        Phase 27 fix: returns True ONLY when THIS call created a new Stage-2 job for this
        tracking number (a successful put_nowait). Returns False for both QueueFull (job
        dropped) AND the in-flight-skip case (the tracking number is already queued — possibly
        from a DIFFERENT message). The fallback caller uses the True return to decide whether
        to mark its message in-flight: it must only do so when it actually owns a job, so a
        defer's _release_inflight (keyed by that job's raw_msg_id) frees the right message
        (finding #674). A False return leaves the message un-pinned, so it converges via the
        tracking-number dedup-skip / re-fetch instead. prefetched_result and raw_msg_id are
        forwarded onto the Stage2Job (see its docstring).
        """
        if normalized_tn in self._stage2_quarantined_tns:
            # WR-03: poison quarantine — extraction for this tracking number failed
            # STAGE2_MSG_QUARANTINE_THRESHOLD times in a row this session. Skip the
            # re-enqueue: the Gmail Stage-1 and IMAP paths re-parse and re-enqueue on
            # every poll, so without this seam the retry loop the quarantine was built
            # to stop stayed live on those paths. Session-scoped; a restart re-tries.
            return False
        if normalized_tn in self._stage2_enqueued_keys:
            # In-flight skip (QUE-06): a job for this tracking number already exists (this or
            # another message). Return False — THIS call did not create a job, so the caller
            # must not pin its message in-flight under a job it does not own (finding #674).
            return False
        if self._stage2_queue is None:
            # IN-03: stop/teardown race — async_stop_stage2 nulled the queue while a
            # poll was still iterating. Drop this enqueue; the message stays
            # re-fetchable and re-enqueues after the next (re)start.
            _LOGGER.debug("Stage-2 queue is stopped — dropping enqueue for %s", normalized_tn)
            return False
        job = Stage2Job(
            storage_key=storage_key,
            normalized_tn=normalized_tn,
            shipment=shipment,
            html_body=html_body,
            message_id=message_id,
            meta=meta,
            prefetched_result=prefetched_result,
            raw_msg_id=raw_msg_id,
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
        if self.config_entry is None or self._stage2_queue is None:
            # IN-06: startup invariant violated (worker spawned on a torn-down
            # coordinator). The former bare asserts crashed the background task and
            # the AssertionError re-raised at unload await time, violating QUE-05
            # ("on-unload never raises"); exit cleanly instead.
            _LOGGER.error("Stage-2 worker started without config entry or queue — exiting")
            return
        # IN-03/IN-06: bind a local reference — async_stop_stage2 nulls
        # self._stage2_queue during teardown, and the cancelled worker may still
        # execute its finally-block after that point.
        queue = self._stage2_queue
        _LOGGER.debug("Stage-2 worker started for entry %s", self.config_entry.entry_id)
        try:
            while True:
                job: Stage2Job = await queue.get()
                normalized_tn = job.normalized_tn
                try:
                    # D-07 / IMAP parity: drain runs on the base class — ImapCoordinator
                    # inherits this worker and therefore gets drain for free (no imap_coordinator.py
                    # change needed). CancelledError from the drain re-raises below (worker shuts down).
                    await self._async_drain_pending_posts()
                    await self._async_process_stage2_job(job)
                except asyncio.CancelledError:
                    self._stage2_enqueued_keys.discard(normalized_tn)  # prevent permanent key lock
                    self._release_inflight(job)  # re-fetch on next start (not lost)
                    raise  # propagate — shuts down the worker
                except ConfigEntryAuthFailed as err:
                    # WR-05: parcelapp auth failure — NOT an Ollama failure. The D-05
                    # contract says _record_stage2_failure tracks Ollama failures only;
                    # routing auth errors through the generic handler inflated the
                    # streak and fired the misleading "check Ollama" notification for
                    # a parcelapp API-key problem. Log the real cause, discard the key
                    # + release the message so the job re-runs after reauth; the next
                    # inline poll raises ConfigEntryAuthFailed from poll context and
                    # triggers HA's reauth flow (T-19-08).
                    _LOGGER.error(
                        "Stage-2 worker: parcelapp auth error for %s — check the "
                        "ParcelApp API key (reauth is requested on the next poll): %s",
                        normalized_tn,
                        err,
                    )
                    self._stage2_enqueued_keys.discard(normalized_tn)
                    self._release_inflight(job)  # re-fetch next poll (not lost)
                except Exception as err:  # noqa: BLE001
                    # FAIL-01/02/04: surface the failure loudly via the centralized helper (D-01..D-04).
                    self._record_stage2_failure(job, err)
                    self._stage2_enqueued_keys.discard(normalized_tn)
                    self._release_inflight(job)  # re-fetch next poll (not lost)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            _LOGGER.debug("Stage-2 worker cancelled — exiting cleanly")
            raise

    async def _async_drain_pending_posts(self) -> None:
        """Drain pending posts from prior quota-blocked extraction cycles.

        Runs before each new extraction job AND at the top of every poll cycle
        (WR-04 — both subclasses call it from _async_update_data_inner, so the
        "drained on next quota-free poll" contract holds even when no new shipment
        email ever arrives) to opportunistically flush the backlog when quota/cap
        conditions allow. Respects MAX_STAGE2_POSTS_PER_POLL cap and
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
        assert self._hub is not None  # attach() runs before any poll (__init__.py:181)
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

            # Phase 28 Plan 03 (D-04): defensive carrier-format re-gate before each drain POST.
            # A pending item may have been queued before the strict gate was deployed, or may
            # carry a malformed value that slipped through an earlier validation gap.
            # On reject: discard the item (it will never pass — no retry benefit) and continue.
            # On pass: use the CLEAN canonical form for both dedup key AND the POST body (D-03).
            drain_tn_raw = merged_shipment.tracking_number or ""
            drain_clean, drain_ok, drain_reject_reason = validate_carrier_format(drain_tn_raw)
            if not drain_ok:
                _LOGGER.debug(
                    "Stage-2 drain: carrier-format gate rejected pending tn='%s' (reason=%s)"
                    " — removing from _pending_posts without POST",
                    drain_clean,
                    drain_reject_reason,
                )
                self._diagnostics.record_carrier_format_rejection(
                    drain_clean, drain_reject_reason or "no_carrier_match"
                )
                del self._pending_posts[storage_key]
                continue

            # Use the gate-clean form as the canonical tracking number for dedup AND POST (D-03).
            # Replace the shipment's tracking_number with the clean form so the POST body
            # and the dedup write both use the identical separator-free canonical string.
            merged_shipment = dc_replace(merged_shipment, tracking_number=drain_clean)

            normalized_tn = drain_clean  # gate-clean == dedup-clean (D-03, no normalize_tracking_number divergence)
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
                    description=merged_shipment.order_summary
                    or merged_shipment.order_name
                    or merged_shipment.tracking_number,
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
            if posted_2xx:
                # WR-08/D-12: only a genuine 2xx POST consumes a cap slot — AlreadyAdded/
                # InvalidTracking consumed no parcelapp quota (Pitfall 4: shared counter).
                self._stage2_posts_this_poll += 1
                self._record_forward()  # Phase 26: forward counter (genuine 2xx only, not AlreadyAdded)
            self._record_stage2_success()
            # Write dedup so next poll does not retry this TN (DEDUP-01: shared hub).
            self._hub.check_and_mark(normalized_tn)
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
        assert self._hub is not None  # attach() runs before any poll (__init__.py:181)
        normalized_tn = job.normalized_tn
        _LOGGER.debug(
            "Stage-2: dequeued job tn=%s extractor=%s",
            normalized_tn,
            "on" if self._extractor is not None else "off",
        )

        # Phase 27 (round-4 fix, finding #501): idempotency guard. If this tracking number was
        # already forwarded — e.g. the _async_drain_pending_posts() call that runs immediately
        # before this method just POSTed a quota-deferred copy of it, or a sibling job handled
        # it — do NOT POST again. A second POST double-consumes the parcelapp 20/day quota and
        # creates a duplicate delivery. Discard the in-flight key and return; leave the
        # in-flight message entry so it converges to a persisted seen ID normally.
        # DEDUP-01: read-only — this is a skip-gate, not a write (see design note in
        # 30-03-PLAN.md); the terminal write happens at the check_and_mark call sites below.
        if self._hub.is_submitted(normalized_tn):
            _LOGGER.debug(
                "Stage-2: tn=%s already forwarded — skipping duplicate POST", normalized_tn
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return

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
            # IN-07: preserve the fallback's already-computed extraction across the
            # cap-defer — discarding it made the gatekeeper run a SECOND Ollama pass
            # on the same body next poll. The gatekeeper pops this cache before
            # extracting (gmail_coordinator fallback path).
            if job.prefetched_result is not None and job.raw_msg_id is not None:
                self._fallback_prefetch_cache[job.raw_msg_id] = job.prefetched_result
                while len(self._fallback_prefetch_cache) > _FALLBACK_PREFETCH_CACHE_MAXLEN:
                    self._fallback_prefetch_cache.popitem(last=False)
            self._release_inflight(job)  # re-fetch next poll (cap-deferred, not lost)
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
                if self._register_stage2_msg_failure(job):
                    # Poison-message quarantine: leave the message in the in-flight
                    # skip set (do NOT release) so the poll gate stops re-fetching it,
                    # breaking the infinite retry loop. Session-scoped; restart re-tries.
                    return
                self._release_inflight(job)  # re-fetch next poll (not lost)
                return

            # WR-03: a successful extraction clears this message's failure streak so
            # a recovered message doesn't carry a stale near-threshold count into the
            # next transient blip.
            self._stage2_msg_failures.pop(self._stage2_failure_key(job), None)

            self._diagnostics.record_llm_call(
                stage2_result.latency_ms,
                fence_retry=stage2_result.passes_used == 2,
            )

            # Phase 35 Plan 03 (MRG-05 Pitfall 1): recompute body-only prose from the raw
            # HTML for the grounding gate's source_text. Never job.html_body raw and never
            # any enriched (Subject/From) string — body-only prose is the ONLY grounding
            # evidence (SC-2).
            prose, _links = preprocess_html(job.html_body)

            # MRG-02/MRG-05: merge Stage-2 result into Stage-1 shipment, gating
            # order_name/order_summary through the MRG-05 grounding check.
            merged_shipment, conflicts, gate_rejections, grounding_rejections = (
                merge_llm_authoritative_with_grounding(job.shipment, stage2_result, prose)
            )

            # Phase 28 Plan 03 (R3/D-06/D-07): record carrier-format rejections that
            # occurred on the MRG-04 promotion path inside merge.py.  The counter
            # increment stays here (HA-holding caller) per the D-02 HA-free boundary.
            for rej in gate_rejections:
                self._diagnostics.record_carrier_format_rejection(rej["clean"], rej["reason"])
                _LOGGER.debug(
                    "Stage-2 worker: carrier-format gate rejected promotion of '%s' (reason=%s)",
                    rej["clean"],
                    rej["reason"],
                )

            # Phase 35 Plan 03 (MRG-05, SC-1): record grounding rejections on a
            # DEDICATED counter, separate from carrier-format (RESEARCH.md Pitfall 3).
            # DEBUG-only logging — no value leakage at INFO/WARNING (Phase 28 D-07 convention).
            for rej in grounding_rejections:
                self._diagnostics.record_grounding_rejection(rej["clean"], rej["reason"])
                _LOGGER.debug(
                    "Stage-2 worker: grounding gate rejected promotion of field '%s' "
                    "value '%s' (reason=%s)",
                    rej["field"],
                    rej["clean"],
                    rej["reason"],
                )

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
            self._release_inflight(job)  # re-fetch next poll (dry-run defer)
            return  # no POST, no dedup write, no pending_posts write, no store save

        # Defensive skip-POST guard (finding #1327, comment corrected per IN-02): under
        # current code merged_shipment.tracking_number is never None —
        # ShipmentData.tracking_number is typed non-Optional str, Stage-1 hits and
        # prefetched fallback jobs carry a validated non-None number, and
        # merge_llm_authoritative contains no assert: its promotion path keeps the
        # original Stage-1 value whenever Stage-2 declines (or MRG-04 discards), so it
        # never downgrades the field to None. This guard is therefore unreachable under
        # the current types; it remains as a cheap belt-and-braces check so a future
        # merge/fallback/type change can never POST a None tracking number to parcelapp.
        # It does NOT release the in-flight ID: a genuine no-data result is terminal
        # (converges to seen), not a retry.
        if merged_shipment.tracking_number is None:
            self._emit_scan_event(
                message_id=job.message_id,
                meta=job.meta,
                outcome="stage2_no_data",
                tracking_number=None,
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return  # no POST, no dedup write, no _pending_posts write, no store save

        # WR-02: Defensive carrier-format re-gate before the worker POST.
        # Mirrors the drain re-gate (coordinator.py ~L1216). A carrier-invalid value is
        # terminal (it can never pass); use the same terminal-convergence handling as the
        # no-data branch above — discard the key and return without _release_inflight and
        # without writing _pending_posts. The gate also produces the canonical clean form
        # (D-03) used for the quota-defer persistence, the POST body, and the dedup write
        # on the happy path.
        wk_clean, wk_ok, wk_reason = validate_carrier_format(merged_shipment.tracking_number)
        if not wk_ok:
            _LOGGER.debug(
                "Stage-2 worker: carrier-format gate rejected tn='%s' (reason=%s)"
                " — discarding job without POST (terminal)",
                wk_clean,
                wk_reason,
            )
            self._diagnostics.record_carrier_format_rejection(
                wk_clean, wk_reason or "no_carrier_match"
            )
            self._stage2_enqueued_keys.discard(normalized_tn)
            return  # terminal — no POST, no _pending_posts write, no _release_inflight

        # Rebind merged_shipment to the gate-clean canonical form (D-03) so the quota-defer
        # persistence, the POST body, and the dedup write all use the identical separator-free
        # canonical string.
        merged_shipment = dc_replace(merged_shipment, tracking_number=wk_clean)  # type: ignore[arg-type]

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
                description=merged_shipment.order_summary
                or merged_shipment.order_name
                or merged_shipment.tracking_number,
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
            # WR-08: do NOT increment _stage2_posts_this_poll here — no POST succeeded
            # and no parcelapp quota was consumed, so counting these ate
            # MAX_STAGE2_POSTS_PER_POLL cap slots for non-POST outcomes (the D-12
            # contract is "increment only on successful POST").
            # Write dedup so next poll does not retry; discard in-flight key (DEDUP-01: shared hub).
            self._hub.check_and_mark(normalized_tn)
            self._stage2_enqueued_keys.discard(normalized_tn)
            # WR-08: mirror the success path — persist AND publish. Persisting without
            # async_set_updated_data left store and live data divergent (the entry was
            # invisible to sensors/cleanup), and the next poll's snapshot then dropped
            # it from the store again. Re-snapshot immediately before publish (S2 race).
            self._pending_shipments = {**(self.data or {}), job.storage_key: merged_shipment}
            await self._async_save_store()
            self.async_set_updated_data({**(self.data or {}), job.storage_key: merged_shipment})
            return
        except ParcelAppTransientError as err:
            _LOGGER.warning(
                "Stage-2 worker: parcelapp transient error for %s: %s",
                normalized_tn,
                str(err)[:100],
            )
            self._diagnostics.stage2_transient_error_total += 1
            self._stage2_enqueued_keys.discard(normalized_tn)
            self._release_inflight(job)  # re-fetch next poll (transient, not lost)
            return

        # Success path (mirrors gmail_coordinator.py lines 513-526).
        self._stage2_posts_this_poll += 1  # MRG-05 D-12: increment only on successful POST
        self._record_stage2_success()  # FAIL-05: dismiss failing-notification + reset streak on real 2xx POST (D-03/D-06).
        self._record_forward()  # Phase 26: forward counter (genuine 2xx POST only)
        self._hub.check_and_mark(normalized_tn)  # DEDUP-01: shared dedup-write
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
        leaves this account's per-entry dedup list un-migrated into the shared hub,
        causing every previously submitted tracking number to be re-POSTed on startup.
        It also leaves _restored_shipments empty, causing all previously persisted
        sensors to disappear until their email is re-scanned.

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
        assert self._hub is not None  # attach() runs before _async_load_store (__init__.py:181-182)
        stored_list = stored.get("submitted_tracking_numbers", [])
        if not isinstance(stored_list, list):
            _LOGGER.warning(
                "submitted_tracking_numbers in store is not a list (type=%s); "
                "treating as empty — dedup will repopulate from parcelapp 'already added' 400s.",
                type(stored_list).__name__,
            )
            stored_list = []
        # WR-01: re-normalize stored keys through the (now separator-stripping)
        # canonical form so entries written by older versions under the
        # strip().upper() scheme stay effective as dedup keys.
        # DEDUP-03: one-time migration — union-merge this account's per-entry list into
        # the shared hub set (idempotent: seed_from_list uses setdefault). The actual
        # persistence of this migration (hub save, then per-entry save dropping the
        # key) is deferred to the end of this method — see the comment near
        # self._store_loaded = True below for why.
        migrated_tns = [normalize_tracking_number(tn) for tn in stored_list if isinstance(tn, str)]
        self._hub.seed_from_list(migrated_tns)
        qe = stored.get("quota_exhausted_until")
        self._quota_exhausted_until = qe if isinstance(qe, int) else None
        _LOGGER.debug(
            "Migrated %d submitted tracking numbers from per-entry store into the shared hub",
            len(migrated_tns),
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
                    order_summary=entry.get("order_summary") or None,
                )
            except TypeError as err:
                _LOGGER.warning(
                    "persisted_shipments entry for %r could not be reconstructed (%s) — skipping",
                    msg_id,
                    err,
                )
        self._restored_shipments = restored
        # CR-02: seed _pending_shipments from the restored shipments so any store save
        # that fires before the first successful non-debug poll (async_stop_stage2 on a
        # failed first refresh, _maybe_reset_used_today rollover, _on_quota_expiry) never
        # snapshots an empty dict and wipes persisted_shipments from disk.
        self._pending_shipments = dict(restored)
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
                    order_summary=entry.get("order_summary") or None,
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
        # DEDUP-03 / Prohibition 2: persist the migration only now — AFTER every field
        # above (_restored_shipments/_pending_shipments/_pending_posts/counters) has
        # been hydrated from `stored`. _store_snapshot() reads those live attributes,
        # so saving any earlier (e.g. right after seed_from_list above) would snapshot
        # them at their __init__ defaults ({}/{}/0/None) and silently wipe the real
        # per-entry store contents — a Rule 1 correctness fix over the plan's literal
        # placement. Ordering still satisfies Prohibition 2: the shared hub is saved
        # durably FIRST, then the per-entry store is saved (which now omits the
        # 'submitted_tracking_numbers' key unconditionally — see _store_snapshot). A
        # crash between the two awaits leaves the per-entry key intact; seed_from_list's
        # setdefault makes re-seeding on the next restart idempotent (no duplicate, no
        # loss).
        if migrated_tns:
            await self._hub.async_save()
            await self._async_save_store(immediate=True)

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

        DEDUP-03: 'submitted_tracking_numbers' is deliberately ABSENT — that dedup
        state now lives only in the shared hub's shop2parcel.__shared__ store
        (Shop2ParcelHub.async_save). Every per-entry save after this plan omits the
        key unconditionally, which is what makes the one-time migration delete
        permanent (see _async_load_store).
        """
        return {
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
                "Scheduled debounced save for %d persisted shipments and %d pending posts "
                "(dedup state now lives in the shared hub store)",
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
