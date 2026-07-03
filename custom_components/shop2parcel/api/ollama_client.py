"""Ollama local-LLM HTTP client.

Implements the /api/generate endpoint for structured-output extraction.
See: https://github.com/ollama/ollama/blob/main/docs/api.md

Auth: none — local Ollama instance requires no authentication.
Timeout: configurable via constructor (default supplied by Phase 17 options flow).

session must be the HA shared session (injected by caller — Phase 17/19).
Never create a new aiohttp.ClientSession inside this class.

No HA imports (mirrors api/parcelapp.py pattern).

Privacy posture (D-07/D-08/D-09):
  * Three log points, all DEBUG. No INFO/WARN/ERROR from this module.
  * Log fields are structural only — model name, prompt length, response
    length, parse_passes, HTTP status, exception class name. Never email
    or prompt content.
  * Exception messages reference status codes, error class names, lengths,
    and chained lower-library messages (via ``from err``). They never
    interpolate the prompt, raw response text, envelope, normalized
    payload, or parsed result.
"""

from __future__ import annotations

import json
import logging
import re

import aiohttp

from .exceptions import OllamaSchemaError, OllamaTransientError
from .ollama_normalize import normalize_llm_payload

_LOGGER = logging.getLogger(__name__)

GENERATE_PATH = "/api/generate"
TAGS_PATH = "/api/tags"

# Cap the number of tokens the model may generate per extraction. A tracking
# extraction response is a small JSON object; without this bound a
# grammar-constrained generation on a pathological non-shipment email can run
# until num_ctx is exhausted and overrun the request timeout on a slow CPU
# Ollama server (observed: same email timing out every poll cycle for hours).
_NUM_PREDICT = 256

# Module-level compiled fence-strip regex (R-03). Used only in Pass 2
# of the parse pipeline (Pitfall 1) — Pass 1 normalizes the raw text
# directly without any fence handling. The pattern matches both
# ``` ```json\n...\n``` ``` and bare ``` ```\n...\n``` ``` forms.
_FENCE_RE = re.compile(r"^```(?:json)?\n(.*?)\n```$", re.DOTALL)


class OllamaClient:
    """Async HTTP transport for Ollama /api/generate.

    Constructor args:
      session    — injected aiohttp.ClientSession (shared HA session).
      base_url   — Ollama server base URL, e.g. "http://192.168.0.190:11434".
      model      — Ollama model tag, e.g. "qwen3.5:2b".
      timeout    — Per-request total timeout in seconds (float).

    This client knows nothing about emails, ShipmentData, or HA config.
    It takes prompt: str and schema: dict as opaque inputs and returns dict.

    All four constructor arguments are required; Phase 17's options flow
    owns the default values (CONTEXT.md §"Integration Points").
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        model: str,
        timeout: float,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")  # R-04: strip trailing slash
        self._model = model
        self._timeout = timeout

    @staticmethod
    async def async_get_tags(
        session: aiohttp.ClientSession,
        base_url: str,
        timeout: float,
    ) -> list[str]:
        """GET /api/tags and return list of available model tag names.

        Never creates a new ClientSession — session is injected by caller.

        Args:
            session   — injected aiohttp.ClientSession (shared HA session).
            base_url  — Ollama server base URL (trailing slash stripped internally).
            timeout   — Per-request total timeout in seconds.

        Returns:
            List of model tag name strings (e.g. ["qwen3.5:2b", "llama3.1:8b"]).
            Entries without a string ``name`` field are filtered out.

        Raises:
            OllamaTransientError: On TimeoutError, any aiohttp.ClientError
                (WR-04: includes ClientPayloadError), or any non-200 HTTP status.
        """
        url = f"{base_url.rstrip('/')}{TAGS_PATH}"
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    raise OllamaTransientError(f"Ollama /api/tags returned HTTP {resp.status}")
                data = await resp.json(content_type=None)
                return [m["name"] for m in data.get("models", []) if isinstance(m.get("name"), str)]
        except (TimeoutError, aiohttp.ClientError) as err:
            # WR-04: catch the aiohttp base class — ClientPayloadError (connection
            # lost mid-body, raised by resp.json()) subclasses ClientError but NOT
            # ClientConnectionError, and previously escaped the documented taxonomy.
            raise OllamaTransientError(f"Ollama /api/tags network error: {err}") from err

    async def async_generate(self, prompt: str, schema: dict) -> dict:
        """POST to /api/generate and return the parsed structured-output dict.

        Backward-compatible wrapper around :meth:`async_generate_with_metadata`
        (Phase 16 / Pitfall 5 / Assumption A1 Option 3). Discards the metadata
        dict so existing Phase-15 callers see the unchanged ``dict`` return.

        The 2-pass parse pipeline, status-code mapping, and exception taxonomy
        all live in :meth:`async_generate_with_metadata` — this wrapper exists
        solely to preserve the original public API.
        """
        result, _meta = await self.async_generate_with_metadata(prompt, schema)
        return result

    async def async_generate_with_metadata(self, prompt: str, schema: dict) -> tuple[dict, dict]:
        """POST to /api/generate and return ``(result, {"passes_used": int})``.

        Phase-16 metadata variant (Pitfall 5 / Assumption A1 Option 3). Surfaces
        the existing internal ``passes_used`` counter to enable Phase-21's D-C1
        diagnostic sensor (rolling counts of clean-first-pass vs. fence-strip
        retries) without breaking the Phase-15 ``async_generate -> dict`` API.

        The 2-pass parse pipeline (D-04/D-05/D-06):
          Pass 1: normalize_llm_payload(raw_text) → json.loads.
                  - On OllamaSchemaError (no '{' in raw_text) → re-raise
                    immediately (D-06; Pitfall 2). No retry can recover.
                  - On json.JSONDecodeError → fall through to Pass 2.
          Pass 2: fence-strip raw_text → normalize_llm_payload → json.loads.
                  - On any failure → wrap as OllamaSchemaError.

        Status-code mapping (Exception Taxonomy Decision Tree):
          401, 403, 404 → OllamaSchemaError
          >= 500        → OllamaTransientError
          400-499 other → OllamaTransientError (catch-all)
          TimeoutError, aiohttp.ClientError (WR-04) → OllamaTransientError

        Returns:
            A tuple ``(result, {"passes_used": 1 | 2})`` where ``passes_used``
            is 1 when the clean first-pass parse succeeded and 2 when the
            fence-strip retry succeeded.

        Raises:
            OllamaSchemaError: HTTP 401/403/404, envelope JSON decode failure,
                envelope missing 'response' key, 'response' not a str, Pass 1
                missing-'{', or Pass 2 parse failure.
            OllamaTransientError: HTTP >=500, other 4xx, TimeoutError, or
                any aiohttp.ClientError (WR-04: includes ClientPayloadError).
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "think": False,  # Disable thinking-model reasoning so the structured
            # output lands in `response`, not `thinking`. No-op for non-thinking
            # models (e.g. qwen2.5, llama3.1). Required for qwen3.5 / deepseek-r1
            # / other reasoning models, which otherwise emit the formatted JSON
            # in `thinking` and leave `response` empty — observed in UAT against
            # qwen3.5:2b. See Ollama /api/generate `think` parameter.
            "format": schema,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": _NUM_PREDICT},
            "keep_alive": "5m",
        }
        _LOGGER.debug("Ollama generate: model=%s prompt_len=%d", self._model, len(prompt))

        try:
            async with self._session.post(
                f"{self._base_url}{GENERATE_PATH}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                # --- Status code mapping (most-specific first) ---
                if resp.status in (401, 403, 404):
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        "OllamaSchemaError",
                    )
                    raise OllamaSchemaError(f"Ollama returned HTTP {resp.status}")
                if resp.status >= 500:
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        "OllamaTransientError",
                    )
                    raise OllamaTransientError(f"Ollama server error: HTTP {resp.status}")
                if 400 <= resp.status < 500:
                    # Catch-all for unexpected 4xx — mirrors parcelapp.py
                    # "unexpected client error" branch.
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        "OllamaTransientError",
                    )
                    raise OllamaTransientError(
                        f"Ollama unexpected client error: HTTP {resp.status}"
                    )

                # --- Decode the JSON envelope ---
                try:
                    envelope = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        type(err).__name__,
                    )
                    raise OllamaSchemaError(f"Ollama response is not valid JSON: {err}") from err

                # --- Extract the 'response' field ---
                raw_text = envelope.get("response")
                if raw_text is None:
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        "OllamaSchemaError",
                    )
                    raise OllamaSchemaError("Ollama envelope missing 'response' field")
                if not isinstance(raw_text, str):
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        "OllamaSchemaError",
                    )
                    raise OllamaSchemaError(
                        f"Ollama 'response' field is not a string: {type(raw_text)}"
                    )

                # --- Pass 1: normalize + json.loads ---
                # normalize_llm_payload raises OllamaSchemaError directly when
                # no '{' is present (D-06). That is a hard fail — Pass 2
                # cannot help (Pitfall 2). JSONDecodeError, however, advances
                # to the Pass 2 fence-strip retry.
                passes_used = 1
                try:
                    normalized = normalize_llm_payload(raw_text)
                    result = json.loads(normalized)
                    _LOGGER.debug(
                        "Ollama 200: response_len=%d parse_passes=%d",
                        len(raw_text),
                        passes_used,
                    )
                    return result, {"passes_used": passes_used}
                except OllamaSchemaError:
                    # D-06 / Pitfall 2: missing-'{' is a hard fail. Do NOT
                    # advance to Pass 2 — the fence-strip cannot synthesize
                    # a JSON object out of text that has none.
                    raise
                except json.JSONDecodeError:
                    pass  # Fall through to Pass 2.

                # --- Pass 2: fence-strip, then normalize + json.loads ---
                # D-05: fence-strip is a fallback, never the default.
                passes_used = 2
                m = _FENCE_RE.match(raw_text.strip())
                stripped = m.group(1) if m else raw_text

                try:
                    normalized = normalize_llm_payload(stripped)
                    result = json.loads(normalized)
                    _LOGGER.debug(
                        "Ollama 200: response_len=%d parse_passes=%d",
                        len(raw_text),
                        passes_used,
                    )
                    return result, {"passes_used": passes_used}
                except (OllamaSchemaError, json.JSONDecodeError) as err:
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        type(err).__name__,
                    )
                    raise OllamaSchemaError(
                        "Ollama response could not be parsed after fence-strip retry"
                    ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            # WR-04: catch the aiohttp base class — ClientPayloadError (connection
            # lost mid-body / invalid transfer encoding) subclasses ClientError but
            # NOT ClientConnectionError, and previously escaped as a raw aiohttp
            # exception that aborted the whole poll cycle.
            _LOGGER.debug(
                "Ollama failure: status=%s err_class=%s",
                None,
                type(err).__name__,
            )
            raise OllamaTransientError(f"Ollama network error: {err}") from err
