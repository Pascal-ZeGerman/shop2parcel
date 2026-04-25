"""Tests for carrier_codes.normalize_carrier.

RED phase: these tests must fail before carrier_codes.py is created.
"""
import pytest
from custom_components.shop2parcel.api.carrier_codes import normalize_carrier


@pytest.mark.parametrize(
    "shopify_name,expected_code",
    [
        ("UPS", "ups"),
        ("ups", "ups"),
        ("FedEx", "fedex"),
        ("Canada Post", "cp"),
        ("DHL Express", "dhl"),
        ("DHL eCommerce", "dhl"),
        ("Royal Mail", "rm"),
        ("Australia Post", "au"),
        ("Unknown Carrier XYZ", "pholder"),
        ("", "pholder"),
        ("completely unknown", "pholder"),
        ("  UPS  ", "ups"),
    ],
)
def test_normalize_carrier(shopify_name: str, expected_code: str) -> None:
    """Verify case-insensitive, whitespace-stripped carrier lookup."""
    assert normalize_carrier(shopify_name) == expected_code


def test_fallback_is_not_none() -> None:
    """'none' is a parcel-ha internal sentinel — must never be submitted to parcelapp API."""
    result = normalize_carrier("completely unknown")
    assert result == "pholder"
    assert result != "none"
