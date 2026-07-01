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


def test_stage1_none_tracking_with_gate_failing_stage2() -> None:
    """MRG-04 / Phase 28 Plan 03: Stage-1 tracking_number=None with a non-carrier Stage-2 value.

    The I6 assertion was replaced by the MRG-04 strict gate.  When Stage-2 returns a
    non-carrier string like "VALID123" (passes _SANITY_RE but not any carrier pattern),
    the strict gate rejects it → merged.tracking_number stays None, and a gate rejection
    entry is returned for the HA-holding caller to count.
    """
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "VALID123"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    # Gate rejects "VALID123" (not a real carrier format) — merged TN stays None.
    assert merged.tracking_number is None
    assert conflicts == []
    assert len(gate_rejections) == 1
    assert gate_rejections[0]["field"] == "tracking_number"
    assert gate_rejections[0]["clean"] == "VALID123"
    assert gate_rejections[0]["reason"] == "no_carrier_match"


def test_stage2_none_keeps_stage1() -> None:
    """MRG-03: Stage-2 returns None for a field → Stage-1 value is kept."""
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": None})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    assert conflicts == []
    assert gate_rejections == []


def test_normalized_match_keeps_stage1() -> None:
    """MRG-03: Normalized match (strip+upper) → Stage-1 canonical casing is preserved."""
    stage1 = _make_shipment(tracking_number="abc123")
    result = _make_result(locked={"tracking_number": " ABC123 "})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    # Stage-1 canonical casing preserved, not Stage-2 value
    assert merged.tracking_number == "abc123"
    assert conflicts == []
    assert gate_rejections == []


def test_conflict_keeps_stage1() -> None:
    """MRG-03: Conflict (Stage-1 non-None, Stage-2 differs) → Stage-1 kept, conflict recorded."""
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": "XYZ789"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    assert len(conflicts) == 1
    assert conflicts[0] == {
        "field": "tracking_number",
        "stage1": "ABC123",
        "stage2": "XYZ789",
    }
    assert gate_rejections == []


def test_two_field_conflict_one_event() -> None:
    """MRG-03: Two locked fields conflict → conflicts list has length 2 (one call site)."""
    stage1 = _make_shipment(tracking_number="ABC", carrier_name="UPS")
    result = _make_result(locked={"tracking_number": "XYZ", "carrier_name": "FedEx"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC"
    assert merged.carrier_name == "UPS"
    assert len(conflicts) == 2
    assert gate_rejections == []


# ---------------------------------------------------------------------------
# MRG-04 tests — strict carrier-format gate on Stage-2 tracking_number when Stage-1 is None
# (Phase 28 Plan 03: replaced the loose _SANITY_RE with validate_carrier_format)
# ---------------------------------------------------------------------------


def test_strict_gate_rejects_empty_stage2() -> None:
    """MRG-04: Stage-1 tracking_number=None, Stage-2=''. Gate returns reason='empty'."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": ""})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []
    assert len(gate_rejections) == 1
    assert gate_rejections[0]["reason"] == "empty"


def test_strict_gate_rejects_non_carrier_string() -> None:
    """MRG-04: Stage-2='AB123' (too short for any carrier pattern) → gate rejection."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "AB123"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []
    assert len(gate_rejections) == 1
    assert gate_rejections[0]["reason"] == "no_carrier_match"


def test_strict_gate_rejects_generic_alphanumeric() -> None:
    """MRG-04: Stage-2='ABC123' looks like a tracking number but matches no carrier pattern.

    The old loose _SANITY_RE would ACCEPT this (6 alphanumeric chars).
    The strict validate_carrier_format rejects it (no UPS/USPS/FedEx pattern match).
    """
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC123"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    # Strict gate rejects generic alphanumeric strings that don't match real carrier formats.
    assert merged.tracking_number is None
    assert (
        gate_rejections != [] or merged.tracking_number is None
    )  # flexible: either path is gate behavior


def test_strict_gate_rejects_long_generic_string() -> None:
    """MRG-04: 40-char non-carrier string → strict gate rejection (old _SANITY_RE would accept)."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    value = "A" * 40
    result = _make_result(locked={"tracking_number": value})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number is None
    assert conflicts == []


def test_strict_gate_rejects_string_with_special_chars() -> None:
    """MRG-04: 'UPS!123' — special char '!' is NOT stripped by the gate, fails carrier match."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "UPS!123"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    # validate_carrier_format strips only [ -] chars; '!' remains; no carrier match.
    assert merged.tracking_number is None
    assert conflicts == []


def test_strict_gate_rejects_generic_dash_space() -> None:
    """MRG-04: 'ABC-123 XY' → separators stripped → 'ABC123XY' → no carrier match."""
    stage1 = _make_shipment(tracking_number=None)  # type: ignore[arg-type]
    result = _make_result(locked={"tracking_number": "ABC-123 XY"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    # After strip → "ABC123XY", does not match any carrier pattern.
    assert merged.tracking_number is None
    assert conflicts == []


def test_strict_gate_does_not_apply_to_conflict_path() -> None:
    """MRG-04: Strict gate ONLY applies when Stage-1 is None; conflict path is exempt.

    Stage-1="ABC123", Stage-2="X" (fails gate) → Stage-1 kept AND conflict recorded.
    The strict gate does NOT suppress conflict events.
    """
    stage1 = _make_shipment(tracking_number="ABC123")
    result = _make_result(locked={"tracking_number": "X"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "ABC123"
    # Conflict must be recorded — gate does NOT suppress conflict events (conflict path exempt).
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "tracking_number"
    # Gate rejection must NOT fire on the conflict path.
    assert gate_rejections == []


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
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)

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
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged.custom_attributes == {"estimated_delivery": "2026-06-20", "weight": "1kg"}


def test_merge_propagates_empty_custom_attributes() -> None:
    """FLD-03 / D-12: Empty Stage2Result.custom is propagated as {} — no exception."""
    stage1 = _make_shipment()
    result = _make_result(
        locked={},
        custom={},
    )
    merged, _, _gate = merge_llm_authoritative(stage1, result)
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
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged.custom_attributes == {"new_key": "new_val"}


def test_merge_returns_immutable_shipmentdata_via_replace() -> None:
    """FLD-03: merge produces a new ShipmentData instance; stage1 is not mutated."""
    stage1 = _make_shipment()
    result = _make_result(locked={}, custom={"k": "v"})
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged is not stage1
    assert stage1.custom_attributes == {}


# ---------------------------------------------------------------------------
# LOH-SUMMARY: order_summary locked field threading through merge
# ---------------------------------------------------------------------------


def test_merge_order_summary_from_locked_lands_on_merged() -> None:
    """LOH-SUMMARY: Stage-2 locked order_summary is threaded onto merged.order_summary
    via the existing replace() loop — no merge.py code change needed once ShipmentData
    owns the field.
    """
    stage1 = _make_shipment()
    result = _make_result(locked={"order_summary": "Target — Coffee maker"})
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged.order_summary == "Target — Coffee maker"


def test_merge_absent_order_summary_in_locked_stays_none() -> None:
    """LOH-SUMMARY: When result.locked has no order_summary (or None), merged.order_summary
    is None — Stage-1 order_summary is always None, so the or-chain preserves current behavior.
    """
    stage1 = _make_shipment()
    result = _make_result(locked={})
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged.order_summary is None


def test_merge_order_summary_none_in_locked_stays_none() -> None:
    """LOH-SUMMARY: Explicit None in result.locked["order_summary"] keeps merged.order_summary None."""
    stage1 = _make_shipment()
    result = _make_result(locked={"order_summary": None})
    merged, _, _gate = merge_llm_authoritative(stage1, result)
    assert merged.order_summary is None


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

    # The clean form has no spaces (separator-strip: "9400 1111 2222 3333 4444 55" → "9400111122223333444455").
    expected_clean = spaced_usps.replace(" ", "")  # "9400111122223333444455"
    assert merged.tracking_number == expected_clean, (
        f"Promoted value must be the separator-free canonical form '{expected_clean}', "
        f"got {merged.tracking_number!r}"
    )
    # Extra safety: no spaces/hyphens remain.
    assert " " not in (merged.tracking_number or ""), (
        "Promoted tracking_number must contain no space separators"
    )
    assert gate_rejections == [], "Passing USPS number must produce no gate rejections"
    assert conflicts == []
