"""LLM payload normalization utilities for Shop2Parcel Stage-2 extraction.

Pure stdlib — no aiohttp, no HA imports.
Reused by:
  - api/ollama_client.py        (Phase 15) — normalize before json.loads
  - extractors/ollama_extractor.py  (Phase 16) — optional post-extraction
  - extractors/merge.py          (Phase 20) — normalize_llm_tracking_number (TODO)

Pipeline owned by normalize_llm_payload:
  1. NFKC normalize (folds full-width Ｚ → Z, compatibility chars)
  2. Strip zero-width + BOM (U+200B, U+200C, U+200D, U+FEFF)
  3. Extract substring from first '{' to last '}' inclusive
  4. Raise OllamaSchemaError if no '{' / '}' present (D-06)

Privacy posture (D-07/D-08/D-09): exception messages reference only the input
length, never the raw response content.
"""

from __future__ import annotations

import unicodedata

from .exceptions import OllamaSchemaError

# Zero-width + BOM characters to strip from LLM output (PITFALLS.md I-9).
# U+200B ZERO WIDTH SPACE, U+200C ZERO WIDTH NON-JOINER,
# U+200D ZERO WIDTH JOINER, U+FEFF BOM / ZERO WIDTH NO-BREAK SPACE.
_ZERO_WIDTH_CHARS: frozenset[str] = frozenset(
    ("\u200b", "\u200c", "\u200d", "\ufeff")
)


def normalize_llm_payload(raw: str) -> str:
    """Normalize a raw LLM response string before json.loads.

    Pipeline (order matters per D-01 and SPEC Req 4):

    1. NFKC normalize — folds full-width digits (Ｚ → Z) and other Unicode
       compatibility characters into their canonical ASCII equivalents.
       Does NOT fold homoglyphs like Cyrillic А (U+0410) — those remain
       distinct from Latin A (U+0041) under NFKC and are caught downstream
       by Phase 20 carrier-regex pre-POST validation.
    2. Strip zero-width + BOM characters (U+200B, U+200C, U+200D, U+FEFF).
    3. Extract substring from first ``{`` to last ``}`` inclusive — discards
       leading prose, trailing prose, and trailing markdown fences.
    4. If no ``{`` or no ``}`` is present, raise OllamaSchemaError directly
       (decision D-06 — missing-brace is a hard fail; no retry can recover).

    Returns the normalized substring, ready for ``json.loads``.

    Raises:
        OllamaSchemaError: if the post-normalize text contains no ``{`` or
            no ``}``, or ``}`` precedes ``{``. Message references only the
            input length, never the raw content (D-07/D-08/D-09).
    """
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(c for c in text if c not in _ZERO_WIDTH_CHARS)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise OllamaSchemaError(
            f"No JSON object found in LLM response (len={len(raw)})"
        )
    return text[start : end + 1]


# TODO(Phase 20): add normalize_llm_tracking_number(tn: str) -> str.
# Pipeline: NFKC + zero-width strip + .strip().upper().
# Consumed by Phase 20 carrier-regex pre-POST validation (MRG-04).
