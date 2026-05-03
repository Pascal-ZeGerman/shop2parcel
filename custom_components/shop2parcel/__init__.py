"""The Shop2Parcel integration.

Phase 4: async_setup_entry instantiates Shop2ParcelCoordinator, hydrates the
deduplication state from Store BEFORE the first refresh (RESEARCH.md Pitfall 1),
and forwards to PLATFORMS (empty in Phase 4; Phase 5 adds 'sensor' + 'binary_sensor').

Phase boundary:
- Phase 3 owned credential validation (replaced — coordinator's _async_update_data
  handles all API errors and translates to ConfigEntryAuthFailed/UpdateFailed).
- Phase 4 owns coordinator instantiation, Store hydration, platform forwarding stub,
  and options flow registration (registered via config_flow.py).
- Phase 5 adds 'sensor' and 'binary_sensor' to PLATFORMS, switches hass.data to
  dict shape, and wires the daily cleanup task via async_track_time_interval.
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
      2. async_load_store() hydrates dedup set + quota state from disk
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
    await coordinator.async_load_store()
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
    # This prevents the orphaned-timer leak described in RESEARCH.md WR-03.
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
