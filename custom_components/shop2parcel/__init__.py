"""The Shop2Parcel integration entry point.

Responsibilities:
- Instantiates GmailCoordinator or ImapCoordinator (based on CONF_CONNECTION_TYPE) and hydrates deduplication state from
  persistent Store BEFORE the first refresh (RESEARCH.md Pitfall 1: Store must
  be loaded before async_config_entry_first_refresh() to avoid re-forwarding
  previously processed shipments).
- Schedules the once-daily delivered-shipment cleanup task via
  async_track_time_interval and registers the cancel callback with
  entry.async_on_unload so the timer is stopped on all teardown paths.
- Stores the coordinator in hass.data[DOMAIN][entry.entry_id] as a dict keyed
  by "coordinator" so sensor.py and binary_sensor.py can retrieve it.
- Forwards platform setup to PLATFORMS ("sensor", "binary_sensor").
  The button platform was removed in Phase 10 (D-04): the Reset Email Cache
  button is no longer needed because dedup now uses persisted tracking numbers
  rather than a message-ID cache that required manual clearing.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .binary_sensor import OPERATIONAL_BINARY_SENSOR_UID_SUFFIXES
from .const import DOMAIN
from .diagnostic_sensor import DIAGNOSTIC_SENSOR_UID_SUFFIXES
from .sensor import OPERATIONAL_SENSOR_UID_SUFFIXES

_LOGGER = logging.getLogger(__name__)

# Phase 5 (CONTEXT.md D-09): platforms now populated.
# Phase 7 (D-13): diagnostic sensors are co-registered under the "sensor" platform
# via sensor.py::async_setup_entry — "diagnostic_sensor" is not a built-in HA
# platform domain, so it cannot be forwarded via async_forward_entry_setups.
# Phase 10 (D-04): button platform removed — the Reset Email Cache button is no
# longer needed; dedup is now tracking-number-based (not message-ID-based) and
# persisted in HA Store, so users never need to manually clear a cache.
PLATFORMS: list[str] = ["sensor", "binary_sensor"]

# Phase 26 Plan 02 (P26-REG-01..03): allowlist of unique_id suffixes that belong
# to known-good entities.  Any registry entry for this config entry whose suffix
# is NOT in this set is treated as an orphan from a prior version and removed
# during async_setup_entry before platform setup runs.
#
# All suffixes are derived from the entity classes' _unique_id_suffix attributes —
# the single source of truth (finding 9). DIAGNOSTIC_SENSOR_UID_SUFFIXES (diagnostic
# sensors), OPERATIONAL_SENSOR_UID_SUFFIXES (sensor.py), and
# OPERATIONAL_BINARY_SENSOR_UID_SUFFIXES (binary_sensor.py) each build from the classes
# they cover, so renaming a suffix on a class automatically updates this allowlist.
# No string literals here — that duplication previously risked the sweep deleting a
# freshly-registered entity whose class suffix had drifted from a hardcoded copy.
#
# NOTE: has_active_shipments is intentionally absent — it is an orphan to sweep.
# NOTE: per-message suffixes (e.g. msgABC123) are not in the allowlist — orphans.
KNOWN_GOOD_UID_SUFFIXES: frozenset[str] = (
    DIAGNOSTIC_SENSOR_UID_SUFFIXES
    | OPERATIONAL_SENSOR_UID_SUFFIXES
    | OPERATIONAL_BINARY_SENSOR_UID_SUFFIXES
)

# Suffix of the binary sensor removed in Phase 26 (P26-REMOVE-02). It was part of the
# public entity surface before removal, so its sweep is logged at WARNING (not INFO) with
# migration guidance — otherwise a user's automation/dashboard referencing it would break
# silently (finding 11).
_REMOVED_HAS_ACTIVE_SHIPMENTS_SUFFIX = "has_active_shipments"


def _sweep_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove orphaned entity registry entries left by prior integration versions.

    Phase 26 migration (P26-REG-01..03): HA does NOT auto-remove entities when
    their producing code disappears (RESEARCH.md primary challenge).  This sweep
    runs BEFORE async_forward_entry_setups (RESEARCH.md Pitfall 2) so the
    registry is clean before new entities register.

    Security domain (T-26-03): allowlist (positive) approach — only entries with
    a non-allowlisted suffix AND the correct {DOMAIN}_{entry_id}_ prefix are
    removed.  Entries outside this config entry / prefix are never touched.

    Two-pass pattern (T-26-04): collect entity_ids first, then remove — avoids
    mutating the iterable while iterating (RESEARCH.md anti-pattern).
    """
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    prefix = f"{DOMAIN}_{entry.entry_id}_"
    to_remove: list[str] = []
    for reg_entry in entries:
        uid = reg_entry.unique_id
        if not uid.startswith(prefix):
            # Not ours — leave untouched (cross-entry safety)
            continue
        suffix = uid[len(prefix) :]
        if suffix not in KNOWN_GOOD_UID_SUFFIXES:
            to_remove.append(reg_entry.entity_id)
            if suffix == _REMOVED_HAS_ACTIVE_SHIPMENTS_SUFFIX:
                # Finding 11: this was a documented entity before Phase 26, so warn (not
                # info) and point at the replacement — a user whose automations or
                # dashboards reference it gets a log breadcrumb rather than silent breakage.
                _LOGGER.warning(
                    "Removing deprecated entity %s (uid=%s): the 'Has Active Shipments' "
                    "binary sensor was removed in Phase 26. Update any automations or "
                    "dashboards to use the 'Shipments Forwarded' sensor "
                    "(its 'currently_tracked' attribute) instead.",
                    reg_entry.entity_id,
                    uid,
                )
            else:
                _LOGGER.info(
                    "Phase 26 migration: removing orphaned entity %s (uid=%s)",
                    reg_entry.entity_id,
                    uid,
                )
    for entity_id in to_remove:
        registry.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shop2Parcel from a config entry.

    Order of operations is critical:
      1. Construct coordinator (no I/O)
      2. _async_load_store() hydrates dedup set + quota state from disk
      3. async_config_entry_first_refresh() runs first poll cycle
      4. Store coordinator in hass.data and forward to platforms

    Step 2 MUST precede step 3 — RESEARCH.md Pitfall 1: an empty forwarded_ids
    set on first poll re-POSTs every previously forwarded shipment, wasting quota.

    Phase 29 (hub skeleton): hass.data[DOMAIN] init + the "_init_lock" asyncio.Lock
    MUST be the first hass.data touch, before any await or lazy import — this is
    the SPEC constraint that closes the multi-account constructor race (LIFE-05).
    The Shop2ParcelHub singleton is created at most once (behind that lock) in
    hass.data[DOMAIN]["__shared__"] and reference-counted per coordinator on
    attach/detach (LIFE-01..04). D-01: this does not touch the per-entry
    Stage-2 worker — that stays 100% intact.
    """
    import asyncio  # stdlib — already available

    # MUST be the first hass.data touch in this function — no await may precede
    # this block (SPEC constraint; closes the hub constructor race, LIFE-05).
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("_init_lock", asyncio.Lock())

    async with hass.data[DOMAIN]["_init_lock"]:
        if "__shared__" not in hass.data[DOMAIN]:
            from .hub import Shop2ParcelHub  # noqa: PLC0415

            hub = Shop2ParcelHub(hass)
            await hub.async_setup()
            hass.data[DOMAIN]["__shared__"] = hub
            _LOGGER.info("shared hub created")
    hub = hass.data[DOMAIN]["__shared__"]

    # Lazy import: gmail_coordinator.py and imap_coordinator.py depend on gmail_client.py
    # which requires google/googleapiclient stubs to be in sys.modules. Deferring to
    # function scope ensures the test harness (conftest.py) has registered the mocks
    # before this import runs. At production runtime there is no difference.
    from .const import (  # noqa: PLC0415
        CONF_CONNECTION_TYPE,
        CONF_OLLAMA_URL,
        CONNECTION_TYPE_GMAIL,
        CONNECTION_TYPE_IMAP,
    )
    from .coordinator import Shop2ParcelCoordinator  # noqa: PLC0415
    from .gmail_coordinator import GmailCoordinator  # noqa: PLC0415
    from .imap_coordinator import ImapCoordinator  # noqa: PLC0415

    # IN-06: use the constant, not a literal; the Gmail default keeps pre-1.5
    # Gmail entries (created without CONF_CONNECTION_TYPE in data) working.
    conn_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_GMAIL)
    coordinator: Shop2ParcelCoordinator
    if conn_type == CONNECTION_TYPE_IMAP:
        coordinator = ImapCoordinator(hass, entry)
    else:
        coordinator = GmailCoordinator(hass, entry)
    hub.attach(coordinator)
    await coordinator._async_load_store()
    # Phase 26 Plan 02 (P26-REG-01..03): sweep orphaned entity registry entries
    # (shipment_* per-message uids + has_active_shipments) left by prior versions.
    # MUST run after _async_load_store (store hydrated) and BEFORE
    # async_forward_entry_setups (RESEARCH.md Pitfall 2 — sweep before platform
    # setup so new entities register into a clean registry).
    _sweep_orphaned_entities(hass, entry)
    # Phase 17 D-05: derive stage2_enabled before first poll.
    # bool() coerces any non-empty URL string to True without exposing the URL value.
    # Empty string fallback prevents AttributeError on v1.2 entries with no ollama_url key.
    coordinator._diagnostics.stage2_enabled = bool(entry.options.get(CONF_OLLAMA_URL, ""))
    # Phase 18: when Stage-2 is enabled, construct the bounded queue + in-flight
    # dedup set so the first poll's enqueues already have somewhere to land.
    # Register async_stop_stage2 via async_on_unload so HA tears down the queue
    # on every unload path (clean unload, exception, HA shutdown).
    # WR-01: register the coroutine function DIRECTLY so unload AWAITS Stage-2
    # shutdown (worker cancel, bounded at 5 s inside async_stop_stage2, + final
    # save) before returning. The previous sync-lambda + hass.async_create_task
    # fire-and-forget let an options-save reload (OptionsFlowWithReload) start a
    # new coordinator/worker while the old worker was still mid-POST, and the old
    # coordinator's debounced save could land AFTER the new coordinator's saves —
    # clobbering fresh dedup state and burning quota on duplicate POSTs.
    # entry.async_on_unload awaits coroutine-returning callables on every HA
    # version this integration supports (>= 2025.8 for OptionsFlowWithReload).
    if coordinator._diagnostics.stage2_enabled:
        await coordinator.async_start_stage2()
        entry.async_on_unload(coordinator.async_stop_stage2)
    await coordinator.async_config_entry_first_refresh()

    # Finding 3: enable the time-boundary refresh timers (quota-expiry + UTC-midnight
    # used_today) now that the first poll has run and any quota window is established.
    # These keep the should_poll=False Problem/Quota entities from going stale between
    # polls when quota_is_exhausted / used_today flip purely by the passage of time.
    # Register cleanup via async_on_unload so HA cancels them on every teardown path.
    coordinator.enable_operational_timers()
    entry.async_on_unload(coordinator._cancel_operational_timers)

    # Phase 5 D-08: schedule once-daily delivered-shipment cleanup.
    # The cancel callback MUST be stored so async_unload_entry can stop the
    # scheduled task (RESEARCH.md "Don't Hand-Roll" — async_track_time_interval
    # gives us correct DST/shutdown handling for free).
    cancel_cleanup = async_track_time_interval(
        hass,
        coordinator.async_cleanup_delivered,
        timedelta(hours=24),
        name="shop2parcel_cleanup",
    )
    # Register via async_on_unload so HA cancels the timer on all teardown paths
    # (clean unload, exception from async_forward_entry_setups, or HA shutdown).
    # This prevents the orphaned-timer leak from async_track_time_interval.
    entry.async_on_unload(cancel_cleanup)

    # Phase 5 D-10: dict-shaped value — sensor.py / binary_sensor.py read ["coordinator"].
    # (hass.data[DOMAIN] itself was already initialized at the top of this
    # function, before the hub lock — Phase 29.)
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Dismiss notifications and delete the per-entry store when this entry is removed.

    W4/P14-WR-01: When the user uninstalls/removes the Shop2Parcel integration,
    any persistent debug-mode notification must be cleaned up.  This does not
    fire on a normal unload (e.g., HA restart), only on explicit removal, which
    is the correct behaviour — HA shows the notification again on next startup
    if the entry is re-added with debug_mode=True.

    Phase 20 MRG-05: also dismisses the Stage-2 cap-hit notification so neither
    notification lingers after the integration is removed.

    WR-05: also removes .storage/shop2parcel.{entry_id} — the file contains
    tracking numbers, message IDs, and order names/summaries (personal data)
    and would otherwise persist on disk indefinitely after an explicit
    uninstall. Standard HA pattern: per-entry stores are removed here.
    """
    from homeassistant.components import persistent_notification  # noqa: PLC0415

    from .const import (  # noqa: PLC0415
        debug_mode_notification_id,
        stage2_cap_notification_id,
        stage2_failing_notification_id,
    )
    from .coordinator import STORAGE_VERSION, Shop2ParcelStore  # noqa: PLC0415

    persistent_notification.async_dismiss(
        hass, notification_id=debug_mode_notification_id(entry.entry_id)
    )
    persistent_notification.async_dismiss(
        hass, notification_id=stage2_cap_notification_id(entry.entry_id)
    )
    persistent_notification.async_dismiss(
        hass, notification_id=stage2_failing_notification_id(entry.entry_id)
    )

    # WR-05: delete the persisted dedup/shipment state for this entry.
    store = Shop2ParcelStore(hass, version=STORAGE_VERSION, key=f"shop2parcel.{entry.entry_id}")
    await store.async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Symmetric with async_setup_entry: unload platforms first, then drop coordinator
    from hass.data only if the platform unload succeeded. Phase 5 benefits
    automatically when it populates PLATFORMS.

    Phase 29 (hub skeleton, D-04): after dropping this entry's coordinator,
    detach it from the shared hub explicitly (looked up from the per-entry
    hass.data dict captured before the pop — NOT via entry.async_on_unload;
    RESEARCH.md Open Question 1). If the hub's refcount reaches 0, shut it
    down and delete hass.data[DOMAIN]["__shared__"] — the hub itself never
    touches hass.data (D-06).
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # IN-05: .get() guard — hass.data[DOMAIN] is absent if unload runs
        # without a prior successful setup (future refactors, direct test calls).
        entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        coordinator = entry_data.get("coordinator") if entry_data else None
        hub = hass.data.get(DOMAIN, {}).get("__shared__")
        if hub is not None and coordinator is not None:
            hub.detach(coordinator)
        if hub is not None and hub._refcount == 0:
            await hub.async_shutdown()
            del hass.data[DOMAIN]["__shared__"]
        # cancel_cleanup is registered via entry.async_on_unload in async_setup_entry
        # so HA cancels it automatically — no explicit call needed here.
    return unload_ok
