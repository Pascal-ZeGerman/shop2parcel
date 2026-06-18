"""Live smoke test for OllamaClient against a real Ollama server.

Skipped unless the OLLAMA_URL environment variable is set. This test does
NOT run in CI — the default ``pytest`` selector in ``pytest.yml`` excludes
the ``live_ollama`` marker, and even when collected the ``@pytest.mark.skipif``
gate fires when OLLAMA_URL is unset.

Run locally with::

    OLLAMA_URL=http://192.168.0.190:11434 .venv/bin/pytest -m live_ollama \
        tests/api/test_ollama_client_live.py -v

Validates SPEC Requirement 1 (transport layer round-trip against a real
Ollama instance) in isolation. Full-stack Stage-2 end-to-end coverage is
explicitly deferred to Phase 22 (D-13).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import aiohttp
import pytest
import pytest_socket

from custom_components.shop2parcel.api.ollama_client import OllamaClient

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
async def test_live_generate_returns_dict(_allow_ollama_socket, session):
    """Live round-trip: real Ollama returns a dict containing key 'ok' (D-11)."""
    client = OllamaClient(
        session=session,
        base_url=OLLAMA_URL,
        model="qwen3.5:2b",
        timeout=60.0,
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    result = await client.async_generate('Reply with JSON {"ok": true}', schema)
    assert isinstance(result, dict)
    assert "ok" in result
