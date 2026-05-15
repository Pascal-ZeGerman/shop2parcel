"""Tests for Shop2ParcelStore._async_migrate_func — v1 → v2 migration.

Covers D-01, D-02, D-03 from Phase 10 context.
No HA hass fixture required — tests use Store.__new__ to bypass Store.__init__
and test the migration logic directly on a stub instance.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.shop2parcel.coordinator import Shop2ParcelStore


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
    """Non-v1 major versions must be returned unchanged (forward compatibility guard)."""
    old_data = {"submitted_tracking_numbers": ["X"], "quota_exhausted_until": None}
    result = await store._async_migrate_func(2, 0, old_data)
    assert result == old_data
