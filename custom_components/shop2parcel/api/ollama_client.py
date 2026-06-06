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

    async def async_generate(self, prompt: str, schema: dict) -> dict:
        """POST to /api/generate and return the parsed structured-output dict.

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
          TimeoutError, aiohttp.ClientConnectionError → OllamaTransientError

        Raises:
            OllamaSchemaError: HTTP 401/403/404, envelope JSON decode failure,
                envelope missing 'response' key, 'response' not a str, Pass 1
                missing-'{', or Pass 2 parse failure.
            OllamaTransientError: HTTP >=500, other 4xx, TimeoutError, or
                aiohttp.ClientConnectionError.
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": 4096},
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
                    return result
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
                    return result
                except (OllamaSchemaError, json.JSONDecodeError) as err:
                    _LOGGER.debug(
                        "Ollama failure: status=%s err_class=%s",
                        resp.status,
                        type(err).__name__,
                    )
                    raise OllamaSchemaError(
                        "Ollama response could not be parsed after fence-strip retry"
                    ) from err
        except (TimeoutError, aiohttp.ClientConnectionError) as err:
            _LOGGER.debug(
                "Ollama failure: status=%s err_class=%s",
                None,
                type(err).__name__,
            )
            raise OllamaTransientError(f"Ollama network error: {err}") from err
