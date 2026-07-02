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
TAGS_URL = f"{BASE_URL}/api/tags"


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
    assert body["think"] is False  # Reasoning-model guard: structured output
    # must land in `response`, not `thinking`. UAT against qwen3.5:2b showed
    # the empty-response gap when thinking is enabled.
    assert body["format"] == schema
    assert body["options"] == {"temperature": 0, "num_ctx": 4096, "num_predict": 256}
    assert body["keep_alive"] == "5m"
    assert body["model"] == "qwen3.5:2b"
    assert body["prompt"] == "test prompt"


@pytest.mark.asyncio
async def test_request_body_caps_generation_length(client):
    """Generation is bounded by num_predict so a runaway extraction cannot
    exceed the request timeout (Stage-2 poison-message timeout loop fix).

    Without a num_predict cap, a grammar-constrained generation on a
    pathological non-shipment email can run until num_ctx is exhausted; on a
    slow CPU Ollama server that overruns the aiohttp total timeout and raises
    OllamaTransientError every poll cycle.
    """
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
        body = requests[0].kwargs["json"]
    assert body["options"]["num_predict"] == 256


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


# ---------------------------------------------------------------------------
# async_generate_with_metadata — Phase 16 (Pitfall 5 / A1 Option 3)
# ---------------------------------------------------------------------------


async def test_async_generate_with_metadata_returns_passes_used_1(client):
    """Pass-1 happy path: clean JSON → returns (result, {'passes_used': 1})."""
    envelope = {
        "model": "qwen3.5:2b",
        "response": '{"tracking_number":"1Z999AA1","carrier_name":"ups","order_name":"#1001"}',
        "done": True,
    }
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result, meta = await client.async_generate_with_metadata(
            "extract shipment", {"type": "object"}
        )
    assert result == {
        "tracking_number": "1Z999AA1",
        "carrier_name": "ups",
        "order_name": "#1001",
    }
    assert meta == {"passes_used": 1}


async def test_async_generate_with_metadata_returns_passes_used_2(client, monkeypatch):
    """Pass-2 fence-strip success: returns (result, {'passes_used': 2}).

    Engineering note: ``normalize_llm_payload`` already does ``{...}`` substring
    extraction, so realistic markdown-fenced LLM output parses successfully on
    Pass 1 in production (see ``test_markdown_fence_retry_success`` — that test
    only asserts the *result*, not the pass count, and it parses in Pass 1).
    Pass 2 is a defense-in-depth code path that is reachable only when Pass 1's
    ``json.loads`` fails *after* normalize succeeded. To prove the metadata
    contract for that path without depending on a fragile constructed input,
    monkeypatch ``json.loads`` to fail exactly once — exercising the Pass 1 →
    Pass 2 transition and verifying ``passes_used == 2`` lands in the tuple.
    """
    import json as _json

    from custom_components.shop2parcel.api import ollama_client as _ollama_client_mod

    real_loads = _json.loads
    call_count = {"n": 0}

    def fail_once_then_succeed(s, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Force Pass 1 to fall through to Pass 2.
            raise _json.JSONDecodeError("simulated Pass-1 failure", s, 0)
        return real_loads(s, *args, **kwargs)

    monkeypatch.setattr(_ollama_client_mod.json, "loads", fail_once_then_succeed)

    envelope = {
        "response": '{"tracking_number":"1Z999AA1"}',
        "done": True,
    }
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result, meta = await client.async_generate_with_metadata("prompt", {"type": "object"})
    assert result == {"tracking_number": "1Z999AA1"}
    assert meta == {"passes_used": 2}
    assert call_count["n"] == 2  # Pass 1 (failed) + Pass 2 (succeeded)


async def test_async_generate_backward_compat(client):
    """async_generate still returns a bare dict (backward-compatible wrapper).

    Mirrors test_async_generate_happy_path but proves the existing one-callsite
    API is unchanged for any caller that doesn't need the metadata variant.
    """
    envelope = {
        "model": "qwen3.5:2b",
        "response": '{"tracking_number":"1Z999AA1","carrier_name":"ups","order_name":"#1001"}',
        "done": True,
    }
    with aioresponses() as mock:
        mock.post(GENERATE_URL, payload=envelope, status=200)
        result = await client.async_generate("extract shipment", {"type": "object"})
    # Bare dict — no tuple unpacking required.
    assert isinstance(result, dict)
    assert result == {
        "tracking_number": "1Z999AA1",
        "carrier_name": "ups",
        "order_name": "#1001",
    }


# ---------------------------------------------------------------------------
# async_get_tags — Phase 17 (D-04, CFG-01)
# ---------------------------------------------------------------------------


async def test_async_get_tags_happy_path(session):
    """200 with models list → returns list[str] of tag names (happy path)."""
    payload = {"models": [{"name": "qwen3.5:2b"}, {"name": "llama3.1:8b"}]}
    with aioresponses() as mock:
        mock.get(TAGS_URL, payload=payload, status=200)
        result = await OllamaClient.async_get_tags(session, BASE_URL, 30.0)
    assert result == ["qwen3.5:2b", "llama3.1:8b"]


async def test_async_get_tags_connection_error(session):
    """aiohttp.ClientConnectionError → OllamaTransientError with 'network error'."""
    with aioresponses() as mock:
        mock.get(TAGS_URL, exception=aiohttp.ClientConnectionError("refused"))
        with pytest.raises(OllamaTransientError, match="network error"):
            await OllamaClient.async_get_tags(session, BASE_URL, 30.0)


async def test_async_get_tags_non_200_status(session):
    """non-200 status (500) → OllamaTransientError with 'HTTP 500'."""
    with aioresponses() as mock:
        mock.get(TAGS_URL, status=500, payload={})
        with pytest.raises(OllamaTransientError, match="HTTP 500"):
            await OllamaClient.async_get_tags(session, BASE_URL, 30.0)


async def test_async_get_tags_trailing_slash_trimmed(session):
    """Trailing slash in base_url is stripped before composing the request URL."""
    payload = {"models": [{"name": "qwen3.5:2b"}]}
    with aioresponses() as mock:
        # TAGS_URL uses the clean BASE_URL — the trailing-slash variant must hit the same URL
        mock.get(TAGS_URL, payload=payload, status=200)
        result = await OllamaClient.async_get_tags(session, BASE_URL + "/", 30.0)
    assert result == ["qwen3.5:2b"]


async def test_async_get_tags_malformed_name_entry_skipped(session):
    """Entries without a string 'name' are filtered out; only string names returned."""
    payload = {
        "models": [
            {"name": "qwen3.5:2b"},
            {"model": "llama3.1:8b"},  # missing 'name' key
            {"name": 42},  # non-string 'name'
        ]
    }
    with aioresponses() as mock:
        mock.get(TAGS_URL, payload=payload, status=200)
        result = await OllamaClient.async_get_tags(session, BASE_URL, 30.0)
    assert result == ["qwen3.5:2b"]
