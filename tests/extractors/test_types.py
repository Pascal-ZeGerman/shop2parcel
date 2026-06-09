"""Tests for extractors.types.Stage2Result — frozen dataclass invariants.

Mirrors tests/api/test_email_parser.py::test_shipment_data_is_dataclass /
test_shipment_data_slots and adds a frozen-mutation negative test
(pytest.raises(dataclasses.FrozenInstanceError)). The four D-04 fields are
asserted exactly — no extras, no omissions.

No HA imports (D-01/D-03 extended to extractors/).
"""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.shop2parcel.extractors.types import Stage2Result


def test_stage2result_is_frozen_dataclass() -> None:
    """Stage2Result must be a frozen, slotted dataclass (D-04)."""
    instance = Stage2Result(
        locked={},
        custom={},
        passes_used=1,
        latency_ms=0.0,
    )
    assert dataclasses.is_dataclass(Stage2Result)
    assert hasattr(Stage2Result, "__dataclass_fields__")
    assert hasattr(instance, "__slots__")
    # frozen=True forbids attribute reassignment
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.passes_used = 2  # type: ignore[misc]


def test_stage2result_fields() -> None:
    """__dataclass_fields__ keys must equal the 4 D-04 field names exactly."""
    assert set(Stage2Result.__dataclass_fields__.keys()) == {
        "locked",
        "custom",
        "passes_used",
        "latency_ms",
    }


def test_stage2result_construction_requires_all_fields() -> None:
    """All four fields are required (no defaults) per D-04 — Stage2Result()
    with no args must raise TypeError.
    """
    with pytest.raises(TypeError):
        Stage2Result()  # type: ignore[call-arg]


def test_stage2result_params_frozen_and_slots() -> None:
    """Dataclass params must reflect frozen=True and slots=True (D-04)."""
    params = Stage2Result.__dataclass_params__
    assert params.frozen is True
    # slots is exposed via __slots__ on the class, not always on __dataclass_params__
    # (depends on Python version), so cross-check on the class.
    assert hasattr(Stage2Result, "__slots__")
