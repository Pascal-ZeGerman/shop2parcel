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


def test_stage1_none_tracking_raises_assertion() -> None:
    """I6 precondition: Stage-1 tracking_number=None must raise AssertionError.

    Coordinators only enqueue emails with a resolved Stage-1 tracking number,
    so merge_llm_authoritative asserts non-None upfront (I6 contract).
    """
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "VALID123"})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


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
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition.

    The MRG-04 sanity gate for tracking_number promotion (Stage-1 None → Stage-2 wins)
    is dead code in production because the coordinator only enqueues emails with a
    resolved Stage-1 tracking number. The I6 assert surfaces violations immediately.
    """
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": ""})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_rejects_5_chars() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "AB123"})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_accepts_6_chars() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC123"})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_accepts_40_chars() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    value = "A" * 40
    result = _make_result(locked={"tracking_number": value})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_rejects_41_chars() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    value = "A" * 41
    result = _make_result(locked={"tracking_number": value})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_rejects_special_chars() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "UPS!123"})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


def test_sanity_check_accepts_dash_and_space() -> None:
    """MRG-04 / I6: Stage-1 tracking_number=None raises AssertionError per I6 precondition."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC-123 XY"})
    with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
        merge_llm_authoritative(stage1, result)


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
    # Inspect the module's source via __file__
    import pathlib  # noqa: PLC0415

    import custom_components.shop2parcel.merge as merge_mod  # noqa: PLC0415

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


# ---------------------------------------------------------------------------
# FLD-03 tests — custom_attributes propagation (Phase 21 Plan 01)
# ---------------------------------------------------------------------------


def test_merge_propagates_custom_attributes_when_non_empty() -> None:
    """FLD-03 / D-12: Non-empty Stage2Result.custom is propagated to merged.custom_attributes."""
    stage1 = _make_shipment()
    result = _make_result(
        locked={"tracking_number": None, "carrier_name": None, "order_name": None},
        custom={"estimated_delivery": "2026-06-20", "weight": "1kg"},
    )
    merged, _ = merge_llm_authoritative(stage1, result)
    assert merged.custom_attributes == {"estimated_delivery": "2026-06-20", "weight": "1kg"}


def test_merge_propagates_empty_custom_attributes() -> None:
    """FLD-03 / D-12: Empty Stage2Result.custom is propagated as {} — no exception."""
    stage1 = _make_shipment()
    result = _make_result(
        locked={},
        custom={},
    )
    merged, _ = merge_llm_authoritative(stage1, result)
    assert merged.custom_attributes == {}


def test_merge_overwrites_existing_custom_attributes_on_stage1() -> None:
    """FLD-03 / D-12: Stage-2 custom overwrites existing Stage-1 custom_attributes unconditionally."""
    stage1 = ShipmentData(
        tracking_number="ABC123",
        carrier_name="UPS",
        order_name="#1",
        message_id="msg1",
        email_date=1700000000,
        custom_attributes={"old_key": "old_val"},
    )
    result = _make_result(
        locked={},
        custom={"new_key": "new_val"},
    )
    merged, _ = merge_llm_authoritative(stage1, result)
    assert merged.custom_attributes == {"new_key": "new_val"}


def test_merge_returns_immutable_shipmentdata_via_replace() -> None:
    """FLD-03: merge produces a new ShipmentData instance; stage1 is not mutated."""
    stage1 = _make_shipment()
    result = _make_result(locked={}, custom={"k": "v"})
    merged, _ = merge_llm_authoritative(stage1, result)
    assert merged is not stage1
    assert stage1.custom_attributes == {}


def test_shipmentdata_default_custom_attributes_is_empty_dict() -> None:
    """FLD-03 Pitfall 1: Positional 5-arg ShipmentData construction still works; default is {}."""
    s = ShipmentData("1Z" + "A" * 16, "UPS", "#1234", "msg1", 1700000000)
    assert s.custom_attributes == {}


# ---------------------------------------------------------------------------
# Phase 28 Plan 03 RED: MRG-04 strict carrier-format gate tests
# ---------------------------------------------------------------------------
# These tests FAIL until Task 2 (GREEN) because:
# (a) merge_llm_authoritative currently returns a 2-tuple, not 3-tuple
# (b) the strict validate_carrier_format gate is not yet wired into MRG-04
# (c) the I6 assertion blocks the Stage-1-None promotion path
# ---------------------------------------------------------------------------


def test_mrg04_strict_gate_rejects_order_number() -> None:
    """R1/R2: ORDER-12345 on the Stage-1-None promotion path is rejected by validate_carrier_format.

    The merged tracking_number must stay None (not promoted), no conflict entry is emitted,
    and a gate_rejections signal is returned so the caller can count the rejection.
    RED: fails because merge_llm_authoritative returns a 2-tuple and _SANITY_RE
    (not the strict gate) is active — ORDER-12345 passes _SANITY_RE.
    """
    # Use a shipment with a valid non-None tracking_number for all other fields.
    # We are testing Stage-2 returning a BAD tracking_number on a job where stage1
    # tracking_number is non-None but the Stage-2 proposes a gate-failing override.
    # In the new MRG-04 design: when stage1.tracking_number IS non-None and stage2.tracking_number
    # is a gate-failing value, the stage1 value is preserved AND a gate rejection is signaled.
    # To exercise the pure "promotion path" (stage1 tn=None), the I6 assert must be relaxed.
    # This test is written for the GREEN state: stage1.tracking_number=None, stage2="ORDER-12345".
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ORDER-12345"})

    # RED: will fail with AssertionError("stage1.tracking_number must be non-None") before
    # even reaching the 3-tuple unpacking.  That IS the expected RED failure.
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)  # type: ignore[misc]

    assert merged.tracking_number is None, (
        "Carrier-format gate must discard ORDER-12345 — merged tracking_number stays None"
    )
    assert not any(c.get("field") == "tracking_number" for c in conflicts), (
        "A gate-rejected value must NOT generate a conflict entry"
    )
    assert len(gate_rejections) == 1, "Exactly one gate rejection must be signaled"
    assert gate_rejections[0]["clean"] == "ORDER12345"
    assert gate_rejections[0]["reason"] == "no_carrier_match"


def test_mrg04_strict_gate_passes_usps_with_separators() -> None:
    """R2/D-03: A USPS number with internal spaces on the promotion path passes the strict gate.

    The promoted tracking_number must be the clean separator-free canonical form (no spaces).
    RED: fails because merge_llm_authoritative returns a 2-tuple and stage1.tracking_number=None
    triggers the AssertionError before the gate is reached.
    """
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    # USPS domestic IMpb — passes _looks_like_tracking() after separator strip.
    spaced_usps = "9400 1111 2222 3333 4444 55"
    result = _make_result(locked={"tracking_number": spaced_usps})

    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)  # type: ignore[misc]

    # The clean form has no spaces.
    assert merged.tracking_number == "940011112222333344445"[:-1] + "5", (
        "Promoted value must be the separator-free canonical form"
    )
    # More explicit assertion: no spaces/hyphens remain.
    assert " " not in (merged.tracking_number or ""), (
        "Promoted tracking_number must contain no space separators"
    )
    assert "-" not in (merged.tracking_number or ""), (
        "Promoted tracking_number must contain no hyphen separators"
    )
    assert gate_rejections == [], "Passing USPS number must produce no gate rejections"
    assert conflicts == []
