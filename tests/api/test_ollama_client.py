"""Tests for OllamaClient — Ollama /api/generate HTTP transport.

Uses aioresponses to mock HTTP responses. Covers every SPEC.md acceptance
checkbox: happy path, request body shape (OLLM-04), markdown-fence-strip
retry (OLLM-05), NFKC + zero-width normalization (OLLM-06), status →
exception mapping (Req 5), and the structural invariants (no HA imports,
exception classes not subclasses of each other).

Invisible Unicode characters are written as escape sequences
(``\\u200b``, ``\\ufeff``, ``\\uff3a``, ``\\u0410``) rather than literal
invisible code points — per the plan acceptance criterion that no literal
U+200B / U+200C / U+200D / U+FEFF / U+FF3A / U+0410 appears in this file
outside of escape form.
"""

from __future__ import annotations

import aiohttp
import pytest
import yarl
from aioresponses import aioresponses

from custom_components.shop2parcel.api.exceptions import (
    OllamaSchemaError,
    OllamaTransientError,
)
from custom_components.shop2parcel.api.ollama_client import (
    GENERATE_PATH,
    OllamaClient,
)

BASE_URL = "http://localhost:11434"
GENERATE_URL = f"{BASE_URL}{GENERATE_PATH}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    """Create a real aiohttp ClientSession for tests."""
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
async def client(session):
    """Create an OllamaClient with test constructor args."""
    return OllamaClient(
        session=session,
        base_url=BASE_URL,
        model="qwen3.5:2b",
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# async_generate — happy path
# ---------------------------------------------------------------------------


async def test_async_generate_happy_path(client):
    """200 with clean JSON in 'response' field → returns parsed dict (SPEC Req 1)."""
    envelope = {
        "model": "qwen3.5:2b",
        "response": ('{"tracking_number":"1Z999AA1","carrier_name":"ups","order_name":"#1001"}'),
        "done": True,
    }
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result = await client.async_generate("extract shipment", {"type": "object"})
    assert result == {
        "tracking_number": "1Z999AA1",
        "carrier_name": "ups",
        "order_name": "#1001",
    }


# ---------------------------------------------------------------------------
# async_generate — request shape verification (OLLM-04, SPEC Req 2)
# ---------------------------------------------------------------------------


async def test_request_body_shape(client):
    """POST body matches OLLM-04 canonical shape exactly (SPEC Req 2)."""
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    envelope = {"response": '{"ok": true}', "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        await client.async_generate("test prompt", schema)
        requests = mock.requests[("POST", yarl.URL(GENERATE_URL))]
        assert len(requests) == 1
        body = requests[0].kwargs["json"]
    assert body["stream"] is False
    assert body["format"] == schema
    assert body["options"] == {"temperature": 0, "num_ctx": 4096}
    assert body["keep_alive"] == "5m"
    assert body["model"] == "qwen3.5:2b"
    assert body["prompt"] == "test prompt"


# ---------------------------------------------------------------------------
# async_generate — 2-pass parse pipeline (OLLM-05, SPEC Req 3)
# ---------------------------------------------------------------------------


async def test_markdown_fence_retry_success(client):
    """200 wrapped in markdown ```json fences parses on Pass 2 (SPEC Req 3 part A)."""
    fenced = '```json\n{"tracking_number":"1Z999AA1"}\n```'
    envelope = {"response": fenced, "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result = await client.async_generate("prompt", {"type": "object"})
    assert result == {"tracking_number": "1Z999AA1"}


async def test_markdown_fence_retry_failure(client):
    """Malformed JSON inside fences → OllamaSchemaError after Pass 2 (SPEC Req 3 part B)."""
    fenced = "```json\nnot-json-here\n```"
    envelope = {"response": fenced, "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


# ---------------------------------------------------------------------------
# async_generate — Unicode normalization (OLLM-06, SPEC Req 4)
# ---------------------------------------------------------------------------


async def test_nfkc_zero_width_bom_cyrillic_fullwidth(client):
    """NFKC + zero-width + BOM normalization runs on every parse attempt (SPEC Req 4).

    Fixture (escapes only):
      ``"\\ufeff{\\"tracking_number\\":\\"1\\uff3a999\\u0410\\u04101\\"}\\u200b"``

    Pipeline-by-step expectation:
      1. NFKC → ``\\ufeff`` stays, ``\\uff3a`` → ``Z``, ``\\u0410`` stays,
         ``\\u200b`` stays.
      2. Strip ``\\u200b``/``\\u200c``/``\\u200d``/``\\ufeff`` → BOM and
         trailing ZWSP gone.
      3. Brace extraction → already the whole post-strip string.

    Expected: ``{"tracking_number": "1Z999\\u0410\\u04101"}``. NFKC does NOT
    fold Cyrillic A (U+0410) to Latin A (U+0041); they remain distinct
    code points. Phase 20 carrier-regex pre-POST validation is the layer
    that handles homoglyph slips on actual tracking numbers; this assertion
    keeps the test aligned with
    ``tests/api/test_ollama_normalize.py::test_combined_fixture_from_spec``.
    """
    raw = '\ufeff{"tracking_number":"1\uff3a999\u0410\u04101"}\u200b'
    envelope = {"response": raw, "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result = await client.async_generate("prompt", {"type": "object"})
    assert result == {"tracking_number": "1Z999\u0410\u04101"}


async def test_missing_brace_raises_schema_error(client):
    """response field with no '{' → Pass 1 normalize raises, no Pass 2 (SPEC Req 4 missing-{)."""
    envelope = {"response": "No JSON here", "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


# ---------------------------------------------------------------------------
# async_generate — network errors (SPEC Req 5)
# ---------------------------------------------------------------------------


async def test_timeout_raises_transient_error(client):
    """aioresponses raises TimeoutError → OllamaTransientError raised."""
    with aioresponses() as mock:
        mock.post(GENERATE_URL, exception=TimeoutError())
        with pytest.raises(OllamaTransientError):
            await client.async_generate("prompt", {"type": "object"})


async def test_connection_refused_raises_transient_error(client):
    """aioresponses raises ClientConnectionError → OllamaTransientError raised."""
    with aioresponses() as mock:
        mock.post(
            GENERATE_URL,
            exception=aiohttp.ClientConnectionError("Connection refused"),
        )
        with pytest.raises(OllamaTransientError):
            await client.async_generate("prompt", {"type": "object"})


# ---------------------------------------------------------------------------
# async_generate — HTTP status mapping (SPEC Req 5)
# ---------------------------------------------------------------------------


async def test_5xx_raises_transient_error(client):
    """503 → OllamaTransientError raised."""
    with aioresponses() as mock:
        mock.post(GENERATE_URL, status=503)
        with pytest.raises(OllamaTransientError):
            await client.async_generate("prompt", {"type": "object"})


async def test_404_raises_schema_error(client):
    """404 (Ollama: 'model not found') → OllamaSchemaError raised."""
    with aioresponses() as mock:
        mock.post(GENERATE_URL, status=404)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


async def test_401_raises_schema_error(client):
    """401 → OllamaSchemaError raised."""
    with aioresponses() as mock:
        mock.post(GENERATE_URL, status=401)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


async def test_403_raises_schema_error(client):
    """403 → OllamaSchemaError raised."""
    with aioresponses() as mock:
        mock.post(GENERATE_URL, status=403)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


# ---------------------------------------------------------------------------
# async_generate — envelope shape errors (SPEC Req 5)
# ---------------------------------------------------------------------------


async def test_missing_response_field_raises_schema_error(client):
    """200 envelope without 'response' key → OllamaSchemaError raised."""
    envelope = {"model": "qwen3.5:2b", "done": True}
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        with pytest.raises(OllamaSchemaError):
            await client.async_generate("prompt", {"type": "object"})


# ---------------------------------------------------------------------------
# Structural tests (SPEC Req 5 invariant, no-HA-imports)
# ---------------------------------------------------------------------------


def test_exception_classes_not_subclasses_of_each_other():
    """OllamaTransientError and OllamaSchemaError are not subclasses of each other (SPEC Req 5)."""
    assert not issubclass(OllamaTransientError, OllamaSchemaError)
    assert not issubclass(OllamaSchemaError, OllamaTransientError)


def test_no_ha_imports():
    """Inspect ollama_client.py source — 'homeassistant' must not appear anywhere."""
    from pathlib import Path

    client_path = (
        Path(__file__).parent.parent.parent
        / "custom_components"
        / "shop2parcel"
        / "api"
        / "ollama_client.py"
    )
    contents = client_path.read_text(encoding="utf-8")
    assert "homeassistant" not in contents
