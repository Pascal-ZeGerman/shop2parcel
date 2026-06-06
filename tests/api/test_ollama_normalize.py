"""Tests for normalize_llm_payload — pure-stdlib LLM response normalization.

No aiohttp or aioresponses needed — normalizer is pure stdlib.
Covers SPEC.md Req 4 (OLLM-06) acceptance fixtures and decision D-06
(no-`{` short-circuit raises OllamaSchemaError).

Invisible Unicode characters are written as escape sequences
(``\\u200b``, ``\\u200c``, ``\\u200d``, ``\\ufeff``, ``\\uff3a``, ``\\u0410``)
rather than literal invisible characters in source — per the plan's
acceptance criterion, no literal U+200B / U+200C / U+200D / U+FEFF /
U+FF3A / U+0410 appear in this file outside of escape form.
"""

from __future__ import annotations

import pytest

from custom_components.shop2parcel.api.exceptions import OllamaSchemaError
from custom_components.shop2parcel.api.ollama_normalize import normalize_llm_payload


def test_nfkc_fullwidth_digit_folding() -> None:
    """Full-width Z (U+FF3A) folds to ASCII Z via NFKC."""
    raw = '{"a":"\uff3a"}'
    result = normalize_llm_payload(raw)
    assert "Z" in result
    assert "\uff3a" not in result


def test_nfkc_cyrillic_lookalike() -> None:
    """Cyrillic A (U+0410) — document NFKC behavior with homoglyphs.

    NFKC does NOT fold Cyrillic A (U+0410) to Latin A (U+0041); they are
    semantically distinct code points and NFKC preserves them. This test
    locks the current behavior so any future change to the normalization
    policy (e.g. adding a homoglyph fold step) shows up here first.

    Rationale: SPEC.md Req 4 mentions Cyrillic-A lookalike folding as a
    motivating example, but the locked pipeline (D-01) is exactly
    NFKC + zero-width strip + brace extraction. NFKC alone does not handle
    Cyrillic A. Phase 20 carrier-regex pre-POST validation
    (normalize_llm_tracking_number, deferred per D-02) is the layer that
    would catch any remaining homoglyph slips on actual tracking numbers.
    """
    raw = '{"a":"\u0410"}'
    result = normalize_llm_payload(raw)
    # NFKC preserves Cyrillic A — the value remains U+0410 inside the braces.
    assert result == '{"a":"\u0410"}'


def test_zero_width_char_stripped() -> None:
    """Each of U+200B, U+200C, U+200D, U+FEFF inside a {...} payload is removed."""
    zero_width_chars = ("\u200b", "\u200c", "\u200d", "\ufeff")
    for ch in zero_width_chars:
        raw = '{"a":"x' + ch + 'y"}'
        result = normalize_llm_payload(raw)
        assert ch not in result, f"zero-width char {ch!r} should be stripped"
        assert result == '{"a":"xy"}'


def test_brace_extraction() -> None:
    """Prose prefix + {...} + prose suffix → only the {...} returned."""
    raw = 'prose prefix {"a": 1} prose suffix'
    result = normalize_llm_payload(raw)
    assert result == '{"a": 1}'


def test_no_open_brace_raises() -> None:
    """Input with no '{' → OllamaSchemaError (D-06)."""
    with pytest.raises(OllamaSchemaError):
        normalize_llm_payload("No JSON here at all")


def test_no_close_brace_raises() -> None:
    """Input with '{' but no '}' → OllamaSchemaError (D-06 extension)."""
    with pytest.raises(OllamaSchemaError):
        normalize_llm_payload('prefix {"a": 1')


def test_clean_json_passthrough() -> None:
    """Clean JSON string returns the same string unchanged."""
    assert normalize_llm_payload('{"a": 1}') == '{"a": 1}'


def test_combined_fixture_from_spec() -> None:
    """SPEC Req 4 combined acceptance fixture: BOM + full-width + Cyrillic + ZWSP.

    Fixture (escapes only):
      ``"\\ufeff{\\"tracking_number\\":\\"1\\uff3a999\\u0410\\u04101\\"}\\u200b"``

    Pipeline-by-step expectation (per D-01 four-step recipe):
      1. NFKC normalize → ``\\ufeff`` stays, ``\\uff3a`` → ``Z``,
         ``\\u0410`` stays, ``\\u200b`` stays.
      2. Strip ``\\u200b``/``\\u200c``/``\\u200d``/``\\ufeff`` → BOM
         and trailing ZWSP gone.
      3. Extract from first ``{`` to last ``}`` → already the whole
         post-strip string.
      4. Return that substring.

    Final expected value matches the NFKC-correct output. The SPEC document
    (15-SPEC.md Req 4) shows ``1Z999AA1`` (with Latin A) as the acceptance
    string, but that is a SPEC-text inaccuracy — NFKC alone does not fold
    Cyrillic A to Latin A (NFKC preserves them as distinct code points).
    The locked pipeline (D-01) is NFKC + zero-width strip + brace extraction;
    no additional homoglyph fold is in scope for Phase 15. This test locks
    the implementation-correct output so the normalizer can be relied on
    for downstream phases.
    """
    raw = '\ufeff{"tracking_number":"1\uff3a999\u0410\u04101"}\u200b'
    result = normalize_llm_payload(raw)
    assert result == '{"tracking_number":"1Z999\u0410\u04101"}'
