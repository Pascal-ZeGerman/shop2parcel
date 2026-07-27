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
# WR-02: placeholder sentinels ("", "Unknown") route to the promotion path
# ---------------------------------------------------------------------------


def test_llm_fills_empty_order_name_without_conflict() -> None:
    """WR-02: Stage-1 order_name='' (the parser's 'not found' sentinel) must be
    PROMOTED by a non-empty Stage-2 value — previously '' was treated as
    authoritative and every such job emitted a spurious stage2_conflict."""
    stage1 = _make_shipment(order_name="")
    result = _make_result(locked={"order_name": "#4711"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.order_name == "#4711"
    assert conflicts == [], "filling an empty Stage-1 field must not emit a conflict"
    assert gate_rejections == []


def test_llm_replaces_unknown_carrier_without_conflict() -> None:
    """WR-02: Stage-1 carrier_name='Unknown' (the parser's carrier sentinel) must
    be replaced by an LLM-identified carrier — previously 'Unknown' won and
    normalize_carrier POSTed 'pholder' even when the LLM knew the carrier."""
    stage1 = _make_shipment(carrier_name="Unknown")
    result = _make_result(locked={"carrier_name": "UPS"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.carrier_name == "UPS"
    assert conflicts == []
    assert gate_rejections == []


def test_unknown_carrier_kept_when_stage2_declines() -> None:
    """WR-02: sentinel values are never downgraded to None — when Stage-2 declines,
    the original Stage-1 sentinel is kept (downstream types are non-Optional str)."""
    stage1 = _make_shipment(carrier_name="Unknown", order_name="")
    result = _make_result(locked={"carrier_name": None, "order_name": None})
    merged, conflicts, _gate = merge_llm_authoritative(stage1, result)
    assert merged.carrier_name == "Unknown"
    assert merged.order_name == ""
    assert conflicts == []


def test_unknown_sentinel_not_special_for_order_name() -> None:
    """WR-02: the 'Unknown' sentinel is carrier-specific — an order_name literally
    equal to 'Unknown' is a real value and still conflicts."""
    stage1 = _make_shipment(order_name="Unknown")
    result = _make_result(locked={"order_name": "#1234"})
    merged, conflicts, _gate = merge_llm_authoritative(stage1, result)
    assert merged.order_name == "Unknown"
    assert len(conflicts) == 1


def test_real_carrier_conflict_still_recorded() -> None:
    """WR-02: conflicts still fire when Stage-1 had a REAL carrier value."""
    stage1 = _make_shipment(carrier_name="USPS")
    result = _make_result(locked={"carrier_name": "UPS"})
    merged, conflicts, _gate = merge_llm_authoritative(stage1, result)
    assert merged.carrier_name == "USPS"
    assert conflicts == [{"field": "carrier_name", "stage1": "USPS", "stage2": "UPS"}]


def test_grounding_none_and_blank_values_pass() -> None:
    """MRG-05: validate_grounding(None, ...) and validate_grounding("  ", ...) have
    nothing to gate — both return (value, True, None) unchanged."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    assert validate_grounding(None, "any prose") == (None, True, None)
    assert validate_grounding("  ", "any prose") == ("  ", True, None)


def test_grounding_no_content_tokens_rejected() -> None:
    """MRG-05: a pure numeric/punctuation value has zero content tokens (numbers
    excluded, alpha-only len>=3) — rejected with reason='no_content_tokens'."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("#1234", "your order 1234 shipped via ups")
    assert value == "#1234"
    assert ok is False
    assert reason == "no_content_tokens"


_TRIGGER_PROSE = (
    "Hi there, your order #1234 has been shipped and is on its way to you. "
    "Your package was shipped via UPS. Tracking number: 1Z999AA10123456784"
)
_BLOOMWILD_PROSE = "Your order from Bloom & Wild has shipped. Letterbox Bouquet arrives soon."
_TARGET_PROSE = "Your order from Target has shipped: Coffee maker included."


def test_grounding_grounded_value_passes() -> None:
    """MRG-05 GOOD case (spike 007 bloomwild): merchant+product tokens present in
    body prose — the gate PASSES."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("Bloom & Wild - Letterbox Bouquet", _BLOOMWILD_PROSE)
    assert value == "Bloom & Wild - Letterbox Bouquet"
    assert ok is True
    assert reason is None


def test_grounding_ungrounded_value_rejected() -> None:
    """MRG-05 BAD case (spike 007 HALLUCINATION_TRIGGER): fabricated merchant/product
    with zero tokens present in body prose — the gate REJECTS with reason='ungrounded'."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("Target - Coffee maker", _TRIGGER_PROSE)
    assert value == "Target - Coffee maker"
    assert ok is False
    assert reason == "ungrounded"


def test_grounding_rejects_coincidental_substring_match() -> None:
    """CR-01 regression: a value token that is merely a SUBSTRING of an unrelated
    word already present in the prose (e.g. 'rack' inside 'tracking') must NOT
    ground the claim -- only a whole-word match counts. Prior to the CR-01 fix,
    `validate_grounding` used plain substring containment
    (`token in source_text.lower()`), so a fabricated 'Rack Room Shoes' merchant
    name spuriously PASSED the gate purely because _TRIGGER_PROSE contains
    'Tracking number: ...'. This must be rejected as ungrounded."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("Rack Room Shoes", _TRIGGER_PROSE)
    assert value == "Rack Room Shoes"
    assert ok is False
    assert reason == "ungrounded"


def test_grounding_platform_conflation_rejected() -> None:
    """MRG-05 BAD case (spike 007 platform conflation): 'shopify' is in
    _BOILERPLATE_DENYLIST and 'your'/'order' are stopwords, so _content_tokens()
    is empty -> reason='no_content_tokens' (fails closed, not 'ungrounded')."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("Shopify - Your order", _TRIGGER_PROSE)
    assert value == "Shopify - Your order"
    assert ok is False
    assert reason == "no_content_tokens"


def test_sender_subject_exclusion_body_only_rejects() -> None:
    """SC-2 (spike 012's confirmed blind-spot fix): a 'Customer Care'-style value
    whose tokens appear only in envelope context (never in body-only prose) is
    rejected — sender/subject header tokens are never grounding evidence."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    value, ok, reason = validate_grounding("Customer Care - Your order #1234", _TRIGGER_PROSE)
    assert value == "Customer Care - Your order #1234"
    assert ok is False
    assert reason == "ungrounded"


def test_sender_subject_exclusion_enriched_would_spuriously_pass() -> None:
    """SC-2 companion (spike 012): documents WHY body-only prose is required as
    source_text — the SAME value spuriously PASSES if an enriched string
    containing 'Customer Care' (as it would appear in an Email From: header)
    were passed as source_text instead of body-only prose."""
    from custom_components.shop2parcel.merge import validate_grounding  # noqa: PLC0415

    enriched = f'Email From: "Customer Care" <updates@example.com>\n\n{_TRIGGER_PROSE}'
    value, ok, _reason = validate_grounding("Customer Care - Your order #1234", enriched)
    assert value == "Customer Care - Your order #1234"
    assert ok is True, (
        "Documents the blind spot: enriched source_text (header tokens) spuriously "
        "grounds the value — this is exactly why source_text MUST be body-only prose."
    )


def test_grounding_content_tokens_extraction() -> None:
    """MRG-05: _content_tokens() lowercases, keeps alpha tokens len>=3, strips
    stopwords/denylist. All-stopword input yields an empty set."""
    from custom_components.shop2parcel.merge import _content_tokens  # noqa: PLC0415

    assert _content_tokens("Bloom & Wild") == {"bloom", "wild"}
    assert _content_tokens("Your order shipped") == set()


def test_grounding_wrapper_returns_four_tuple() -> None:
    """MRG-05: merge_llm_authoritative_with_grounding returns a 4-tuple
    (merged, conflicts, gate_rejections, grounding_rejections) — a DELIBERATE
    deviation from the spike blueprint's 3-tuple (RESEARCH.md Pitfall 3)."""
    from custom_components.shop2parcel.merge import (  # noqa: PLC0415
        merge_llm_authoritative_with_grounding,
    )

    stage1 = _make_shipment()
    result = _make_result(locked={})
    returned = merge_llm_authoritative_with_grounding(stage1, result, "")
    assert len(returned) == 4


def test_grounding_wrapper_discards_ungrounded_order_summary() -> None:
    """MRG-05 discard-not-conflict: Stage-1 order_summary is None (never populated
    by Stage-1), Stage-2 fabricates 'Target - Coffee maker', source_text is the
    ungrounded trigger prose -> the wrapper discards it back to the Stage-1
    sentinel (None) and records a grounding rejection distinct from
    gate_rejections."""
    from custom_components.shop2parcel.merge import (  # noqa: PLC0415
        merge_llm_authoritative_with_grounding,
    )

    stage1 = _make_shipment()
    assert stage1.order_summary is None
    result = _make_result(locked={"order_summary": "Target - Coffee maker"})
    merged, _conflicts, gate_rejections, grounding_rejections = (
        merge_llm_authoritative_with_grounding(stage1, result, _TRIGGER_PROSE)
    )
    assert merged.order_summary is None
    assert grounding_rejections == [
        {"field": "order_summary", "clean": "Target - Coffee maker", "reason": "ungrounded"}
    ]
    assert gate_rejections == []


def test_grounding_wrapper_keeps_grounded_order_summary() -> None:
    """MRG-05: same fabricated-looking value, but source_text now contains the
    merchant/product tokens -> the wrapper keeps the Stage-2 value; no
    grounding rejection recorded."""
    from custom_components.shop2parcel.merge import (  # noqa: PLC0415
        merge_llm_authoritative_with_grounding,
    )

    stage1 = _make_shipment()
    result = _make_result(locked={"order_summary": "Target - Coffee maker"})
    merged, _conflicts, _gate_rejections, grounding_rejections = (
        merge_llm_authoritative_with_grounding(stage1, result, _TARGET_PROSE)
    )
    assert merged.order_summary == "Target - Coffee maker"
    assert grounding_rejections == []


def test_grounding_wrapper_noop_on_real_stage1() -> None:
    """MRG-05: the wrapper only acts when _stage1_missing() is True. Stage-1 has a
    REAL order_name ('#5678') -> the wrapper does not touch order_name at all;
    MRG-03's existing conflict handling inside merge_llm_authoritative governs
    it (Stage-1 wins, conflict recorded), and grounding_rejections stays empty
    for that field."""
    from custom_components.shop2parcel.merge import (  # noqa: PLC0415
        merge_llm_authoritative_with_grounding,
    )

    stage1 = _make_shipment(order_name="#5678")
    result = _make_result(locked={"order_name": "Fabricated Brand"})
    merged, conflicts, _gate_rejections, grounding_rejections = (
        merge_llm_authoritative_with_grounding(stage1, result, _TRIGGER_PROSE)
    )
    assert merged.order_name == "#5678"
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "order_name"
    assert grounding_rejections == []


def test_empty_stage1_tracking_number_gets_gated_promotion() -> None:
    """WR-02 + MRG-04: an (anomalous) empty Stage-1 tracking_number routes to the
    promotion path, where the strict carrier-format gate still applies."""
    stage1 = _make_shipment(tracking_number="")
    # Valid UPS format → gate passes → clean form promoted.
    result = _make_result(locked={"tracking_number": "1Z999AA10123456784"})
    merged, conflicts, gate_rejections = merge_llm_authoritative(stage1, result)
    assert merged.tracking_number == "1Z999AA10123456784"
    assert conflicts == []
    assert gate_rejections == []
    # Non-carrier string → gate rejects → sentinel kept, rejection recorded.
    result_bad = _make_result(locked={"tracking_number": "NOTATRACKINGNO"})
    merged_bad, conflicts_bad, gate_rejections_bad = merge_llm_authoritative(stage1, result_bad)
    assert merged_bad.tracking_number == ""
    assert conflicts_bad == []
    assert len(gate_rejections_bad) == 1


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
