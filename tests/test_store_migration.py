"""Tests for Shop2ParcelStore._async_migrate_func — v1 → v2 migration.

Covers D-01, D-02, D-03 from Phase 10 context.
No HA hass fixture required — tests use Store.__new__ to bypass Store.__init__
and test the migration logic directly on a stub instance.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator, Shop2ParcelStore
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator
from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS


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
    # version=2 is the current; old_major_version=2 (same) → passthrough
    store.version = 2
    store.minor_version = 1
    old_data = {"submitted_tracking_numbers": ["X"], "quota_exhausted_until": None}
    result = await store._async_migrate_func(2, 0, old_data)
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
