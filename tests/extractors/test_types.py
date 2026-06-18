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


# ---------------------------------------------------------------------------
# Conftest fixtures — Plan 02/03 consumers
# ---------------------------------------------------------------------------


def test_mock_client_fixture_is_spec_bound_to_ollama_client(mock_client) -> None:
    """mock_client fixture must be an AsyncMock bound to OllamaClient (Plan 03)."""
    from unittest.mock import AsyncMock

    from custom_components.shop2parcel.api.ollama_client import OllamaClient

    assert isinstance(mock_client, AsyncMock)
    # spec=OllamaClient — accessing a non-existent attr raises AttributeError
    assert isinstance(mock_client, OllamaClient)


def test_sample_stage1_is_real_shipment_data(sample_stage1) -> None:
    """sample_stage1 fixture must be a real ShipmentData with all 5 fields populated."""
    from custom_components.shop2parcel.api.email_parser import ShipmentData

    assert isinstance(sample_stage1, ShipmentData)
    assert sample_stage1.tracking_number == "1Z999AA10123456784"
    assert sample_stage1.carrier_name == "UPS"
    assert sample_stage1.order_name == "#1234"
    assert sample_stage1.message_id == "msg-test-1"
    assert sample_stage1.email_date == 0


def test_shopify_mini_html_fixture_contains_required_signals(
    shopify_mini_html,
) -> None:
    """shopify_mini_html fixture must contain a prose tracking number, an
    explicit label, and a tracking <a href> link (Plan 02/03 helpers consume).
    """
    assert isinstance(shopify_mini_html, str)
    assert "1Z999AA10123456784" in shopify_mini_html
    assert "<p>" in shopify_mini_html
    assert "<a href=" in shopify_mini_html
    assert "Tracking number" in shopify_mini_html
