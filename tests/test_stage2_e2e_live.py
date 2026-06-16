"""Live end-to-end smoke test for OllamaExtractor against a real Ollama server.

This is the Phase 22 full-stack end-to-end live test promised by ROADMAP Phase 22
and explicitly deferred from Phase 15 D-13. It exercises the complete Stage-2
pipeline: ``OllamaExtractor.async_extract`` with an injected ``OllamaClient``
connecting to a real Ollama server.

Skipped unless the OLLAMA_URL environment variable is set. This test does
NOT run in CI — the default ``pytest`` selector in ``pytest.yml`` excludes
the ``live_ollama`` marker, and even when collected the ``@pytest.mark.skipif``
gate fires when OLLAMA_URL is unset.

Run locally with::

    OLLAMA_URL=http://<HOST>:11434 .venv/bin/pytest -m live_ollama \
        tests/test_stage2_e2e_live.py -v

The model ``qwen3.5:2b`` must be pre-pulled on the target Ollama instance
before running this test (``ollama pull qwen3.5:2b``). If that tag is
unavailable on your instance, the test will fail with a model-not-found
error — not a useful skip. See RESEARCH.md Open Question #3.

Validates the full Stage-2 pipeline (D-12, D-13, D-14, D-15): confirms that
OllamaExtractor successfully extracts all three locked fields
(``tracking_number``, ``carrier_name``, ``order_name``) from a representative
HTML email body against a live model. No parcelapp.net calls are made
(D-13 — quota protection).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import aiohttp
import pytest
import pytest_socket

from custom_components.shop2parcel.api.ollama_client import OllamaClient
from custom_components.shop2parcel.extractors.ollama_extractor import OllamaExtractor

OLLAMA_URL = os.environ.get("OLLAMA_URL")

pytestmark = pytest.mark.live_ollama


@pytest.fixture
async def session():
    """Create a real aiohttp ClientSession for the live smoke test."""
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def _allow_ollama_socket(socket_enabled):
    """Fully unblock sockets for the live smoke test.

    The HA harness (`pytest_homeassistant_custom_component.plugins`) installs
    `socket_allow_hosts(["127.0.0.1"])` BEFORE `disable_socket`, which mutates
    the real `socket.socket.connect` to a host-filtered guard. The standard
    `socket_enabled` fixture only restores `socket.socket` itself, not its
    `connect` method, so even with `socket_enabled` requests to non-127.0.0.1
    hosts raise `SocketConnectBlockedError`. We need both:

      1. `socket_enabled` (autouse via fixture arg) → restores `socket.socket`
      2. `socket_allow_hosts([OLLAMA_URL host])` → replaces the connect guard
         with one that permits our target host.
    """
    if OLLAMA_URL:
        host = urlparse(OLLAMA_URL).hostname
        if host:
            pytest_socket.socket_allow_hosts([host], allow_unix_socket=True)
    yield


@pytest.mark.skipif(not OLLAMA_URL, reason="OLLAMA_URL env var not set")
async def test_stage2_extractor_live(_allow_ollama_socket, session):
    """Full Stage-2 pipeline: OllamaExtractor.async_extract returns a usable Stage2Result.

    Constructs an OllamaClient with the real Ollama server, wraps it in an
    OllamaExtractor, and calls async_extract with a hardcoded minimal HTML
    email body. Asserts that all three locked fields are non-None, confirming
    the Phase 16 OllamaExtractor produces a complete Stage2Result against a
    live model (D-13, D-14).
    """
    client = OllamaClient(
        session=session,
        base_url=OLLAMA_URL,
        model="qwen3.5:2b",
        timeout=60.0,
    )
    extractor = OllamaExtractor(client=client, field_list=[])

    html = """<html><body>
    <p>Your order #1001 has shipped!</p>
    <p>Tracking number: 1Z999AA10123456784</p>
    <p>Carrier: UPS</p>
    <a href="https://www.ups.com/track?tracknum=1Z999AA10123456784">Track your package</a>
    </body></html>"""

    result = await extractor.async_extract(html, None)

    assert result.locked["tracking_number"] is not None
    assert result.locked["carrier_name"] is not None
    assert result.locked["order_name"] is not None
