"""Tests for Shop2Parcel binary_sensor.py — Phase 26 Plan 03.

Coverage:
- P26-ENT-04: ProblemBinarySensor (PROBLEM device class; three-condition is_on).
- P26-REMOVE-02: HasActiveShipmentsBinarySensor is no longer registered.
- M6A-01: EmailProcessingActiveBinarySensor (DIAGNOSTIC category; reads email_processing_active).
"""

from __future__ import annotations

import time

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.binary_sensor import EmailProcessingActiveBinarySensor
from custom_components.shop2parcel.const import DOMAIN, STAGE2_NOTIFY_THRESHOLD
from custom_components.shop2parcel.coordinator import Stage2Job
from tests.conftest import setup_coordinator_with_data as _setup_with_data


def _make_shipment(message_id: str, tracking: str) -> ShipmentData:
    return ShipmentData(
        tracking_number=tracking,
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1745452800,
    )


# ---------------------------------------------------------------------------
# Phase 26 Plan 03: ProblemBinarySensor tests
# ---------------------------------------------------------------------------


async def test_problem_sensor_on_failure_streak(hass, mock_config_entry):
    """P26-ENT-04: ProblemBinarySensor.is_on True when stage2_consecutive_failures >= threshold."""
    from custom_components.shop2parcel.binary_sensor import ProblemBinarySensor

    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = ProblemBinarySensor(coordinator, mock_config_entry)

    # At rest: 0 failures, quota not exhausted, no pending posts -> off
    coordinator._stage2_consecutive_failures = 0
    coordinator._quota_exhausted_until = None
    coordinator._pending_posts = {}
    assert sensor.is_on is False

    # 2 failures (below threshold) + no quota/pending issues -> still off
    coordinator._stage2_consecutive_failures = STAGE2_NOTIFY_THRESHOLD - 1
    assert sensor.is_on is False

    # 3 failures (at threshold) -> on
    coordinator._stage2_consecutive_failures = STAGE2_NOTIFY_THRESHOLD
    assert sensor.is_on is True

    # More than threshold -> still on
    coordinator._stage2_consecutive_failures = STAGE2_NOTIFY_THRESHOLD + 5
    assert sensor.is_on is True


async def test_problem_sensor_on_quota_exhausted(hass, mock_config_entry):
    """P26-ENT-04: ProblemBinarySensor.is_on True when quota_is_exhausted, even with 0 failures."""
    from custom_components.shop2parcel.binary_sensor import ProblemBinarySensor

    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = ProblemBinarySensor(coordinator, mock_config_entry)

    # 0 consecutive failures, no pending posts
    coordinator._stage2_consecutive_failures = 0
    coordinator._pending_posts = {}

    # quota not exhausted -> off
    coordinator._quota_exhausted_until = None
    assert sensor.is_on is False

    # quota exhausted (future epoch) -> on
    coordinator._quota_exhausted_until = int(time.time()) + 3600
    assert sensor.is_on is True

    # quota window elapsed (past epoch) -> off (quota_is_exhausted returns False)
    coordinator._quota_exhausted_until = int(time.time()) - 1
    assert sensor.is_on is False


async def test_problem_sensor_on_pending_posts_backlog(hass, mock_config_entry):
    """P26-ENT-04 (finding 1): ProblemBinarySensor.is_on True when a pending-posts
    backlog exists, even with 0 failures and quota not exhausted.

    Regression: is_on previously implemented only 2 of the 3 documented conditions
    (failure-streak, quota), silently omitting pending_posts_depth > 0 — so a stuck
    un-forwarded backlog read as healthy (Problem: off).
    """
    from custom_components.shop2parcel.binary_sensor import ProblemBinarySensor

    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = ProblemBinarySensor(coordinator, mock_config_entry)

    # No failures, quota healthy, but a quota-deferred shipment is stuck pending.
    coordinator._stage2_consecutive_failures = 0
    coordinator._quota_exhausted_until = None
    coordinator._pending_posts = {"msg1": _make_shipment("msg1", "1Z-PENDING")}
    assert coordinator.pending_posts_depth == 1
    assert sensor.is_on is True, (
        "Problem sensor must be ON when an un-forwarded pending-posts backlog exists "
        "(3rd documented condition: pending_posts_depth > 0)."
    )

    # Drain the backlog → back to healthy.
    coordinator._pending_posts = {}
    assert sensor.is_on is False


async def test_has_active_shipments_not_registered(hass, mock_config_entry):
    """P26-REMOVE-02: No entity with suffix 'has_active_shipments' after setup.
    The 'problem' and 'email_processing_active' binary sensors must exist.
    """
    await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)

    uid_has_active = f"{DOMAIN}_{mock_config_entry.entry_id}_has_active_shipments"
    uid_problem = f"{DOMAIN}_{mock_config_entry.entry_id}_problem"
    uid_email_active = f"{DOMAIN}_{mock_config_entry.entry_id}_email_processing_active"

    uids = {e.unique_id for e in entries}
    assert uid_has_active not in uids, (
        f"HasActiveShipmentsBinarySensor {uid_has_active!r} must NOT be registered after Phase 26 Plan 03."
    )
    assert uid_problem in uids, (
        f"ProblemBinarySensor {uid_problem!r} must be registered. Found: {sorted(uids)}"
    )
    assert uid_email_active in uids, (
        f"EmailProcessingActiveBinarySensor {uid_email_active!r} must be registered."
    )


# ---------------------------------------------------------------------------
# M6A-01: EmailProcessingActiveBinarySensor tests (retained)
# ---------------------------------------------------------------------------


async def test_email_processing_sensor_registered_and_off_at_rest(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor registered at setup; off when idle."""
    await _setup_with_data(hass, mock_config_entry, {})
    registry = er.async_get(hass)
    entries = registry.entities.get_entries_for_config_entry_id(mock_config_entry.entry_id)
    uid = f"{DOMAIN}_{mock_config_entry.entry_id}_email_processing_active"
    entry = next((e for e in entries if e.unique_id == uid), None)
    assert entry is not None, (
        f"email_processing_active sensor {uid!r} not found in entity registry. "
        f"Found: {[e.unique_id for e in entries]}"
    )
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.state == "off"


async def test_email_processing_sensor_on_when_active(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor.is_on tracks coordinator.email_processing_active.

    Phase 32 cutover: stage2_queue_depth (and email_processing_active through it) now
    reads the hub's per-account in-flight count (hub.inflight_count) instead of the
    retired per-entry _stage2_queue.qsize() — drive it via a real hub.enqueue()/
    _release_inflight() round trip rather than seeding a local asyncio.Queue.
    """
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)
    entry_id = mock_config_entry.entry_id

    # At rest: both sources off
    assert coordinator._poll_in_progress is False
    assert coordinator.stage2_queue_depth == 0
    assert sensor.is_on is False

    # _poll_in_progress True → sensor on
    coordinator._poll_in_progress = True
    assert sensor.is_on is True

    # Reset flag but seed the hub's per-account in-flight count → sensor stays on
    coordinator._poll_in_progress = False
    job = Stage2Job(
        storage_key="sentinel",
        normalized_tn="sentinel",
        shipment=_make_shipment("sentinel-msg", "sentinel"),
        html_body="<html/>",
        message_id="sentinel-msg",
        meta={"subject": "sentinel", "from": "sentinel@example.com"},
        entry_id=entry_id,
    )
    coordinator._hub.enqueue(job)
    assert coordinator.stage2_queue_depth == 1
    assert sensor.is_on is True

    # Release the in-flight slot and reset flag → sensor off
    coordinator._hub._release_inflight(entry_id, "sentinel")
    assert coordinator.stage2_queue_depth == 0
    assert sensor.is_on is False


async def test_email_processing_sensor_is_diagnostic_category(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor entity_category is DIAGNOSTIC."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


async def test_email_processing_sensor_unique_id_format(hass, mock_config_entry):
    """M6A-01: EmailProcessingActiveBinarySensor unique_id uses the standard suffix."""
    coordinator = await _setup_with_data(hass, mock_config_entry, {})
    sensor = EmailProcessingActiveBinarySensor(coordinator, mock_config_entry)
    assert (
        sensor._attr_unique_id == f"{DOMAIN}_{mock_config_entry.entry_id}_email_processing_active"
    )
