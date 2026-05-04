"""Tests for email_parser.EmailParser and ShipmentData.

RED phase: these tests must fail before email_parser.py is created.
"""

import dataclasses
import inspect
from pathlib import Path

import pytest

from custom_components.shop2parcel.api.email_parser import (
    EmailParser,
    ParseResult,
    ShipmentData,
    STRATEGY_FEDEX,
    STRATEGY_HTML,
    STRATEGY_REGEX,
    STRATEGY_UPS,
    STRATEGY_USPS,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_PATH = FIXTURE_DIR / "shopify_shipping_email.html"  # backward compat for existing tests/fixtures


@pytest.fixture
def shopify_html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def ups_html() -> str:
    return (FIXTURE_DIR / "ups_shipping.html").read_text(encoding="utf-8")


@pytest.fixture
def usps_html() -> str:
    return (FIXTURE_DIR / "usps_shipping.html").read_text(encoding="utf-8")


@pytest.fixture
def fedex_html() -> str:
    return (FIXTURE_DIR / "fedex_shipping.html").read_text(encoding="utf-8")


def test_extracts_all_fields_from_fixture(shopify_html: str) -> None:
    """HTML strategy: parse standard Shopify shipping email and extract all fields."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg123", 1745452800)
    assert result.shipment is not None
    assert isinstance(result.shipment, ShipmentData)
    assert result.shipment.tracking_number == "1Z999AA10123456784"
    assert result.shipment.order_name == "#1234"
    assert result.shipment.carrier_name != ""
    assert result.shipment.message_id == "msg123"
    assert result.shipment.email_date == 1745452800


def test_html_strategy_used_first(shopify_html: str) -> None:
    """HTML strategy should return non-None for standard Shopify layout (no regex needed)."""
    parser = EmailParser()
    html_result = parser._parse_html_template(shopify_html, "msg123", 1745452800)
    assert html_result.shipment is not None
    assert html_result.shipment.tracking_number == "1Z999AA10123456784"


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
    html = "<html><body><div>Tracking number: 1Z999AA10123456784 Order #5678</div></body></html>"
    parser = EmailParser()
    # HTML strategy only scans <p> — so <div>-only HTML should return None from HTML strategy
    html_result = parser._parse_html_template(html, "msg456", 0)
    assert html_result.shipment is None, "HTML strategy should fail on div-only HTML"
    # Regex fallback should succeed
    result = parser.parse(html, "msg456", 0)
    assert result.shipment is not None
    assert result.shipment.tracking_number == "1Z999AA10123456784"


def test_returns_none_when_no_tracking_no_order() -> None:
    """Parser returns None when neither tracking number nor order name is found."""
    parser = EmailParser()
    result = parser.parse("<html><body><p>Hello world</p></body></html>", "x", 0)
    assert result.shipment is None


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
    html = "<html><body><p>Your order #9999 has shipped.</p><p>1Z999AA10123456784</p></body></html>"
    parser = EmailParser()
    result = parser.parse(html, "msg789", 0)
    assert result.shipment is not None
    assert result.shipment.carrier_name == "Unknown"


def test_order_name_starts_with_hash(shopify_html: str) -> None:
    """Parsed order_name must always start with '#'."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg123", 1745452800)
    assert result.shipment is not None
    assert result.shipment.order_name.startswith("#")


def test_regex_fallback_extracts_tracking() -> None:
    """Regex fallback correctly extracts tracking number from minimal HTML."""
    html = "<html><body><p>Tracking number: 1Z999AA10123456784 Order #5678</p></body></html>"
    parser = EmailParser()
    result = parser.parse(html, "msg456", 0)
    assert result.shipment is not None
    assert result.shipment.tracking_number == "1Z999AA10123456784"


def test_regex_fallback_extracts_order() -> None:
    """Regex fallback correctly extracts order name from minimal HTML."""
    html = "<html><body><p>Tracking number: 1Z999AA10123456784 Order #5678</p></body></html>"
    parser = EmailParser()
    result = parser.parse(html, "msg456", 0)
    assert result.shipment is not None
    assert result.shipment.order_name == "#5678"


def test_parse_always_returns_parseresult(shopify_html: str) -> None:
    """DIAG-01: parse() must always return a ParseResult instance, never None."""
    parser = EmailParser()
    # Success path
    assert isinstance(parser.parse(shopify_html, "msg1", 1), ParseResult)
    # Failure path
    assert isinstance(parser.parse("<html/>", "msg2", 2), ParseResult)


def test_html_strategy_success_strategy_used(shopify_html: str) -> None:
    """DIAG-02: HTML strategy success -> strategy_used='html_template', skip_reason=None,
    keyword_hits all-False (HTML strategy never runs the fallback regexes -- D-07)."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg1", 1)
    assert result.shipment is not None
    assert result.strategy_used == "html_template"
    assert result.skip_reason is None
    assert result.keyword_hits == {
        "tracking_regex": False,
        "order_regex": False,
        "carrier_regex": False,
    }


def test_regex_fallback_success_strategy_used() -> None:
    """DIAG-03: Regex fallback success -> strategy_used='regex_fallback', skip_reason=None,
    tracking_regex and order_regex hits are True."""
    html = (
        "<html><body><div>Tracking number: 1Z999AA10123456784 Order #5678</div></body></html>"
    )
    parser = EmailParser()
    result = parser.parse(html, "msg2", 0)
    assert result.shipment is not None
    assert result.strategy_used == "regex_fallback"
    assert result.skip_reason is None
    assert result.keyword_hits["tracking_regex"] is True
    assert result.keyword_hits["order_regex"] is True


def test_keyword_hits_always_has_all_three_keys() -> None:
    """DIAG-04: Every parse() call's keyword_hits has exactly the 3 expected keys
    with bool values, including the total-failure path."""
    parser = EmailParser()
    expected_keys = {"tracking_regex", "order_regex", "carrier_regex"}
    # Total failure path
    empty = parser.parse("<html><body><p>Hello world</p></body></html>", "x", 0)
    assert empty.shipment is None
    assert empty.skip_reason == "no_regex_match"
    assert empty.strategy_used is None
    assert set(empty.keyword_hits.keys()) == expected_keys
    assert all(isinstance(v, bool) for v in empty.keyword_hits.values())
    # Regex fallback path
    regex = parser.parse(
        "<html><body><div>Tracking number: 1Z999AA10123456784 Order #5678</div></body></html>",
        "y",
        0,
    )
    assert set(regex.keyword_hits.keys()) == expected_keys
    assert all(isinstance(v, bool) for v in regex.keyword_hits.values())


def test_no_ha_imports() -> None:
    """email_parser.py must not import from homeassistant.*"""
    import custom_components.shop2parcel.api.email_parser as module

    source_file = inspect.getfile(module)
    content = Path(source_file).read_text(encoding="utf-8")
    assert "homeassistant" not in content, (
        "email_parser.py contains homeassistant import — violates D-03 no-HA-import rule"
    )


def test_strategy_constants_are_defined() -> None:
    """PARSE-01 / D-07: All STRATEGY_* constants importable from email_parser with locked string values."""
    assert STRATEGY_HTML == "html_template"
    assert STRATEGY_UPS == "ups_template"
    assert STRATEGY_USPS == "usps_template"
    assert STRATEGY_FEDEX == "fedex_template"
    assert STRATEGY_REGEX == "regex_fallback"


def test_ups_template_extracts_tracking(ups_html: str) -> None:
    """PARSE-04: UPS template extracts tracking number, sets carrier_name='UPS' and strategy_used=STRATEGY_UPS."""
    parser = EmailParser()
    result = parser.parse(ups_html, "ups_msg1", 1746000000)
    assert result.shipment is not None
    assert result.shipment.tracking_number == "1Z0Y12345678031234"
    assert result.shipment.carrier_name == "UPS"
    assert result.shipment.message_id == "ups_msg1"
    assert result.shipment.email_date == 1746000000
    assert result.strategy_used == STRATEGY_UPS
    assert result.skip_reason is None


def test_usps_template_extracts_tracking(usps_html: str) -> None:
    """PARSE-05: USPS template extracts 26-digit tracking number, sets carrier_name='USPS' and strategy_used=STRATEGY_USPS."""
    parser = EmailParser()
    result = parser.parse(usps_html, "usps_msg1", 1746000000)
    assert result.shipment is not None
    assert result.shipment.tracking_number == "92123456508577307776690000"
    assert result.shipment.carrier_name == "USPS"
    assert result.strategy_used == STRATEGY_USPS
    assert result.skip_reason is None


def test_fedex_template_extracts_tracking(fedex_html: str) -> None:
    """PARSE-06: FedEx template extracts 20-digit SmartPost tracking, sets carrier_name='FedEx' and strategy_used=STRATEGY_FEDEX."""
    parser = EmailParser()
    result = parser.parse(fedex_html, "fedex_msg1", 1746000000)
    assert result.shipment is not None
    assert result.shipment.tracking_number == "61290912345678912345"
    assert result.shipment.carrier_name == "FedEx"
    assert result.strategy_used == STRATEGY_FEDEX
    assert result.skip_reason is None


def test_ups_detect_fn_not_triggered_on_shopify_html(shopify_html: str) -> None:
    """PARSE-09 / T-Spoof mitigation: _detect_ups must NOT fire on Shopify fixture (contains ups.com link but also 'shopify')."""
    from custom_components.shop2parcel.api.email_parser import _detect_ups
    assert _detect_ups(shopify_html) is False


def test_usps_detect_fn_not_triggered_on_shopify_html(shopify_html: str) -> None:
    """PARSE-09 / T-Spoof mitigation: _detect_usps must NOT fire on the Shopify fixture (no usps.com present)."""
    from custom_components.shop2parcel.api.email_parser import _detect_usps
    assert _detect_usps(shopify_html) is False


def test_fedex_detect_fn_not_triggered_on_shopify_html(shopify_html: str) -> None:
    """PARSE-09 / T-Spoof mitigation: _detect_fedex must NOT fire on the Shopify fixture (no fedex.com present)."""
    from custom_components.shop2parcel.api.email_parser import _detect_fedex
    assert _detect_fedex(shopify_html) is False


def test_usps_detect_fn_not_triggered_on_shopify_usps_merchant_email() -> None:
    """_detect_usps must NOT fire on a Shopify merchant email that includes a USPS tracking URL."""
    from custom_components.shop2parcel.api.email_parser import _detect_usps
    html = (
        "<html><body>"
        "<p>Your order #1234 has shipped. shopify.com</p>"
        "<a href='https://tools.usps.com/go/TrackConfirmAction?tLabels=9261290100830125000029'>Track</a>"
        "</body></html>"
    )
    assert _detect_usps(html) is False


def test_fedex_detect_fn_not_triggered_on_shopify_fedex_merchant_email() -> None:
    """_detect_fedex must NOT fire on a Shopify merchant email that includes a FedEx tracking URL."""
    from custom_components.shop2parcel.api.email_parser import _detect_fedex
    html = (
        "<html><body>"
        "<p>Your order #5678 has shipped. shopify.com</p>"
        "<a href='https://www.fedex.com/fedextrack/?trknbr=449044304137821'>Track</a>"
        "</body></html>"
    )
    assert _detect_fedex(html) is False


def test_registry_checks_carrier_templates_before_shopify_path(shopify_html: str) -> None:
    """PARSE-03: Registry order must NOT cause Shopify fixture to be misclassified — strategy_used must be STRATEGY_HTML."""
    parser = EmailParser()
    result = parser.parse(shopify_html, "msg1", 1)
    assert result.strategy_used == STRATEGY_HTML


def test_ups_direct_email_has_empty_order_name(ups_html: str) -> None:
    """PARSE-04: Direct carrier emails have no Shopify order number — order_name must be ''."""
    parser = EmailParser()
    result = parser.parse(ups_html, "ups_msg1", 1746000000)
    assert result.shipment is not None
    assert result.shipment.order_name == ""


def test_ups_template_keyword_hits_all_false(ups_html: str) -> None:
    """PARSE-13: Carrier templates don't run fallback regex — keyword_hits must be all False with all 3 keys."""
    parser = EmailParser()
    result = parser.parse(ups_html, "ups_msg1", 1746000000)
    assert result.keyword_hits == {
        "tracking_regex": False,
        "order_regex": False,
        "carrier_regex": False,
    }


def test_regex_fallback_extracts_carrier_shipped_via() -> None:
    """WR-02: Regex fallback must extract 'UPS' from 'shipped via UPS', not 'via'."""
    html = (
        "<html><body><div>"
        "Your order #1234 was shipped via UPS. "
        "Tracking number: 1Z999AA10123456784"
        "</div></body></html>"
    )
    parser = EmailParser()
    result = parser.parse(html, "msg_carrier", 0)
    assert result.shipment is not None
    assert result.shipment.carrier_name == "UPS"


def test_usps_91xxx_tracking_recognized() -> None:
    """WR-05: USPS Priority Mail Express 91xxx (22-digit IMpb) is recognized by _looks_like_tracking."""
    from custom_components.shop2parcel.api.email_parser import _looks_like_tracking

    # 91 prefix + 20 digits = 22 digits total (standard USPS Priority Mail Express)
    assert _looks_like_tracking("9102901000857730777669") is True
