"""Shop2ParcelHub — shared singleton scaffolding (Phase 29).

Lives in hass.data[DOMAIN]["__shared__"], created at most once per HA
instance behind an asyncio.Lock installed synchronously (before any await)
in async_setup_entry. Reference-counted via attach(coordinator)/
detach(coordinator); torn down via async_shutdown() only when the last
account detaches (refcount reaches 0).

Phase 29 is pure scaffolding (D-03): the worker stub sits idle in
production — no coordinator enqueues jobs to it yet. The per-entry Stage-2
worker (async_start_stage2/async_stop_stage2 in coordinator.py) is left
100% intact (D-01); Phase 32 does the coordinator->hub queue cutover.

SHARED_STORAGE_VERSION is a separate version chain from coordinator.py's
STORAGE_VERSION=3 — the two must never share a key or version number.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval

from .const import (
    HUB_STAGE2_POLL_WINDOW,
    HUB_STAGE2_QUEUE_MAXLEN,
    MAX_STAGE2_POSTS_PER_POLL,
    MAX_SUBMITTED_TRACKING_NUMBERS,
    PARCELAPP_DAILY_LIMIT,
    STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
    EnqueueOutcome,
)
from .coordinator import Shop2ParcelCoordinator, Shop2ParcelStore, Stage2Job, _valid_nonneg_int

_LOGGER = logging.getLogger(__name__)

SHARED_STORAGE_VERSION = 1


def _next_midnight_utc() -> int:
    """Compute epoch seconds for the next 00:00 UTC.

    Hoisted verbatim from coordinator.py (D-05) — per CONTEXT.md D-06, when a
    quota-exhaustion reset_at is None, fall back to the next UTC midnight so
    the backoff aligns with parcelapp's daily reset. The coordinator.py copy
    stays in place until 31-04 removes it, so Waves 1-3 stay green.
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

    Hoisted verbatim from coordinator.py (D-05) — used for the UTC
    date-rollover check in _maybe_reset_used_today. UTC has no DST so the
    rollover is always at exactly 00:00 UTC.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d")


class Shop2ParcelHub:
    """Singleton hub for shared Shop2Parcel state.

    Lifecycle: created in async_setup_entry when hass.data[DOMAIN]["__shared__"]
    is absent; reference-counted via attach/detach; torn down when the last
    attached coordinator detaches (refcount reaches 0). async_unload_entry owns
    deleting the hass.data entry (D-04/D-06) — the hub never touches hass.data
    itself.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Construct the hub. No I/O happens here — see async_setup()."""
        self._hass = hass
        self._refcount: int = 0
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=HUB_STAGE2_QUEUE_MAXLEN)
        self._worker_task: asyncio.Task | None = None
        self._store: Shop2ParcelStore | None = None
        # Phase 32 (WORK-01..04): entry_id -> currently-attached-coordinator
        # registry (D-01, populated/identity-guard-popped in attach/detach);
        # per-account in-flight tracking (cap + purge source, D-05); and the
        # flat global in-flight dedup set (D-05). Populated by enqueue(),
        # released by _release_inflight()/detach()'s purge.
        self._coordinators: dict[str, Shop2ParcelCoordinator] = {}
        self._inflight: dict[str, set[str]] = {}
        self._stage2_enqueued_keys: set[str] = set()
        # Phase 30 (DEDUP-01/DEDUP-03): single shared dedup set, FIFO-capped
        # at MAX_SUBMITTED_TRACKING_NUMBERS. Pure in-memory core in this plan
        # (30-01) — persistence (async_save/async_load) lands in 30-02.
        self._submitted_tracking_numbers: OrderedDict[str, None] = OrderedDict()
        # Phase 31 (QUOTA-01/02/04): shared daily-budget + per-poll-cap state.
        # _used_today is PRIVATE backing — always read via the used_today
        # property so a read triggers the UTC rollover (mirrors the old
        # per-coordinator property). Pure in-memory in this plan (31-01);
        # persistence lands in 31-02, timer callbacks in 31-03.
        self._used_today: int = 0
        self.used_today_date: str = ""
        self.quota_exhausted_until: int | None = None
        self._stage2_posts_this_poll: int = 0
        self._midnight_unsub: CALLBACK_TYPE | None = None
        self._quota_expiry_unsub: CALLBACK_TYPE | None = None
        self._poll_window_unsub: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        """Load/seed the shared store and spawn the hass-scoped worker stub."""
        self._store = Shop2ParcelStore(
            self._hass,
            version=SHARED_STORAGE_VERSION,
            key="shop2parcel.__shared__",
        )
        data = await self._store.async_load()
        if not data:
            await self._store.async_save({"version": SHARED_STORAGE_VERSION})
        elif not isinstance(data.get("version"), int):
            # T-29-01: a tampered/corrupt version value must not crash hub
            # setup — reset to the current version (mirrors coordinator.py's
            # v2->v3 migration corrupt-data guard).
            _LOGGER.warning(
                "shop2parcel.__shared__ store had a corrupt 'version' value "
                "(type=%s); resetting to %d.",
                type(data.get("version")).__name__,
                SHARED_STORAGE_VERSION,
            )
            await self._store.async_save({"version": SHARED_STORAGE_VERSION})
        # else: valid existing version — leave unchanged (R5 idempotency).

        # DEDUP-02: rehydrate the shared dedup set after the version handling
        # above (the store is already assigned by this point).
        await self.async_load()

        # Hub-scoped (NOT entry-scoped): this task must survive any single
        # account being removed, so it is spawned via hass.async_create_background_task
        # rather than the per-entry background-task API (see coordinator.py:1222).
        self._worker_task = self._hass.async_create_background_task(
            self._async_hub_worker(),
            name="shop2parcel_hub_worker",
        )
        self._worker_task.add_done_callback(self._log_hub_worker_crash)

        # Phase 31 (QUOTA-03/04): arm all three hub-owned timers exactly
        # once here. Unlike coordinator.py's per-entry timers, the hub
        # always goes through async_setup() (no bare-construction test
        # path needs a gate) — so these arm unconditionally.
        self._schedule_midnight_refresh()
        # In case a still-future quota_exhausted_until was just loaded from
        # the store above (async_load), re-arm the expiry timer for it.
        self._arm_quota_expiry_timer()
        self._poll_window_unsub = async_track_time_interval(
            self._hass,
            self._on_poll_window_tick,
            HUB_STAGE2_POLL_WINDOW,
            name="shop2parcel_hub_poll_window",
        )

    def attach(self, coordinator: Shop2ParcelCoordinator) -> None:
        """Increment the reference count (called from async_setup_entry).

        Phase 30-03 (DEDUP-01..03): sets ``coordinator._hub = self`` — the single
        wiring point that lets every coordinator reach the shared dedup set.
        Runs at __init__.py:181, before _async_load_store at :182, so ``_hub`` is
        available for migration seeding and every subsequent poll.

        Phase 32 (D-01, WORK-02): also registers ``_coordinators[entry_id] =
        coordinator`` (last-writer-wins) so the shared worker can resolve a
        Stage2Job's entry_id to the CURRENTLY-attached coordinator at dispatch
        time. Guarded against ``coordinator.config_entry is None`` (bare
        coordinators in some tests) — mirrors _debug_mode_active's None
        tolerance (coordinator.py:739-750).
        """
        self._refcount += 1
        coordinator._hub = self
        entry_id = getattr(coordinator.config_entry, "entry_id", None)
        if entry_id is not None:
            self._coordinators[entry_id] = coordinator
        _LOGGER.debug("Hub attach: refcount=%d", self._refcount)

    def detach(self, coordinator: Shop2ParcelCoordinator) -> None:
        """Decrement the reference count (called from async_unload_entry).

        T-29-02: guarded against underflow — a detach with no matching attach
        (mismatch/double-detach) logs a WARNING instead of going negative,
        which would otherwise cause premature/spurious hub shutdown.

        Phase 32 (D-01/D-05, WORK-02/WORK-04): removes this entry_id's
        registry entry ONLY if it still points at THIS coordinator instance
        (identity guard) — protects a fresh reload attach() from being
        evicted by an out-of-order detach() of the departed instance. Also
        purges this entry_id's in-flight tracking numbers from both
        _inflight and the flat global _stage2_enqueued_keys so a removed
        account's dedup keys never linger. Guarded against
        ``coordinator.config_entry is None`` like attach().
        """
        if self._refcount > 0:
            self._refcount -= 1
        else:
            _LOGGER.warning("Hub detach called with refcount already 0 — ignoring")
        _LOGGER.debug("Hub detach: refcount=%d", self._refcount)
        entry_id = getattr(coordinator.config_entry, "entry_id", None)
        if entry_id is not None:
            if self._coordinators.get(entry_id) is coordinator:
                del self._coordinators[entry_id]
            for tn in self._inflight.pop(entry_id, set()):
                self._stage2_enqueued_keys.discard(tn)

    def _trim_submitted_tns(self) -> None:
        """FIFO-trim _submitted_tracking_numbers to MAX_SUBMITTED_TRACKING_NUMBERS.

        Mirrors coordinator.py's ``_trim_submitted_tns`` (IN-01): ``while``
        (not ``if``) so an oversized set — e.g. a migration seed of 1200
        items — converges to the cap in one call rather than shrinking by one
        entry per insert. Shared by both write paths (check_and_mark,
        seed_from_list) so the cap policy cannot drift between them.
        """
        while len(self._submitted_tracking_numbers) > MAX_SUBMITTED_TRACKING_NUMBERS:
            self._submitted_tracking_numbers.popitem(last=False)

    def check_and_mark(self, tn: str) -> bool:
        """Atomically check-and-mark a tracking number in the shared set.

        DEDUP-01: synchronous by design (no ``await`` between check and
        insert) — lock-free; the single-threaded HA event loop serializes
        callers. Returns ``True`` if ``tn`` was already present (caller must
        skip the POST — already submitted by this or another account).
        Returns ``False`` and records ``tn`` if it is new (caller proceeds
        with the POST). Does NOT re-normalize ``tn`` — callers must pass the
        ``validate_carrier_format()``-clean canonical form (D-03).
        """
        if tn in self._submitted_tracking_numbers:
            return True
        self._submitted_tracking_numbers[tn] = None
        self._trim_submitted_tns()
        return False

    def is_submitted(self, tn: str) -> bool:
        """Read-only membership check — never mutates the shared set.

        Used as a skip-gate ahead of a queue-decoupled POST (the terminal
        POST-success write happens via ``check_and_mark`` instead; see
        30-SPEC.md AC-68). Does NOT re-normalize ``tn``.
        """
        return tn in self._submitted_tracking_numbers

    @property
    def submitted_count(self) -> int:
        """Number of tracking numbers currently in the shared dedup set."""
        return len(self._submitted_tracking_numbers)

    def seed_from_list(self, tn_list: list[str]) -> None:
        """Union-merge a per-account tracking-number list into the shared set.

        DEDUP-03 migration seeding: each ``tn`` uses ``setdefault`` so an
        already-present key keeps its original (first-write) position — no
        re-ordering, no duplicate. Non-str items are dropped defensively
        (T-30-02), mirroring the coordinator's ``isinstance(tn, str)`` guard.
        The FIFO cap is enforced once AFTER the full list is merged (not only
        inside check_and_mark) so a migration seed larger than the cap still
        converges to MAX_SUBMITTED_TRACKING_NUMBERS. An empty list is a
        natural no-op — the loop runs zero times and the trim is applied to
        a set already at/under cap.
        """
        for tn in tn_list:
            if isinstance(tn, str):
                self._submitted_tracking_numbers.setdefault(tn, None)
        self._trim_submitted_tns()

    # ------------------------------------------------------------------
    # Phase 32 (WORK-01..04): shared Stage-2 queue enqueue path.
    #
    # enqueue()/_release_inflight() are SYNCHRONOUS (plain `def`, zero
    # `await` in the body) — mirrors check_and_mark's lock-free mutator
    # shape above (Pattern 1, RESEARCH.md). The single-threaded HA event
    # loop serializes callers, so no lock is needed as long as no `await`
    # sits between a check and its mutation.
    # ------------------------------------------------------------------

    def enqueue(self, job: Stage2Job) -> EnqueueOutcome:
        """Synchronously gate and enqueue a Stage2Job onto the shared queue.

        D-02/D-05/D-06: gates apply in this exact order — dedup (global
        in-flight set) -> per-account in-flight cap
        (STAGE2_PER_ACCOUNT_INFLIGHT_CAP) -> global bound
        (HUB_STAGE2_QUEUE_MAXLEN via put_nowait/QueueFull, drop-newest). A
        job is recorded into _inflight/_stage2_enqueued_keys ONLY after a
        successful put_nowait — DROPPED_BACKPRESSURE/SKIPPED_DUP mutate
        neither structure (no phantom in-flight slot on a dropped/dup job).
        normalized_tn is matched/stored verbatim — no re-normalization,
        consistent with the global submitted-TN set (check_and_mark above).
        """
        if job.normalized_tn in self._stage2_enqueued_keys:
            return EnqueueOutcome.SKIPPED_DUP
        if len(self._inflight.get(job.entry_id, ())) >= STAGE2_PER_ACCOUNT_INFLIGHT_CAP:
            return EnqueueOutcome.DROPPED_BACKPRESSURE
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            return EnqueueOutcome.DROPPED_BACKPRESSURE
        self._inflight.setdefault(job.entry_id, set()).add(job.normalized_tn)
        self._stage2_enqueued_keys.add(job.normalized_tn)
        return EnqueueOutcome.ENQUEUED

    def _release_inflight(self, entry_id: str, normalized_tn: str) -> None:
        """Release one in-flight tracking-number slot (D-07).

        Called by the shared hub worker's ``finally`` block on every job
        outcome (success/failure/skip). Removes ``normalized_tn`` from
        ``_inflight[entry_id]`` — deleting the ``entry_id`` key once its set
        empties, to bound memory — and discards it from the flat global
        ``_stage2_enqueued_keys``. Idempotent: releasing an absent
        entry_id/tn combination is a safe no-op.
        """
        keys = self._inflight.get(entry_id)
        if keys is not None:
            keys.discard(normalized_tn)
            if not keys:
                del self._inflight[entry_id]
        self._stage2_enqueued_keys.discard(normalized_tn)

    def inflight_count(self, entry_id: str) -> int:
        """Public accessor: jobs enqueued-but-not-yet-completed for entry_id.

        The single source of truth for the per-account cap check inside
        enqueue(); also the accessor sensors read instead of the private
        ``_inflight`` dict directly.
        """
        return len(self._inflight.get(entry_id, ()))

    # ------------------------------------------------------------------
    # Phase 31 (QUOTA-01/02/04): shared daily-budget + per-poll-cap mutators.
    #
    # All mutators below are SYNCHRONOUS (plain `def`, zero `await` in the
    # body). The single-threaded HA event loop serializes callers — this is
    # the same lock-free discipline as check_and_mark above. Pure in-memory
    # in this plan (31-01); persistence lands in 31-02, timer callbacks that
    # reset these counters land in 31-03.
    # ------------------------------------------------------------------

    def _maybe_reset_used_today(self) -> None:
        """Reset used_today to 0 on UTC date rollover (in-memory only).

        Mirrors coordinator.py's _maybe_reset_used_today minus the persist
        call — the D-07-gated end-of-poll save lands in 31-04/31-05.
        """
        today = _today_utc_str()
        if self.used_today_date != today:
            self._used_today = 0
            self.used_today_date = today

    def try_consume(self) -> bool:
        """Reserve one shared daily-quota slot, first-come-first-served.

        QUOTA-01: synchronous check-and-increment with no await between the
        two — a race between concurrent callers cannot both succeed past the
        limit. Returns True (and reserves the slot) while used_today is under
        PARCELAPP_DAILY_LIMIT; returns False (no mutation) once exhausted.
        """
        self._maybe_reset_used_today()
        if self._used_today >= PARCELAPP_DAILY_LIMIT:
            return False
        self._used_today += 1
        return True

    def refund_consume(self) -> None:
        """Return one previously-reserved slot (e.g. on a transient/5xx failure).

        QUOTA-02: clamps at 0 — mirrors _trim_submitted_tns's clamp
        discipline. A stray or double refund can never drive used_today
        negative.
        """
        self._used_today = max(0, self._used_today - 1)

    def record_quota_exhausted(self, reset_at: int | None) -> None:
        """Record a parcelapp quota-exhaustion cooldown window.

        QUOTA-02 (D-06): max-precedence — an active block is never
        shortened by a subsequent call with an earlier timestamp. When
        ``reset_at`` is None, falls back to the next UTC midnight
        (parcelapp's daily reset boundary). Does not arm any timer here —
        31-03 adds the _arm_quota_expiry_timer() call.
        """
        new_until = reset_at if reset_at is not None else _next_midnight_utc()
        if self.quota_exhausted_until is None:
            self.quota_exhausted_until = new_until
        else:
            self.quota_exhausted_until = max(self.quota_exhausted_until, new_until)
        # QUOTA-03 (D-04): re-arm the single hub expiry timer after every
        # mutation so it always corresponds to the current window.
        self._arm_quota_expiry_timer()

    def seed_quota_from_account(self, quota_exhausted_until: int | None) -> None:
        """Merge one per-account store's quota_exhausted_until into the shared value.

        QUOTA-05 (R5): one-time migration seeding — same max-precedence merge
        as record_quota_exhausted, so a later per-account cooldown is never
        shortened by an earlier one. Deliberately does NOT touch
        self.used_today (or self._used_today) — the migration day starts
        conservatively at 0 rather than summing/copying stale per-account
        counters (R5). Synchronous, idempotent: re-seeding with None is a
        no-op; re-seeding the same timestamp twice yields the same value.

        Re-arms the shared expiry timer after mutating quota_exhausted_until
        (WR-03), mirroring record_quota_exhausted() — otherwise a migrated
        cooldown that outlives whatever was armed at async_setup() time (or
        no timer at all, if async_setup() saw quota_exhausted_until as None)
        never gets proactively cleared.
        """
        if quota_exhausted_until is None:
            return
        if self.quota_exhausted_until is None:
            self.quota_exhausted_until = quota_exhausted_until
        else:
            self.quota_exhausted_until = max(self.quota_exhausted_until, quota_exhausted_until)
        self._arm_quota_expiry_timer()

    def poll_cap_reached(self) -> bool:
        """True once the shared per-poll Stage-2 POST cap has been reached.

        QUOTA-04: the counter is shared across every attached account in the
        current poll window (30-minute cadence armed in 31-03).
        """
        return self._stage2_posts_this_poll >= MAX_STAGE2_POSTS_PER_POLL

    def record_poll_post(self) -> None:
        """Bump the shared per-poll Stage-2 POST counter (consume-on-success).

        QUOTA-04 (D-01/D-02): ephemeral — never persisted; resets on the
        30-minute poll-window tick (31-03) and naturally on HA restart.
        """
        self._stage2_posts_this_poll += 1

    @property
    def used_today(self) -> int:
        """Estimated shared ParcelApp POSTs made today (UTC day).

        Calls _maybe_reset_used_today() on each read so the ParcelApp Quota
        sensor never shows a stale prior-day count even if no forwarding has
        happened today yet (faithful port of the old per-coordinator
        property).
        """
        self._maybe_reset_used_today()
        return self._used_today

    @property
    def quota_is_exhausted(self) -> bool:
        """True while a recorded quota-exhaustion cooldown is still active."""
        return (
            self.quota_exhausted_until is not None and int(time.time()) < self.quota_exhausted_until
        )

    # ------------------------------------------------------------------
    # Phase 31 (QUOTA-03/04): the three hub-owned timers. Mirrors
    # coordinator.py's _arm_quota_expiry_timer/_on_quota_expiry/
    # _schedule_midnight_refresh/_on_midnight (the hoist source), but the
    # hub arms them unconditionally — it always goes through async_setup(),
    # so there is no bare-construction enable-flag gate to mirror. Armed
    # once in async_setup(); cancelled only in async_shutdown() (refcount
    # 0) — never per-account, so they survive single-account removal.
    # ------------------------------------------------------------------

    def _schedule_midnight_refresh(self) -> None:
        """(Re)schedule the one-shot timer at the next 00:00 UTC that resets
        used_today.

        Cancels any existing handle first, then reschedules for the next
        midnight. Self-rescheduling: _on_midnight calls this again after
        firing so the daily reset repeats indefinitely.
        """
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        self._midnight_unsub = async_track_point_in_time(
            self._hass, self._on_midnight, datetime.fromtimestamp(_next_midnight_utc(), tz=UTC)
        )

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        """UTC midnight: force the used_today rollover reset and reschedule."""
        self._midnight_unsub = None
        self._maybe_reset_used_today()
        self._schedule_midnight_refresh()

    def _arm_quota_expiry_timer(self) -> None:
        """(Re)schedule the one-shot timer that clears quota_exhausted_until
        when the cooldown window elapses (D-04).

        Called after every quota_exhausted_until mutation (record_quota_
        exhausted) and once from async_setup() in case a still-future
        window was just loaded from the store. Always cancels any prior
        expiry timer first; schedules a new one only when
        quota_exhausted_until is still in the future — a past value is
        already reported as not-exhausted by the quota_is_exhausted
        property.
        """
        if self._quota_expiry_unsub is not None:
            self._quota_expiry_unsub()
            self._quota_expiry_unsub = None
        until = self.quota_exhausted_until
        if until is None or until <= int(time.time()):
            return
        self._quota_expiry_unsub = async_track_point_in_time(
            self._hass, self._on_quota_expiry, datetime.fromtimestamp(until, tz=UTC)
        )

    @callback
    def _on_quota_expiry(self, _now: datetime) -> None:
        """Quota window elapsed: clear the stale block in memory.

        The timer is re-armed on every quota_exhausted_until change, so
        when it fires it always corresponds to the current window — clear
        unconditionally. Clearing in memory makes quota_is_exhausted read
        False immediately.
        """
        self._quota_expiry_unsub = None
        if self.quota_exhausted_until is not None:
            self.quota_exhausted_until = None

    @callback
    def _on_poll_window_tick(self, _now: datetime) -> None:
        """Reset the shared per-poll Stage-2 POST counter on the fixed
        HUB_STAGE2_POLL_WINDOW tick (QUOTA-04).

        No re-arm needed here — async_track_time_interval self-repeats.
        _stage2_posts_this_poll is never persisted (ephemeral by design).
        """
        self._stage2_posts_this_poll = 0

    async def async_save(self) -> None:
        """Serialize the shared dedup + quota state (plus version) to the shared store.

        DEDUP-02: additive store key — 'submitted_tracking_numbers' lives
        alongside the existing 'version' key with no SHARED_STORAGE_VERSION
        bump. QUOTA-03: three further additive keys — 'used_today' (the
        private backing attribute, NOT the rollover property, so a save
        never has a side effect), 'used_today_date', and
        'quota_exhausted_until'. '_stage2_posts_this_poll' is deliberately
        excluded — it is ephemeral per-poll state (QUOTA-04), never
        persisted. No-op if the store hasn't been created yet (async_setup
        not called, or already torn down).
        """
        if self._store is not None:
            await self._store.async_save(
                {
                    "version": SHARED_STORAGE_VERSION,
                    "submitted_tracking_numbers": list(self._submitted_tracking_numbers.keys()),
                    "used_today": self._used_today,
                    "used_today_date": self.used_today_date,
                    "quota_exhausted_until": self.quota_exhausted_until,
                }
            )

    async def async_load(self) -> None:
        """Rehydrate the shared dedup + quota state from the shared store.

        T-30-04: defensive against a corrupt/hand-edited store payload — a
        non-list 'submitted_tracking_numbers' value logs a WARNING and loads
        as empty rather than crashing hub setup (mirrors coordinator.py's
        equivalent guard). Non-str items are dropped by seed_from_list
        (T-30-02). T-31-04: 'used_today' must be a genuine non-negative int
        (via coordinator.py's _valid_nonneg_int — excludes bool) or it
        resets to 0 with a WARNING; 'quota_exhausted_until' must be a
        genuine int (excludes bool) or it resets to None with a WARNING —
        a corrupt/hand-edited value must never inflate the shared budget on
        load. No-op if the store hasn't been created yet.
        """
        if self._store is None:
            return
        data = await self._store.async_load() or {}
        stored_list = data.get("submitted_tracking_numbers", [])
        if not isinstance(stored_list, list):
            _LOGGER.warning(
                "shop2parcel.__shared__ store's 'submitted_tracking_numbers' "
                "value is not a list (type=%s); treating as empty.",
                type(stored_list).__name__,
            )
            stored_list = []
        self.seed_from_list([tn for tn in stored_list if isinstance(tn, str)])

        raw_used_today = data.get("used_today", 0)
        if _valid_nonneg_int(raw_used_today):
            self._used_today = raw_used_today
        else:
            _LOGGER.warning(
                "shop2parcel.__shared__ store's 'used_today' value is not a "
                "non-negative int (type=%s); resetting to 0.",
                type(raw_used_today).__name__,
            )
            self._used_today = 0
        self.used_today_date = str(data.get("used_today_date", ""))

        raw_quota_exhausted_until = data.get("quota_exhausted_until")
        if raw_quota_exhausted_until is None:
            self.quota_exhausted_until = None
        elif isinstance(raw_quota_exhausted_until, int) and not isinstance(
            raw_quota_exhausted_until, bool
        ):
            self.quota_exhausted_until = raw_quota_exhausted_until
        else:
            _LOGGER.warning(
                "shop2parcel.__shared__ store's 'quota_exhausted_until' value "
                "is not an int (type=%s); resetting to None.",
                type(raw_quota_exhausted_until).__name__,
            )
            self.quota_exhausted_until = None

    async def async_shutdown(self) -> None:
        """Cancel the worker task and flush the store. Does NOT touch hass.data.

        Called only when refcount reaches 0 (async_unload_entry's responsibility
        per D-04/D-06 — the caller performs the hass.data[DOMAIN]["__shared__"] del).
        """
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=5.0)
            except (asyncio.CancelledError, TimeoutError):  # fmt: skip
                pass
        # Phase 31 (QUOTA-03/04): cancel all three hub-owned timers before
        # the final flush below — idempotent (each unsub call is guarded),
        # safe on every teardown path. Timers are hub-owned and cancelled
        # ONLY here (refcount 0), never via entry.async_on_unload — they
        # survive single-account removal.
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        if self._quota_expiry_unsub is not None:
            self._quota_expiry_unsub()
            self._quota_expiry_unsub = None
        if self._poll_window_unsub is not None:
            self._poll_window_unsub()
            self._poll_window_unsub = None
        # DEDUP-02: flush the dedup set (not just the version) on teardown —
        # the Phase 29 shutdown wrote {"version": SHARED_STORAGE_VERSION}
        # only, which would silently erase the dedup set on every unload.
        await self.async_save()

    async def _async_hub_worker(self) -> None:
        """Single long-lived shared Stage-2 worker draining self._queue serially.

        Phase 32 (WORK-01/WORK-02/WORK-04, D-09): replaces the Phase 29
        ``_stub_worker``. For every dequeued job, resolves ``coord =
        self._coordinators.get(job.entry_id)`` (D-01) BEFORE the per-job
        try — the CURRENTLY-attached coordinator for that entry_id, so a
        same-entry_id reload dispatches to the fresh instance, not a
        captured/stale one. A job whose entry_id has no attached coordinator
        (never attached, or departed via detach()'s purge — the
        purge-vs-drain race backstop) is skipped: no drain, no dispatch, no
        error.

        Ports coordinator._async_stage2_worker's try/except crash-isolation
        ladder verbatim in structure, operating on the resolved ``coord``
        instead of ``self``: asyncio.CancelledError propagates after
        releasing state (the only thing that stops this worker — shutdown);
        ConfigEntryAuthFailed is logged (NOT counted as an Ollama failure,
        WR-05); any other Exception is isolated via
        coord._record_stage2_failure so one job's failure never takes down
        Stage-2 for the whole instance. coord._release_inflight(job) — the
        per-account raw_msg_id message-gate release — is a DIFFERENT
        structure from the hub's dedup/cap release and stays per-account
        (D-08); it is called in every except branch as the coordinator's own
        worker did.

        The hub-scoped in-flight release (self._release_inflight, D-07) and
        self._queue.task_done() are ALWAYS run in a single finally — on
        success, skip, and every exception path — so queue.join() accounting
        never drifts and a job's dedup/cap slot is freed exactly once per
        dequeue, regardless of outcome.
        """
        while True:
            job: Stage2Job = await self._queue.get()
            normalized_tn = job.normalized_tn
            coord = self._coordinators.get(job.entry_id)
            try:
                if coord is not None:
                    await coord._async_drain_pending_posts()
                    await coord._async_process_stage2_job(job)
            except asyncio.CancelledError:
                if coord is not None:
                    coord._release_inflight(job)
                raise  # propagate — shuts down the worker
            except ConfigEntryAuthFailed as err:
                _LOGGER.error(
                    "Stage-2 hub worker: parcelapp auth error for %s — check the "
                    "ParcelApp API key (reauth is requested on the next poll): %s",
                    normalized_tn,
                    err,
                )
                if coord is not None:
                    coord._release_inflight(job)  # re-fetch next poll (not lost)
            except Exception as err:  # noqa: BLE001
                if coord is not None:
                    coord._record_stage2_failure(job, err)
                    coord._release_inflight(job)  # re-fetch next poll (not lost)
            finally:
                self._release_inflight(job.entry_id, normalized_tn)
                self._queue.task_done()

    @callback
    def _log_hub_worker_crash(self, task: asyncio.Task) -> None:
        """Retrieve and log a crashed hub worker's exception at completion time.

        Mirrors coordinator.py's _log_stage2_worker_crash (T-29-05): retrieving
        the exception here prevents asyncio's "Task exception was never
        retrieved" warning at GC.
        """
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            _LOGGER.error(
                "Shop2Parcel hub worker crashed: %s",
                err,
                exc_info=err,
            )
