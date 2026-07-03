"""Tests for Shop2Parcel __init__.py — Phase 5 coordinator wiring.

Verifies async_setup_entry instantiates Shop2ParcelCoordinator, hydrates Store
before first refresh, and that hass.data[DOMAIN][entry_id] holds a dict with
"coordinator" key (Phase 5 dict shape). cancel_cleanup is registered via
entry.async_on_unload rather than stored in hass.data (WR-03 fix).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.shop2parcel.api.exceptions import GmailAuthError
from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator


async def test_setup_entry_imap_wires_imap_coordinator(hass, mock_imap_config_entry):
    """IMAP connection_type dispatches ImapCoordinator, not GmailCoordinator."""
    mock_imap_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
        result = await hass.config_entries.async_setup(mock_imap_config_entry.entry_id)
    assert result is True
    coordinator = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
    assert isinstance(coordinator, ImapCoordinator)


async def test_setup_entry_wires_coordinator(hass, mock_config_entry):
    """Phase 5 setup stores dict with coordinator in hass.data.

    cancel_cleanup is registered via entry.async_on_unload (WR-03) and is
    therefore NOT stored in hass.data — HA calls it automatically on unload.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    entry_data = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert isinstance(entry_data, dict)
    assert isinstance(entry_data["coordinator"], Shop2ParcelCoordinator)
    # cancel_cleanup is registered via entry.async_on_unload, not stored in hass.data
    assert "cancel_cleanup" not in entry_data


async def test_setup_entry_calls_load_store_before_first_refresh(hass, mock_config_entry):
    """RESEARCH.md Pitfall 1: Store.async_load MUST run before _async_update_data.

    Otherwise forwarded_ids is empty on first poll and every prior shipment is re-POSTed.
    """
    mock_config_entry.add_to_hass(hass)
    parent = MagicMock()
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        # Track call order via parent mock
        store_load = AsyncMock(return_value=None)
        list_messages = AsyncMock(return_value=([], "q after:0"))
        parent.attach_mock(store_load, "async_load")
        parent.attach_mock(list_messages, "async_list_messages")
        mock_store_cls.return_value.async_load = store_load
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = list_messages
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    # Assert async_load was called before async_list_messages
    call_names = [c[0] for c in parent.mock_calls if c[0] in ("async_load", "async_list_messages")]
    assert call_names.index("async_load") < call_names.index("async_list_messages")


async def test_setup_entry_gmail_auth_failure_sets_setup_error(hass, mock_config_entry):
    """Gmail auth error -> coordinator raises ConfigEntryAuthFailed -> HA SETUP_ERROR state."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            side_effect=GmailAuthError("fake auth fail")
        )
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry_removes_coordinator(hass, mock_config_entry):
    """async_unload_entry calls async_unload_platforms with PLATFORMS then drops hass.data."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert mock_config_entry.entry_id in hass.data[DOMAIN]
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_unload_entry_awaits_stage2_shutdown(hass, mock_config_entry):
    """WR-01: unload must AWAIT Stage-2 shutdown — async_stop_stage2 has run to
    COMPLETION by the time async_unload returns. The previous fire-and-forget
    lambda (hass.async_create_task) let a reload start a new coordinator/worker
    while the old teardown was still pending, so its debounced save could land
    after — and clobber — the new coordinator's state."""
    import asyncio

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={"ollama_url": "http://localhost:11434"},
    )

    stop_completed = False
    real_stop = Shop2ParcelCoordinator.async_stop_stage2

    async def _tracked_stop(self):
        nonlocal stop_completed
        # Force at least one event-loop suspension so an (incorrect) eager
        # fire-and-forget registration cannot complete this synchronously.
        await asyncio.sleep(0)
        await real_stop(self)
        stop_completed = True

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch.object(Shop2ParcelCoordinator, "async_stop_stage2", _tracked_stop),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        worker_task = coordinator._stage2_worker_task
        assert worker_task is not None and not worker_task.done(), (
            "precondition: worker running after setup"
        )

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)

        # WR-01: shutdown must be COMPLETE when unload returns — no pending
        # fire-and-forget task allowed to overlap with a subsequent reload.
        assert stop_completed, "unload must await async_stop_stage2 to completion"
        assert worker_task.done(), "worker task must be fully stopped when unload returns"
        assert coordinator._stage2_worker_task is None, (
            "async_stop_stage2 must have run to completion inside unload"
        )


async def test_setup_entry_forwards_to_sensor_platforms(hass, mock_config_entry):
    """CONTEXT.md D-09 / Phase 10 D-04: PLATFORMS includes sensor and binary_sensor only.

    The button platform was removed in Phase 10 (D-04) because the Reset Email
    Cache button is no longer needed — dedup is now tracking-number-based and
    persisted in HA Store automatically.
    """
    from custom_components.shop2parcel import PLATFORMS

    assert PLATFORMS == ["sensor", "binary_sensor"]


async def test_setup_entry_registers_cleanup_task_with_24h_interval(hass, mock_config_entry):
    """D-08: async_track_time_interval is registered with timedelta(hours=24)."""
    from datetime import timedelta

    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.async_track_time_interval") as mock_track,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        cancel_cb = MagicMock()
        mock_track.return_value = cancel_cb
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
    # Assert async_track_time_interval was called with the 24h timedelta
    assert mock_track.called
    call_args = mock_track.call_args
    # Positional args: (hass, callback, interval) — interval is positional in HA's signature
    interval_arg = (
        call_args.args[2] if len(call_args.args) >= 3 else call_args.kwargs.get("interval")
    )
    assert interval_arg == timedelta(hours=24)


async def test_unload_entry_cancels_cleanup_task(hass, mock_config_entry):
    """D-10: async_unload_entry must invoke the cancel callback returned by async_track_time_interval."""
    mock_config_entry.add_to_hass(hass)
    cancel_cb = MagicMock()
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch("custom_components.shop2parcel.async_track_time_interval", return_value=cancel_cb),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        cancel_cb.assert_not_called()  # Setup does NOT call cancel
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
    cancel_cb.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 17 D-05: stage2_enabled diagnostic flag
# ---------------------------------------------------------------------------


def test_stage2_enabled_pollstats_default():
    """D-05 / Test 5: PollStats().stage2_enabled defaults to False.

    This is a pure dataclass default — no coordinator setup needed.
    """
    from custom_components.shop2parcel.coordinator import PollStats

    stats = PollStats()
    assert stats.stage2_enabled is False


async def test_stage2_enabled_false_when_options_empty(hass, mock_config_entry):
    """D-05 / Test 1: empty entry.options (no ollama_url key) → stage2_enabled is False after setup.

    Covers the v1.2 backward-compat case: entries created before Phase 17 have
    no ollama_url in their options dict.  The integration must load without raising
    and expose stage2_enabled=False via coordinator.diagnostics.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
    assert coordinator.diagnostics.stage2_enabled is False


async def test_stage2_enabled_true_when_ollama_url_set(hass):
    """D-05 / Test 2: entry.options == {"ollama_url": "http://10.0.0.5:11434"} → stage2_enabled True.

    Covers the v1.3 case where the user has configured an Ollama server URL.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={"ollama_url": "http://10.0.0.5:11434"},
        unique_id="ollama_user@gmail.com",
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.diagnostics.stage2_enabled is True


async def test_stage2_enabled_false_when_ollama_url_empty_string(hass):
    """D-05 / Test 3: entry.options == {"ollama_url": ""} (explicit empty) → stage2_enabled False.

    An explicit empty string must behave identically to the missing-key case.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={"ollama_url": ""},
        unique_id="emptyurl_user@gmail.com",
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.diagnostics.stage2_enabled is False


async def test_stage2_v12_entry_no_ollama_url_loads_without_exception(hass, mock_config_entry):
    """D-05 / Test 4 (no-regression): v1.2-shaped entry with no ollama_url loads without raising.

    This is the CFG-04 backward-compat requirement: an entry created before Phase 17
    must continue to boot Stage-1-only without any crash or exception.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        # Must not raise
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert result is True


# ---------------------------------------------------------------------------
# Phase 26 Plan 02: Migration sweep tests (P26-REG-01..03)
# ---------------------------------------------------------------------------


async def test_migration_sweep_removes_shipment_entities(hass, mock_config_entry):
    """P26-REG-01: Orphaned shipment_* entities are removed from registry during setup.

    Pre-seed an entity with a per-message uid (shop2parcel_{entry_id}_msgABC123) under
    the config entry. After async_setup_entry, that entity must no longer exist in the
    entity registry — the _sweep_orphaned_entities migration removed it.
    """
    from homeassistant.helpers import entity_registry as er

    mock_config_entry.add_to_hass(hass)
    entry_id = mock_config_entry.entry_id
    from custom_components.shop2parcel.const import DOMAIN

    # Pre-seed an orphaned per-message shipment entity
    registry = er.async_get(hass)
    orphan = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_msgABC123",
        config_entry=mock_config_entry,
    )
    assert registry.async_get(orphan.entity_id) is not None, "Pre-condition: entity seeded"

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(entry_id)

    assert result is True
    # The orphaned entity must be gone after setup
    assert registry.async_get(orphan.entity_id) is None, (
        "Orphaned shipment_* entity must be removed by migration sweep"
    )


async def test_migration_sweep_removes_has_active_shipments(hass, mock_config_entry):
    """P26-REG-02: has_active_shipments entity is removed by _sweep_orphaned_entities.

    Tests the sweep function directly to confirm has_active_shipments is absent from
    KNOWN_GOOD_UID_SUFFIXES and is collected for removal.  The full async_setup_entry
    path is not used here because binary_sensor.py still contains HasActiveShipmentsBinarySensor
    which re-registers the entity during platform setup — that class is removed in Plan 03.
    Direct sweep testing is the correct approach for Plan 02.
    """
    from homeassistant.helpers import entity_registry as er

    mock_config_entry.add_to_hass(hass)
    entry_id = mock_config_entry.entry_id
    from custom_components.shop2parcel import _sweep_orphaned_entities
    from custom_components.shop2parcel.const import DOMAIN

    registry = er.async_get(hass)
    orphan = registry.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_has_active_shipments",
        config_entry=mock_config_entry,
    )
    assert registry.async_get(orphan.entity_id) is not None, "Pre-condition: entity seeded"

    # Call the sweep function directly — bypasses binary_sensor platform re-registration
    _sweep_orphaned_entities(hass, mock_config_entry)

    assert registry.async_get(orphan.entity_id) is None, (
        "has_active_shipments entity must be removed by _sweep_orphaned_entities"
    )


async def test_migration_sweep_preserves_allowlisted_entities(hass, mock_config_entry):
    """P26-REG-03: Allowlisted entities (diagnostic + operational) survive the sweep.

    Pre-seed:
      - sensor with diagnostic suffix emails_scanned
      - binary_sensor with suffix email_processing_active
      - sensor with new operational suffix shipments_forwarded

    All three must still exist in the entity registry after async_setup_entry.
    """
    from homeassistant.helpers import entity_registry as er

    mock_config_entry.add_to_hass(hass)
    entry_id = mock_config_entry.entry_id
    from custom_components.shop2parcel.const import DOMAIN

    registry = er.async_get(hass)
    diag_entity = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_emails_scanned",
        config_entry=mock_config_entry,
    )
    proc_entity = registry.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_email_processing_active",
        config_entry=mock_config_entry,
    )
    fwd_entity = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_shipments_forwarded",
        config_entry=mock_config_entry,
    )

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        result = await hass.config_entries.async_setup(entry_id)

    assert result is True
    # All three allowlisted entities must survive the sweep
    assert registry.async_get(diag_entity.entity_id) is not None, (
        "emails_scanned (diagnostic) must be preserved"
    )
    assert registry.async_get(proc_entity.entity_id) is not None, (
        "email_processing_active must be preserved"
    )
    assert registry.async_get(fwd_entity.entity_id) is not None, (
        "shipments_forwarded (new operational) must be preserved"
    )


def test_operational_uid_suffixes_derived_from_entity_classes():
    """Finding 9: operational uid suffixes in KNOWN_GOOD_UID_SUFFIXES must come from the
    entity classes' _unique_id_suffix attribute (single source of truth), not hardcoded
    literals. Otherwise renaming a suffix on the class without also editing __init__.py
    makes _sweep_orphaned_entities delete the freshly-registered entity on every restart.
    """
    from custom_components.shop2parcel import KNOWN_GOOD_UID_SUFFIXES
    from custom_components.shop2parcel.binary_sensor import (
        EmailProcessingActiveBinarySensor,
        ProblemBinarySensor,
    )
    from custom_components.shop2parcel.sensor import (
        LastForwardedSensor,
        ParcelAppQuotaSensor,
        ShipmentsForwardedSensor,
    )

    operational_classes = [
        ProblemBinarySensor,
        EmailProcessingActiveBinarySensor,
        ShipmentsForwardedSensor,
        LastForwardedSensor,
        ParcelAppQuotaSensor,
    ]
    for cls in operational_classes:
        assert hasattr(cls, "_unique_id_suffix"), (
            f"{cls.__name__} must expose _unique_id_suffix (single source of truth)"
        )
        assert cls._unique_id_suffix in KNOWN_GOOD_UID_SUFFIXES, (
            f"{cls.__name__}._unique_id_suffix={cls._unique_id_suffix} must be in the sweep "
            "allowlist — derived from the class, not a drifting literal"
        )


async def test_sweep_warns_on_has_active_shipments_removal(hass, mock_config_entry, caplog):
    """Finding 11: removing the deprecated has_active_shipments entity must emit a WARNING
    pointing users to the replacement, so automations referencing it leave a log
    breadcrumb instead of breaking silently.
    """
    import logging

    from homeassistant.helpers import entity_registry as er

    from custom_components.shop2parcel import _sweep_orphaned_entities
    from custom_components.shop2parcel.const import DOMAIN

    mock_config_entry.add_to_hass(hass)
    entry_id = mock_config_entry.entry_id
    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_{entry_id}_has_active_shipments",
        config_entry=mock_config_entry,
    )

    with caplog.at_level(logging.WARNING):
        _sweep_orphaned_entities(hass, mock_config_entry)

    assert "has_active_shipments" in caplog.text, "removal must be surfaced at WARNING level"
    assert "Shipments Forwarded" in caplog.text, "the warning must name the replacement entity"


# ---------------------------------------------------------------------------
# WR-05: async_remove_entry deletes the per-entry store file
# ---------------------------------------------------------------------------


async def test_remove_entry_deletes_store_file(hass, mock_config_entry):
    """WR-05: async_remove_entry removes .storage/shop2parcel.{entry_id}.

    The store contains tracking numbers, message IDs, and order names/summaries
    (personal data) — it must not persist on disk after an explicit uninstall.
    """
    from custom_components.shop2parcel import async_remove_entry
    from custom_components.shop2parcel.coordinator import STORAGE_VERSION, Shop2ParcelStore

    mock_config_entry.add_to_hass(hass)
    key = f"shop2parcel.{mock_config_entry.entry_id}"
    store = Shop2ParcelStore(hass, version=STORAGE_VERSION, key=key)
    await store.async_save(
        {
            "submitted_tracking_numbers": ["1Z999AA10123456784"],
            "quota_exhausted_until": None,
            "persisted_shipments": {},
        }
    )
    assert await store.async_load() is not None, "precondition: store file must exist"

    await async_remove_entry(hass, mock_config_entry)

    fresh = Shop2ParcelStore(hass, version=STORAGE_VERSION, key=key)
    assert await fresh.async_load() is None, "store file must be deleted on entry removal"


async def test_remove_entry_without_store_file_does_not_raise(hass, mock_config_entry):
    """WR-05: removing an entry that never persisted a store must not raise."""
    from custom_components.shop2parcel import async_remove_entry

    mock_config_entry.add_to_hass(hass)
    await async_remove_entry(hass, mock_config_entry)  # must complete without error
