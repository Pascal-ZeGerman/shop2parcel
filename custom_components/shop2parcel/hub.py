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
from collections import OrderedDict

from homeassistant.core import HomeAssistant, callback

from .const import MAX_SUBMITTED_TRACKING_NUMBERS
from .coordinator import Shop2ParcelCoordinator, Shop2ParcelStore

_LOGGER = logging.getLogger(__name__)

SHARED_STORAGE_VERSION = 1


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
        """Increment the reference count (called from async_setup_entry)."""
        self._refcount += 1
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
