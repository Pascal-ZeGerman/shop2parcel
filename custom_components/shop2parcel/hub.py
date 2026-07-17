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

from homeassistant.core import HomeAssistant, callback

from .const import MAX_STAGE2_POSTS_PER_POLL, MAX_SUBMITTED_TRACKING_NUMBERS, PARCELAPP_DAILY_LIMIT
from .coordinator import Shop2ParcelCoordinator, Shop2ParcelStore

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
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._store: Shop2ParcelStore | None = None
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
        self._midnight_unsub = None
        self._quota_expiry_unsub = None
        self._poll_window_unsub = None

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
            self._stub_worker(),
            name="shop2parcel_hub_worker",
        )
        self._worker_task.add_done_callback(self._log_hub_worker_crash)

    def attach(self, coordinator: Shop2ParcelCoordinator) -> None:
        """Increment the reference count (called from async_setup_entry).

        Phase 30-03 (DEDUP-01..03): sets ``coordinator._hub = self`` — the single
        wiring point that lets every coordinator reach the shared dedup set.
        Runs at __init__.py:181, before _async_load_store at :182, so ``_hub`` is
        available for migration seeding and every subsequent poll.
        """
        self._refcount += 1
        coordinator._hub = self
        _LOGGER.debug("Hub attach: refcount=%d", self._refcount)

    def detach(self, coordinator: Shop2ParcelCoordinator) -> None:
        """Decrement the reference count (called from async_unload_entry).

        T-29-02: guarded against underflow — a detach with no matching attach
        (mismatch/double-detach) logs a WARNING instead of going negative,
        which would otherwise cause premature/spurious hub shutdown.
        """
        if self._refcount > 0:
            self._refcount -= 1
        else:
            _LOGGER.warning("Hub detach called with refcount already 0 — ignoring")
        _LOGGER.debug("Hub detach: refcount=%d", self._refcount)

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

    async def async_save(self) -> None:
        """Serialize the shared dedup set (plus version) to the shared store.

        DEDUP-02: additive store key — 'submitted_tracking_numbers' lives
        alongside the existing 'version' key with no SHARED_STORAGE_VERSION
        bump. No-op if the store hasn't been created yet (async_setup not
        called, or already torn down).
        """
        if self._store is not None:
            await self._store.async_save(
                {
                    "version": SHARED_STORAGE_VERSION,
                    "submitted_tracking_numbers": list(self._submitted_tracking_numbers.keys()),
                }
            )

    async def async_load(self) -> None:
        """Rehydrate the shared dedup set from the shared store.

        T-30-04: defensive against a corrupt/hand-edited store payload — a
        non-list 'submitted_tracking_numbers' value logs a WARNING and loads
        as empty rather than crashing hub setup (mirrors coordinator.py's
        equivalent guard). Non-str items are dropped by seed_from_list
        (T-30-02). No-op if the store hasn't been created yet.
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
        # DEDUP-02: flush the dedup set (not just the version) on teardown —
        # the Phase 29 shutdown wrote {"version": SHARED_STORAGE_VERSION}
        # only, which would silently erase the dedup set on every unload.
        await self.async_save()

    async def _stub_worker(self) -> None:
        """Worker stub — raises NotImplementedError on any job dequeue.

        D-03: this sits idle in production for the whole of Phase 29; the
        real worker (Ollama extraction + per-account job routing) ships in
        Phase 32.
        """
        while True:
            _job = await self._queue.get()
            self._queue.task_done()
            raise NotImplementedError("Hub worker stub: real worker ships in Phase 32")

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
