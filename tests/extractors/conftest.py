"""Shared fixtures for extractor tests.

Mirrors the tests/api/test_ollama_client.py in-file fixture block — promoted
to a conftest because Plans 02 and 03 will both consume these. Phase 16 is
pure-Python and intentionally pulls no HA fixtures into this conftest (the
parent tests/conftest.py is auto-loaded for any HA-coupled tests).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.api.ollama_client import OllamaClient


@pytest.fixture
def mock_client() -> AsyncMock:
    """An AsyncMock bound to the OllamaClient interface.

    Mirrors tests/api/test_ollama_client.py fixture style. spec=OllamaClient
    ensures attribute typos fail at construction — Plan-03's async_extract
    cannot call a method the real OllamaClient doesn't expose.
    """
    return AsyncMock(spec=OllamaClient)


@pytest.fixture
def sample_stage1() -> ShipmentData:
    """A minimal Stage-1 ShipmentData for async_extract's stage1 arg.

    The extractor does NOT embed stage1 in the prompt (D-01) — this fixture
    exists only to satisfy the call signature and to power Plan-03's
    test_prompt_does_not_contain_stage1_values check.
    """
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id="msg-test-1",
        email_date=0,
    )


@pytest.fixture
def shopify_mini_html() -> str:
    """Minimal Shopify-style HTML with one tracking link and one TN in prose.

    Contains:
      * A <p> with prose tracking number (Strategy 1 / Plan-02 prose path).
      * A second <p> with explicit "Tracking number:" label.
      * One <a href> with the tracking number in the query string
        (Plan-02 preprocess_html href-list path / D-02).
    """
    return (
        "<html><body>"
        "<p>Your order #1234 has shipped via UPS.</p>"
        "<p>Tracking number: 1Z999AA10123456784</p>"
        '<a href="https://shopify.com/track?trk=1Z999AA10123456784">Track</a>'
        "</body></html>"
    )
