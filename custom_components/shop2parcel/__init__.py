"""The Shop2Parcel integration entry point.

Responsibilities:
- Instantiates Shop2ParcelCoordinator and hydrates deduplication state from
  persistent Store BEFORE the first refresh (RESEARCH.md Pitfall 1: Store must
  be loaded before async_config_entry_first_refresh() to avoid re-forwarding
  previously processed shipments).
- Schedules the once-daily delivered-shipment cleanup task via
  async_track_time_interval and registers the cancel callback with
  entry.async_on_unload so the timer is stopped on all teardown paths.
- Stores the coordinator in hass.data[DOMAIN][entry.entry_id] as a dict keyed
  by "coordinator" so sensor.py and binary_sensor.py can retrieve it.
- Forwards platform setup to PLATFORMS ("sensor", "binary_sensor").
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN

# Phase 5 (CONTEXT.md D-09): platforms now populated.
# Phase 7 (D-13): diagnostic sensors are co-registered under the "sensor" platform
# via sensor.py::async_setup_entry — "diagnostic_sensor" is not a built-in HA
# platform domain, so it cannot be forwarded via async_forward_entry_setups.
PLATFORMS: list[str] = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shop2Parcel from a config entry.

    Order of operations is critical:
      1. Construct coordinator (no I/O)
      2. _async_load_store() hydrates dedup set + quota state from disk
      3. async_config_entry_first_refresh() runs first poll cycle
      4. Store coordinator in hass.data and forward to platforms

    Step 2 MUST precede step 3 — RESEARCH.md Pitfall 1: an empty forwarded_ids
    set on first poll re-POSTs every previously forwarded shipment, wasting quota.
    """
    # Lazy import: coordinator.py depends on gmail_client.py which requires
    # google/googleapiclient stubs to be in sys.modules. Deferring to function scope
    # ensures the test harness (conftest.py) has registered the mocks before this
    # import runs. At production runtime there is no difference.
    from .coordinator import Shop2ParcelCoordinator  # noqa: PLC0415

    coordinator = Shop2ParcelCoordinator(hass, entry)
    await coordinator._async_load_store()
    await coordinator.async_config_entry_first_refresh()

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

    hass.data.setdefault(DOMAIN, {})
    # Phase 5 D-10: dict-shaped value — sensor.py / binary_sensor.py read ["coordinator"].
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Symmetric with async_setup_entry: unload platforms first, then drop coordinator
    from hass.data only if the platform unload succeeded. Phase 5 benefits
    automatically when it populates PLATFORMS.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # cancel_cleanup is registered via entry.async_on_unload in async_setup_entry
        # so HA cancels it automatically — no explicit call needed here.
    return unload_ok
