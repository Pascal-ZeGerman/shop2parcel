"""Tests for email_parser.EmailParser and ShipmentData.

RED phase: these tests must fail before email_parser.py is created.
"""
import dataclasses
import inspect
from pathlib import Path

import pytest

from custom_components.shop2parcel.api.email_parser import EmailParser, ShipmentData

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "shopify_shipping_email.html"


@pytest.fixture
def shopify_html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_extracts_all_fields_from_fixture(shopify_html: str) -> None:
    """HTML strategy: parse standard Shopify shipping email and extract all fields."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg123", 1745452800)
    assert result is not None
    assert isinstance(result, ShipmentData)
    assert result.tracking_number == "1Z999AA10123456784"
    assert result.order_name == "#1234"
    assert result.carrier_name != ""
    assert result.message_id == "msg123"
    assert result.email_date == 1745452800


def test_html_strategy_used_first(shopify_html: str) -> None:
    """HTML strategy should return non-None for standard Shopify layout (no regex needed)."""
    parser = EmailParser()
    result = parser._parse_html_template(shopify_html, "msg123", 1745452800)
    assert result is not None
    assert result.tracking_number == "1Z999AA10123456784"


def test_dual_strategy_fallback_used() -> None:
    """When HTML strategy finds no 'via Carrier', regex fallback must succeed.

    This HTML has no 'via UPS' pattern so _parse_html_template fails the carrier
    extraction — but it still finds tracking + order in a <p>, so the HTML strategy
    WOULD succeed with carrier_name='Unknown'. We need HTML that has NO tracking
    number in a CSS-matchable pattern so the HTML strategy returns None entirely.
    Use a format where tracking is embedded in text as 'Tracking number: X' but
    without the '#order' in a separate paragraph — forcing regex path.

    Actually: we need HTML where _parse_html_template returns None (no tracking+order
    pair). So: tracking in a <div> (not <p>), order in a <div>. But the regex fallback
    works on full text. Let's use HTML with no <p> elements at all.
    """
    html = (
        "<html><body>"
        "<div>Tracking number: 1Z999AA10123456784 Order #5678</div>"
        "</body></html>"
    )
    parser = EmailParser()
    # HTML strategy only scans <p> — so <div>-only HTML should return None from HTML strategy
    html_result = parser._parse_html_template(html, "msg456", 0)
    assert html_result is None, "HTML strategy should fail on div-only HTML"
    # Regex fallback should succeed
    result = parser.parse(html, "msg456", 0)
    assert result is not None
    assert result.tracking_number == "1Z999AA10123456784"


def test_returns_none_when_no_tracking_no_order() -> None:
    """Parser returns None when neither tracking number nor order name is found."""
    parser = EmailParser()
    result = parser.parse("<html><body><p>Hello world</p></body></html>", "x", 0)
    assert result is None


def test_shipment_data_is_dataclass() -> None:
    """ShipmentData must be a proper Python dataclass."""
    assert dataclasses.is_dataclass(ShipmentData)
    assert hasattr(ShipmentData, "__dataclass_fields__")


def test_shipment_data_slots() -> None:
    """ShipmentData must use __slots__ (slots=True)."""
    instance = ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id="msg123",
        email_date=1745452800,
    )
    assert hasattr(instance, "__slots__")


def test_carrier_name_unknown_when_not_found() -> None:
    """When carrier not extractable from HTML, carrier_name defaults to 'Unknown'."""
    # HTML has tracking + order but no 'via Carrier' pattern
    html = (
        "<html><body>"
        "<p>Your order #9999 has shipped.</p>"
        "<p>1Z999AA10123456784</p>"
        "</body></html>"
    )
    parser = EmailParser()
    result = parser.parse(html, "msg789", 0)
    assert result is not None
    assert result.carrier_name == "Unknown"


def test_order_name_starts_with_hash(shopify_html: str) -> None:
    """Parsed order_name must always start with '#'."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg123", 1745452800)
    assert result is not None
    assert result.order_name.startswith("#")


def test_regex_fallback_extracts_tracking() -> None:
    """Regex fallback correctly extracts tracking number from minimal HTML."""
    html = (
        "<html><body>"
        "<p>Tracking number: 1Z999AA10123456784 Order #5678</p>"
        "</body></html>"
    )
    parser = EmailParser()
    result = parser.parse(html, "msg456", 0)
    assert result is not None
    assert result.tracking_number == "1Z999AA10123456784"


def test_regex_fallback_extracts_order() -> None:
    """Regex fallback correctly extracts order name from minimal HTML."""
    html = (
        "<html><body>"
        "<p>Tracking number: 1Z999AA10123456784 Order #5678</p>"
        "</body></html>"
    )
    parser = EmailParser()
    result = parser.parse(html, "msg456", 0)
    assert result is not None
    assert result.order_name == "#5678"


def test_no_ha_imports() -> None:
    """email_parser.py must not import from homeassistant.*"""
    import custom_components.shop2parcel.api.email_parser as module
    source_file = inspect.getfile(module)
    content = Path(source_file).read_text(encoding="utf-8")
    assert "homeassistant" not in content, (
        "email_parser.py contains homeassistant import — violates D-03 no-HA-import rule"
    )
