"""Tests for Shop2ParcelStore._async_migrate_func — v1 → v2 migration.

Covers D-01, D-02, D-03 from Phase 10 context.
No HA hass fixture required — tests use Store.__new__ to bypass Store.__init__
and test the migration logic directly on a stub instance.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch as _patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN, MAX_SUBMITTED_TRACKING_NUMBERS
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator, Shop2ParcelStore
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator


@pytest.fixture()
def store() -> Shop2ParcelStore:
    """Return a Shop2ParcelStore instance that bypasses Store.__init__."""
    s = Shop2ParcelStore.__new__(Shop2ParcelStore)
    s.key = "shop2parcel.test_entry_id"
    return s


async def test_migrate_func_v1_drops_old_keys_and_seeds_submitted_tracking_numbers(
    store: Shop2ParcelStore,
) -> None:
    """D-01 / D-02: v1→v2 migration drops forwarded_ids/last_imap_uid/last_email_timestamp
    and seeds submitted_tracking_numbers as an empty list.
    quota_exhausted_until is preserved from old_data.
    """
    old_data = {
        "forwarded_ids": ["msg1"],
        "last_imap_uid": 10,
        "last_email_timestamp": 1700000000,
        "quota_exhausted_until": 9999999,
    }
    result = await store._async_migrate_func(1, 1, old_data)
    assert "forwarded_ids" not in result
    assert "last_imap_uid" not in result
    assert "last_email_timestamp" not in result
    assert result["submitted_tracking_numbers"] == []
    assert result["quota_exhausted_until"] == 9999999


async def test_migrate_func_v1_preserves_missing_quota_exhausted_until_as_none(
    store: Shop2ParcelStore,
) -> None:
    """D-02: When quota_exhausted_until is absent from v1 data, result must have None."""
    result = await store._async_migrate_func(1, 1, {"forwarded_ids": []})
    assert result["quota_exhausted_until"] is None


async def test_migrate_func_v1_emits_warning_with_entry_id(
    store: Shop2ParcelStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-03: Migration must emit a WARNING log containing both the literal
    'Migrated Shop2Parcel Store to v2' and the entry_id derived from self.key.
    """
    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        await store._async_migrate_func(1, 1, {})

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected at least one WARNING log record"
    messages = " ".join(r.getMessage() for r in warning_records)
    assert "Migrated Shop2Parcel Store to v2" in messages, (
        f"WARNING log must contain 'Migrated Shop2Parcel Store to v2', got: {messages!r}"
    )
    assert "test_entry_id" in messages, (
        f"WARNING log must contain entry_id 'test_entry_id', got: {messages!r}"
    )


async def test_migrate_func_future_version_returns_data_unchanged(
    store: Shop2ParcelStore,
) -> None:
    """Same-major future-minor versions are returned unchanged (passthrough)."""
    # version=3 is the current; old_major_version=3 (same) → passthrough
    store.version = 3
    store.minor_version = 1
    old_data = {"submitted_tracking_numbers": ["X"], "quota_exhausted_until": None}
    result = await store._async_migrate_func(3, 0, old_data)
    assert result == old_data


async def test_migrate_unknown_future_major_returns_empty(
    store: Shop2ParcelStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """W16/P13-WR-08: Future major version → returns empty recoverable state + WARNING log."""
    store.version = 3
    store.minor_version = 1
    old_data = {"submitted_tracking_numbers": ["TN1", "TN2"], "quota_exhausted_until": 9999}

    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        result = await store._async_migrate_func(4, 0, old_data)

    assert result["submitted_tracking_numbers"] == []
    assert result["quota_exhausted_until"] is None
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected at least one WARNING log record"
    messages = " ".join(r.getMessage() for r in warning_records)
    assert "downgrade not supported" in messages.lower() or "newer" in messages.lower(), (
        f"WARNING must mention downgrade or newer version, got: {messages!r}"
    )


# ---------------------------------------------------------------------------
# R3/test-a: v2→v3 migration seeds persisted_shipments (RED — Plan 02 implements)
# ---------------------------------------------------------------------------


async def test_migrate_func_v2_seeds_persisted_shipments(
    store: Shop2ParcelStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R3: _async_migrate_func(2, 0, ...) must return persisted_shipments={},
    preserve submitted_tracking_numbers + quota_exhausted_until, and emit
    a WARNING log mentioning 'v3' and the entry_id.

    RED: fails until Plan 02 adds the v2→v3 branch in _async_migrate_func.
    """
    old_data = {
        "submitted_tracking_numbers": ["TN1", "TN2"],
        "quota_exhausted_until": 9999999,
    }

    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        result = await store._async_migrate_func(2, 0, old_data)

    assert result["persisted_shipments"] == {}, (
        "v2→v3 migration must seed persisted_shipments as empty dict"
    )
    assert result["submitted_tracking_numbers"] == ["TN1", "TN2"], (
        "v2→v3 migration must preserve submitted_tracking_numbers"
    )
    assert result["quota_exhausted_until"] == 9999999, (
        "v2→v3 migration must preserve quota_exhausted_until"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected at least one WARNING log record"
    messages = " ".join(r.getMessage() for r in warning_records)
    assert "v3" in messages, (
        f"WARNING log must mention 'v3', got: {messages!r}"
    )
    assert "test_entry_id" in messages, (
        f"WARNING log must contain entry_id 'test_entry_id', got: {messages!r}"
    )


# ---------------------------------------------------------------------------
# R5/test-f: _async_load_store skips corrupt persisted_shipments entries (RED)
# ---------------------------------------------------------------------------


async def test_load_store_skips_corrupt_persisted_shipments_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5: _async_load_store must skip invalid persisted_shipments entries with
    one WARNING each, while preserving valid siblings in _restored_shipments.

    RED: fails until Plan 02 extends _async_load_store to load persisted_shipments.
    """
    coordinator = Shop2ParcelCoordinator.__new__(Shop2ParcelCoordinator)
    mock_store = MagicMock()
    mock_store.async_load = AsyncMock(
        return_value={
            "submitted_tracking_numbers": [],
            "quota_exhausted_until": None,
            "persisted_shipments": {
                "valid_msg": {
                    "tracking_number": "TN123",
                    "carrier_name": "UPS",
                    "order_name": "#1001",
                    "message_id": "valid_msg",
                    "email_date": 1700000000,
                },
                "corrupt_msg": {"tracking_number": 999},  # wrong type, missing fields
            },
        }
    )
    coordinator._store = mock_store
    coordinator._submitted_tracking_numbers = OrderedDict()
    coordinator._quota_exhausted_until = None
    coordinator._pending_shipments = {}
    coordinator._restored_shipments = {}
    coordinator._store_loaded = False

    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        await coordinator._async_load_store()

    assert "valid_msg" in coordinator._restored_shipments, (
        "valid persisted_shipments entry must be loaded into _restored_shipments"
    )
    assert isinstance(coordinator._restored_shipments["valid_msg"], ShipmentData), (
        "_restored_shipments values must be ShipmentData instances"
    )
    assert "corrupt_msg" not in coordinator._restored_shipments, (
        "corrupt persisted_shipments entry must be skipped"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1 and "corrupt_msg" in warning_records[0].getMessage(), (
        "Exactly 1 WARNING mentioning 'corrupt_msg' must be emitted for the invalid entry"
    )


# ---------------------------------------------------------------------------
# Helper: set up a coordinator (Gmail or IMAP) with full mock infrastructure.
# Returns (coordinator, mock_store_instance) so tests can inspect store calls.
# ---------------------------------------------------------------------------

async def _setup_coordinator(hass, config_entry, coordinator_cls, request):
    """Set up a coordinator with mocked store, clients, and parser.

    Returns (coordinator, mock_store_instance) where mock_store_instance exposes
    async_delay_save (MagicMock) so tests can inspect persisted_shipments.

    For GmailCoordinator: applies persistent patches for the OAuth2 flow and
    GmailClient so _async_update_data() can be called after setup without
    the real OAuth infrastructure being invoked.  The patches are started
    here (rather than scoped to the setup call) so subsequent direct calls to
    coordinator._async_update_data() in tests continue to see the mocks.

    Patch teardown is registered via request.addfinalizer so patches are
    released even when a test assertion fails mid-test (leak-proof teardown).
    """
    from tests.conftest import setup_coordinator_with_data, setup_imap_coordinator_with_data

    if coordinator_cls is GmailCoordinator:
        coordinator = await setup_coordinator_with_data(hass, config_entry, {})
        _oauth_patcher = _patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        )
        mock_oauth = _oauth_patcher.start()
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        _gmail_patcher = _patch(
            "custom_components.shop2parcel.gmail_coordinator.GmailClient"
        )
        mock_gmail_cls = _gmail_patcher.start()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        # Overwrite the coordinator's _email_client with the new mock so the
        # persistent patch takes effect for subsequent _async_update_data() calls.
        coordinator._email_client = mock_gmail_cls.return_value

        # Register finalizers so patches are stopped even on test assertion failure.
        request.addfinalizer(_oauth_patcher.stop)
        request.addfinalizer(_gmail_patcher.stop)
    else:
        coordinator = await setup_imap_coordinator_with_data(hass, config_entry, {})
    return coordinator, coordinator._store


# ---------------------------------------------------------------------------
# R1/test-b: shipments are saved to store after a successful poll (RED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_shipments_saved_to_store_after_poll(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R1: After _async_update_data yields a shipment, the mocked store's
    async_delay_save must have been called with a lambda that materialises a
    dict containing 'persisted_shipments' with the new shipment's fields.

    RED: fails until Plan 03 adds _pending_shipments assignment + _async_save_store()
    call at end of _async_update_data in both coordinators.
    """
    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry

    # Build the expected shipment
    expected_shipment = ShipmentData(
        tracking_number="TN_NEW",
        carrier_name="UPS",
        order_name="#1001",
        message_id="MSG_NEW",
        email_date=1700000000,
    )

    # Pre-populate coordinator with the shipment (simulates a poll that found it)
    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)
    coordinator.async_set_updated_data({"MSG_NEW": expected_shipment})

    # Trigger a poll with no new emails (the FIFO-cap + save logic fires at end of poll)
    coordinator._restored_shipments = {}
    await coordinator._async_update_data()

    # Inspect the lambda passed to async_delay_save
    assert mock_store.async_delay_save.called, (
        "async_delay_save must be called at end of _async_update_data"
    )
    save_lambda = mock_store.async_delay_save.call_args[0][0]
    materialized = save_lambda()
    assert "persisted_shipments" in materialized, (
        "async_delay_save lambda must include 'persisted_shipments' key"
    )
    assert "MSG_NEW" in materialized["persisted_shipments"], (
        "persisted_shipments must contain the shipment keyed by message_id"
    )
    entry = materialized["persisted_shipments"]["MSG_NEW"]
    assert entry == {
        "tracking_number": "TN_NEW",
        "carrier_name": "UPS",
        "order_name": "#1001",
        "message_id": "MSG_NEW",
        "email_date": 1700000000,
    }, f"persisted_shipments entry has wrong fields: {entry!r}"


# ---------------------------------------------------------------------------
# R2/test-c: restored shipments appear in the first poll after restart (RED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_restored_shipments_present_in_first_poll(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R2: When coordinator.data is None (first poll after restart) and
    _restored_shipments has entries, _async_update_data must seed current_data
    from _restored_shipments so the returned dict contains those shipments.

    RED: fails until Plan 03 changes the seed expression in both coordinators.
    """
    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry

    coordinator, _mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    # Simulate a restart: data is None, _restored_shipments has 2 entries
    coordinator.async_set_updated_data(None)  # type: ignore[arg-type]
    coordinator._restored_shipments = {
        "A": ShipmentData(tracking_number="TNA", carrier_name="UPS", order_name="#A", message_id="A", email_date=1),
        "B": ShipmentData(tracking_number="TNB", carrier_name="FedEx", order_name="#B", message_id="B", email_date=2),
    }

    result = await coordinator._async_update_data()

    assert "A" in result, (
        "restored shipment 'A' must be present in first-poll result"
    )
    assert "B" in result, (
        "restored shipment 'B' must be present in first-poll result"
    )
    assert isinstance(result["A"], ShipmentData), (
        "result['A'] must be a ShipmentData instance"
    )
    assert result["A"].tracking_number == "TNA", (
        f"result['A'].tracking_number must be 'TNA', got: {result['A'].tracking_number!r}"
    )


# ---------------------------------------------------------------------------
# R4/test-d: cleanup removes delivered shipment from store (RED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_cleanup_removes_shipment_from_store(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R4: async_cleanup_delivered must assign _pending_shipments = new_data and
    call _async_save_store so the delivered shipment is removed from the store.

    RED: fails until Plan 02 appends _pending_shipments assignment + save to
    async_cleanup_delivered.
    """
    from datetime import datetime as _datetime

    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry

    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    shipment_y = ShipmentData(tracking_number="TNY", carrier_name="UPS", order_name="#Y", message_id="Y", email_date=1)
    shipment_z = ShipmentData(tracking_number="TNZ", carrier_name="FedEx", order_name="#Z", message_id="Z", email_date=2)
    coordinator.async_set_updated_data({"Y": shipment_y, "Z": shipment_z})

    # Reset call count so only the cleanup save counts
    mock_store.async_delay_save.reset_mock()

    # Patch ParcelAppClient so async_get_deliveries returns "Y" as delivered
    with _patch(
        "custom_components.shop2parcel.coordinator.ParcelAppClient"
    ) as mock_parcel_cls:
        mock_parcel_cls.return_value.async_get_deliveries = AsyncMock(
            return_value=[
                {"tracking_number": "TNY", "status_code": 0},
                {"tracking_number": "TNZ", "status_code": 1},
            ]
        )
        await coordinator.async_cleanup_delivered(_datetime.now())

    assert "Y" not in coordinator._pending_shipments, (
        "delivered shipment 'Y' must be removed from _pending_shipments after cleanup"
    )
    assert "Z" in coordinator._pending_shipments, (
        "non-delivered shipment 'Z' must remain in _pending_shipments after cleanup"
    )
    assert mock_store.async_delay_save.called, (
        "async_delay_save must be triggered after cleanup removes delivered shipments"
    )
    assert not mock_store.async_save.called, (
        "async_save must NOT be called — cleanup uses the debounced async_delay_save path"
    )
    # Inspect the lambda from the most recent delay_save call
    if mock_store.async_delay_save.called:
        save_lambda = mock_store.async_delay_save.call_args[0][0]
        materialized = save_lambda()
        assert "Y" not in materialized.get("persisted_shipments", {}), (
            "persisted_shipments must not contain delivered shipment 'Y' after cleanup"
        )
        assert "Z" in materialized.get("persisted_shipments", {}), (
            "persisted_shipments must still contain non-delivered shipment 'Z'"
        )


# ---------------------------------------------------------------------------
# R1/test-e: FIFO cap evicts oldest entry when >1000 shipments (RED)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_fifo_cap_evicts_oldest_entry(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R1: When current_data exceeds MAX_SUBMITTED_TRACKING_NUMBERS (1000),
    the FIFO cap must evict the oldest entries (by insertion order) so that
    persisted_shipments contains exactly 1000 entries after the poll.

    RED: fails until Plan 03 adds the while-len-trim loop before
    self._pending_shipments = trimmed in _async_update_data.
    """
    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry

    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    # Seed coordinator with 1001 entries in known insertion order
    oversize_data: dict[str, ShipmentData] = {}
    for i in range(1001):
        msg_id = f"msg_{i:04d}"
        oversize_data[msg_id] = ShipmentData(
            tracking_number=f"TN{i:04d}",
            carrier_name="UPS",
            order_name=f"#{i}",
            message_id=msg_id,
            email_date=i,
        )
    coordinator.async_set_updated_data(oversize_data)
    coordinator._restored_shipments = {}

    # Reset call counter before the poll
    mock_store.async_delay_save.reset_mock()

    # Run a poll with no new emails — FIFO trim + save fires at end
    await coordinator._async_update_data()

    assert mock_store.async_delay_save.called, (
        "async_delay_save must be called at end of _async_update_data"
    )
    save_lambda = mock_store.async_delay_save.call_args[0][0]
    materialized = save_lambda()
    persisted = materialized.get("persisted_shipments", {})

    assert len(persisted) == MAX_SUBMITTED_TRACKING_NUMBERS, (
        f"persisted_shipments must be capped at {MAX_SUBMITTED_TRACKING_NUMBERS}, "
        f"got {len(persisted)}"
    )
    assert "msg_0000" not in persisted, (
        "oldest entry 'msg_0000' must be evicted by FIFO cap"
    )
    assert "msg_1000" in persisted, (
        "newest entry 'msg_1000' must be retained after FIFO cap"
    )
    assert "msg_0001" in persisted, (
        "second-oldest entry 'msg_0001' must be retained after FIFO cap"
    )


# ---------------------------------------------------------------------------
# R6 boundary: exactly at cap must NOT evict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_fifo_cap_at_boundary_does_not_evict(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R6 boundary: exactly MAX entries must not trigger eviction.

    The trim uses `while len > MAX` (strictly greater), so at exactly cap no
    entry is removed — msg_0000 (oldest) must still be present.
    """
    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry
    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    at_cap: dict[str, ShipmentData] = {
        f"msg_{i:04d}": ShipmentData(
            tracking_number=f"TN{i:04d}", carrier_name="UPS",
            order_name=f"#{i}", message_id=f"msg_{i:04d}", email_date=i,
        )
        for i in range(MAX_SUBMITTED_TRACKING_NUMBERS)
    }
    coordinator.async_set_updated_data(at_cap)
    coordinator._restored_shipments = {}
    mock_store.async_delay_save.reset_mock()

    await coordinator._async_update_data()

    save_lambda = mock_store.async_delay_save.call_args[0][0]
    persisted = save_lambda()["persisted_shipments"]

    assert len(persisted) == MAX_SUBMITTED_TRACKING_NUMBERS, (
        f"exactly {MAX_SUBMITTED_TRACKING_NUMBERS} entries must be retained (no eviction at cap)"
    )
    assert "msg_0000" in persisted, (
        "oldest entry must be retained when count == cap (trim condition is strictly greater)"
    )


# ---------------------------------------------------------------------------
# R2 complement: second poll must ignore _restored_shipments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_second_poll_ignores_restored_shipments(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R2 complement: when self.data is not None (subsequent polls), current_data
    must seed from self.data and ignore _restored_shipments entirely.
    """
    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry
    coordinator, _mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    live_shipment = ShipmentData(
        tracking_number="TN_LIVE", carrier_name="UPS", order_name="#LIVE",
        message_id="LIVE_KEY", email_date=100,
    )
    coordinator.async_set_updated_data({"LIVE_KEY": live_shipment})

    # _restored_shipments has a conflicting key — must NOT appear in result
    coordinator._restored_shipments = {
        "RESTORED_ONLY": ShipmentData(
            tracking_number="TN_RESTORED", carrier_name="DHL", order_name="#RES",
            message_id="RESTORED_ONLY", email_date=50,
        )
    }

    result = await coordinator._async_update_data()

    assert "LIVE_KEY" in result, "live shipment from self.data must carry through"
    assert "RESTORED_ONLY" not in result, (
        "_restored_shipments must be ignored when self.data is not None"
    )


# ---------------------------------------------------------------------------
# DBG-03: debug mode must skip FIFO trim and end-of-poll save
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coordinator_cls,make_entry",
    [
        (
            GmailCoordinator,
            lambda: MockConfigEntry(
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
                options={"debug_mode": True},
                unique_id="debug_user@gmail.com",
            ),
        ),
        (
            ImapCoordinator,
            lambda: MockConfigEntry(
                domain=DOMAIN,
                data={
                    "connection_type": "imap",
                    "imap_host": "imap.example.com",
                    "imap_port": 993,
                    "imap_username": "user@example.com",
                    "imap_password": "app-password",
                    "imap_tls": "ssl",
                    "api_key": "test-parcelapp-key",
                },
                options={"debug_mode": True, "imap_search": 'SUBJECT "shipped"', "poll_interval": 30},
                unique_id="debug_imap@example.com@imap.example.com",
            ),
        ),
    ],
)
async def test_debug_mode_skips_fifo_trim_and_save(
    hass,
    coordinator_cls,
    make_entry,
    request,
) -> None:
    """DBG-03: FIFO trim and end-of-poll store save must be skipped in debug mode.

    If the `if not debug_mode:` guard is removed, _pending_shipments would be
    populated and async_delay_save called, breaking the zero-write contract.
    """
    config_entry = make_entry()
    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    shipment = ShipmentData(
        tracking_number="TN_DBG", carrier_name="UPS", order_name="#DBG",
        message_id="DBG", email_date=1,
    )
    coordinator.async_set_updated_data({"DBG": shipment})
    coordinator._restored_shipments = {}
    coordinator._pending_shipments = {}
    mock_store.async_delay_save.reset_mock()

    await coordinator._async_update_data()

    assert coordinator._pending_shipments == {}, (
        "_pending_shipments must not be assigned in debug mode (DBG-03 zero-write contract)"
    )
    assert not mock_store.async_delay_save.called, (
        "async_delay_save must not be called at poll end in debug mode"
    )


# ---------------------------------------------------------------------------
# R4 guard: cleanup no-op when nothing delivered — store must not be written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls", [GmailCoordinator, ImapCoordinator])
async def test_cleanup_no_op_when_no_deliveries(
    hass,
    mock_config_entry,
    mock_imap_config_entry,
    coordinator_cls,
    request,
) -> None:
    """R4 guard: async_cleanup_delivered must not call the store when removed_ids
    is empty — the `if not removed_ids: return` guard must prevent unnecessary writes.
    """
    from datetime import datetime as _datetime

    config_entry = mock_config_entry if coordinator_cls is GmailCoordinator else mock_imap_config_entry
    coordinator, mock_store = await _setup_coordinator(hass, config_entry, coordinator_cls, request)

    shipment_z = ShipmentData(
        tracking_number="TNZ", carrier_name="FedEx", order_name="#Z",
        message_id="Z", email_date=2,
    )
    coordinator.async_set_updated_data({"Z": shipment_z})
    mock_store.async_delay_save.reset_mock()

    with _patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls:
        mock_parcel_cls.return_value.async_get_deliveries = AsyncMock(
            return_value=[{"tracking_number": "TNZ", "status_code": 1}]  # in-transit
        )
        await coordinator.async_cleanup_delivered(_datetime.now())

    assert not mock_store.async_delay_save.called, (
        "async_delay_save must NOT be called when no shipments are delivered"
    )


# ---------------------------------------------------------------------------
# R3: v2→v3 migration — quota_exhausted_until absent in v2 data
# ---------------------------------------------------------------------------


async def test_migrate_func_v2_missing_quota_exhausted_until(
    store: Shop2ParcelStore,
) -> None:
    """R3: v2→v3 migration must treat absent quota_exhausted_until as None."""
    old_data = {"submitted_tracking_numbers": ["TN1"]}  # no quota_exhausted_until key

    result = await store._async_migrate_func(2, 0, old_data)

    assert result["quota_exhausted_until"] is None, (
        "missing quota_exhausted_until in v2 data must produce None in v3 result"
    )
    assert result["submitted_tracking_numbers"] == ["TN1"]
    assert result["persisted_shipments"] == {}


# ---------------------------------------------------------------------------
# R5 extra: wrong type with all 5 fields present; non-dict outer value
# ---------------------------------------------------------------------------


async def test_load_store_skips_corrupt_entry_wrong_type_correct_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5: Entry with all 5 fields present but wrong type must be skipped with WARNING."""
    coordinator = Shop2ParcelCoordinator.__new__(Shop2ParcelCoordinator)
    mock_store = MagicMock()
    mock_store.async_load = AsyncMock(
        return_value={
            "submitted_tracking_numbers": [],
            "quota_exhausted_until": None,
            "persisted_shipments": {
                "wrong_type_msg": {
                    "tracking_number": "TN123",
                    "carrier_name": "UPS",
                    "order_name": "#1001",
                    "message_id": "wrong_type_msg",
                    "email_date": "not-an-int",  # str instead of int
                },
            },
        }
    )
    mock_store.key = "shop2parcel.test_entry"
    coordinator._store = mock_store
    coordinator._submitted_tracking_numbers = OrderedDict()
    coordinator._quota_exhausted_until = None
    coordinator._pending_shipments = {}
    coordinator._restored_shipments = {}
    coordinator._store_loaded = False

    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        await coordinator._async_load_store()

    assert "wrong_type_msg" not in coordinator._restored_shipments, (
        "entry with wrong field type must be skipped"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("wrong_type_msg" in r.getMessage() for r in warning_records), (
        "WARNING must mention the skipped entry key"
    )


async def test_load_store_non_dict_persisted_shipments_emits_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5 outer guard: non-dict persisted_shipments (e.g. a list) must emit WARNING
    and result in empty _restored_shipments.
    """
    coordinator = Shop2ParcelCoordinator.__new__(Shop2ParcelCoordinator)
    mock_store = MagicMock()
    mock_store.async_load = AsyncMock(
        return_value={
            "submitted_tracking_numbers": [],
            "quota_exhausted_until": None,
            "persisted_shipments": ["not", "a", "dict"],
        }
    )
    mock_store.key = "shop2parcel.test_entry"
    coordinator._store = mock_store
    coordinator._submitted_tracking_numbers = OrderedDict()
    coordinator._quota_exhausted_until = None
    coordinator._pending_shipments = {}
    coordinator._restored_shipments = {}
    coordinator._store_loaded = False

    with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
        await coordinator._async_load_store()

    assert coordinator._restored_shipments == {}, (
        "_restored_shipments must be empty when persisted_shipments is not a dict"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("persisted_shipments" in r.getMessage() for r in warning_records), (
        "WARNING must be emitted when persisted_shipments is not a dict"
    )
