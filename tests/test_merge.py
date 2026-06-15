"""Phase 20-01 RED: test suite for merge_llm_authoritative (MRG-03, MRG-04).

All tests FAIL until Task 2 creates custom_components/shop2parcel/merge.py.
"""

from __future__ import annotations

import sys

import pytest

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.extractors.types import Stage2Result
from custom_components.shop2parcel.merge import merge_llm_authoritative

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shipment(
    tracking_number: str = "ABC123",
    carrier_name: str = "UPS",
    order_name: str = "#1",
    message_id: str = "msg1",
    email_date: int = 1700000000,
) -> ShipmentData:
    return ShipmentData(
        tracking_number=tracking_number,
        carrier_name=carrier_name,
        order_name=order_name,
        message_id=message_id,
        email_date=email_date,
    )


def _make_result(
    locked: dict | None = None,
    custom: dict | None = None,
    passes_used: int = 1,
    latency_ms: float = 10.0,
) -> Stage2Result:
    return Stage2Result(
        locked=locked if locked is not None else {},
        custom=custom if custom is not None else {},
        passes_used=passes_used,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# MRG-03 tests
# ---------------------------------------------------------------------------


def test_stage1_none_stage2_valid_wins() -> None:
    """MRG-03: Stage-1 tracking_number=None → Stage-2 value is promoted."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "VALID123"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "VALID123"
    assert conflicts == []


def test_stage2_none_keeps_stage1() -> None:
    """MRG-03: Stage-2 returns None for a field → Stage-1 value is kept."""
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": None})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    assert conflicts == []


def test_normalized_match_keeps_stage1() -> None:
    """MRG-03: Normalized match (strip+upper) → Stage-1 canonical casing is preserved."""
    stage1 = _make_shipment(tracking_number="abc123")
    result = _make_result(locked={"tracking_number": " ABC123 "})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    # Stage-1 canonical casing preserved, not Stage-2 value
    assert merged.tracking_number == "abc123"
    assert conflicts == []


def test_conflict_keeps_stage1() -> None:
    """MRG-03: Conflict (Stage-1 non-None, Stage-2 differs) → Stage-1 kept, conflict recorded."""
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": "XYZ789"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    assert len(conflicts) == 1
    assert conflicts[0] == {
        "field": "tracking_number",
        "stage1": "ABC123",
        "stage2": "XYZ789",
    }


def test_two_field_conflict_one_event() -> None:
    """MRG-03: Two locked fields conflict → conflicts list has length 2 (one call site)."""
    stage1 = _make_shipment(tracking_number="ABC", carrier_name="UPS")
    result = _make_result(locked={"tracking_number": "XYZ", "carrier_name": "FedEx"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC"
    assert merged.carrier_name == "UPS"
    assert len(conflicts) == 2


# ---------------------------------------------------------------------------
# MRG-04 tests — sanity check on Stage-2 tracking_number when Stage-1 is None
# ---------------------------------------------------------------------------


def test_sanity_check_rejects_invalid_empty() -> None:
    """MRG-04: Empty string Stage-2 tracking_number → silently discarded, no conflict."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": ""})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []


def test_sanity_check_rejects_5_chars() -> None:
    """MRG-04: 5-char tracking_number (below 6-char minimum) → discarded."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "AB123"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []


def test_sanity_check_accepts_6_chars() -> None:
    """MRG-04: 6-char alphanumeric tracking_number → accepted."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC123"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    assert conflicts == []


def test_sanity_check_accepts_40_chars() -> None:
    """MRG-04: 40-char alphanumeric tracking_number (maximum) → accepted."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    value = "A" * 40
    result = _make_result(locked={"tracking_number": value})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == value
    assert conflicts == []


def test_sanity_check_rejects_41_chars() -> None:
    """MRG-04: 41-char tracking_number (above 40-char maximum) → discarded."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    value = "A" * 41
    result = _make_result(locked={"tracking_number": value})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []


def test_sanity_check_rejects_special_chars() -> None:
    """MRG-04: Tracking number with special chars (!) → discarded."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "UPS!123"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []


def test_sanity_check_accepts_dash_and_space() -> None:
    """MRG-04: Tracking number with dashes and spaces → accepted."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC-123 XY"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC-123 XY"
    assert conflicts == []


def test_sanity_check_does_not_apply_to_conflict_path() -> None:
    """MRG-04: Sanity check ONLY applies when Stage-1 is None; conflict path is exempt.

    Stage-1="ABC123", Stage-2="X" (5 chars, fails sanity) → Stage-1 kept AND
    conflict recorded. The sanity gate does not silently discard the conflict.
    """
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": "X"})
    merged, conflicts = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    # Conflict must be recorded — sanity check does NOT suppress conflict events
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "tracking_number"


# ---------------------------------------------------------------------------
# D-02 and immutability tests
# ---------------------------------------------------------------------------


def test_no_ha_imports_in_merge_module() -> None:
    """D-02: merge.py must not import any homeassistant module."""
    import custom_components.shop2parcel.merge as merge_mod  # noqa: PLC0415

    # Inspect the module's source via __file__
    import pathlib  # noqa: PLC0415

    source = pathlib.Path(merge_mod.__file__).read_text()
    assert "homeassistant" not in source, (
        "merge.py must not import homeassistant (D-02 violation). "
        "Found 'homeassistant' in merge.py source."
    )


def test_returns_new_shipmentdata() -> None:
    """merge_llm_authoritative returns a NEW ShipmentData; stage1 is not mutated.

    message_id and email_date from Stage-1 are preserved in the merged result.
    """
    stage1 = _make_shipment(
        tracking_number="ABC123",
        carrier_name="UPS",
        order_name="#1",
        message_id="original-msg",
        email_date=1700000000,
    )
    result = _make_result(locked={"tracking_number": None, "carrier_name": None})
    merged, conflicts = merge_llm_authoritative(stage1, result)

    # Must be a different object
    assert merged is not stage1

    # Stage-1 metadata fields preserved
    assert merged.message_id == "original-msg"
    assert merged.email_date == 1700000000

    # Stage-1 not mutated
    assert stage1.tracking_number == "ABC123"
    assert conflicts == []
