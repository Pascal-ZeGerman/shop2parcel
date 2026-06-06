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

import aiohttp
import pytest

from custom_components.shop2parcel.api.ollama_client import OllamaClient

OLLAMA_URL = os.environ.get("OLLAMA_URL")

pytestmark = pytest.mark.live_ollama


@pytest.fixture
async def session():
    """Create a real aiohttp ClientSession for the live smoke test."""
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.mark.skipif(not OLLAMA_URL, reason="OLLAMA_URL env var not set")
async def test_live_generate_returns_dict(session):
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
