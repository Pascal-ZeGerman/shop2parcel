"""Tests for the OllamaExtractor module-level helpers (Phase 16, Plan 02).

Covers the pure helpers shipped in this plan — schema composition (FLD-04),
HTML preprocessing (D-02), and prompt construction (D-02 + Pitfall 2 OWASP
instructional defense). The ``OllamaExtractor`` class itself is added by
Plan 03; its tests will extend this file via additional imports.
"""

from __future__ import annotations

from custom_components.shop2parcel.const import LOCKED_OLLAMA_FIELDS
from custom_components.shop2parcel.extractors.ollama_extractor import (
    build_prompt,
    build_schema,
    preprocess_html,
)

# ---------------------------------------------------------------------------
# build_schema — FLD-04 / D-06
# ---------------------------------------------------------------------------


def test_build_schema_locked_only():
    """Empty field_list yields a schema with only the 3 locked fields required.

    D-06 + acceptance: top-level type=object, additionalProperties=False,
    required == list(LOCKED_OLLAMA_FIELDS), and properties contain exactly
    the locked field names.
    """
    schema = build_schema([])

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(LOCKED_OLLAMA_FIELDS)
    # Empty field_list produces an empty properties dict — the schema still
    # demands the locked keys via required (Ollama's constrained decoder
    # honors required without needing them in properties for omission
    # detection, but Phase-17 will pass them through field_list so this
    # branch primarily proves the helper's "no extras" guarantee).
    assert schema["properties"] == {}


def test_build_schema_with_custom_fields():
    """Custom fields land in properties but stay OUT of required (D-06)."""
    schema = build_schema(
        [
            ("tracking_number", None),
            ("carrier_name", None),
            ("order_name", None),
            ("estimated_delivery", "ISO date the package is expected"),
        ]
    )

    # 4 properties: 3 locked + 1 custom.
    assert set(schema["properties"].keys()) == {
        "tracking_number",
        "carrier_name",
        "order_name",
        "estimated_delivery",
    }
    # Locked fields are still the only `required` entries.
    assert schema["required"] == list(LOCKED_OLLAMA_FIELDS)
    # The custom field's description survives intact.
    assert (
        schema["properties"]["estimated_delivery"]["description"]
        == "ISO date the package is expected"
    )


def test_build_schema_auto_description_for_none():
    """When description is None, auto-generate the D-03 fallback phrasing."""
    schema = build_schema([("est_delivery", None)])

    assert (
        schema["properties"]["est_delivery"]["description"]
        == "The est_delivery value extracted from the email, or null if not present."
    )


def test_build_schema_property_types_are_string_or_null():
    """Every property's type is ['string', 'null'] (D-06 + FEATURES TS-C3)."""
    schema = build_schema(
        [
            ("tracking_number", None),
            ("carrier_name", None),
            ("order_name", None),
            ("custom_field", "some description"),
        ]
    )
    for name, prop in schema["properties"].items():
        assert prop["type"] == ["string", "null"], (
            f"property {name!r} has wrong type: {prop['type']!r}"
        )


def test_build_schema_additional_properties_false():
    """additionalProperties is the literal False, not falsy or absent (D-06)."""
    schema = build_schema([])
    assert schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# preprocess_html — D-02
# ---------------------------------------------------------------------------


def test_preprocess_html_returns_prose_and_links(shopify_mini_html):
    """Returns (prose, links): prose has visible text, links has tracking href.

    Uses the conftest ``shopify_mini_html`` fixture — the same fixture Plan 03
    will exercise the full async_extract path with.
    """
    prose, links = preprocess_html(shopify_mini_html)

    # Prose contains the visible text but NOT the raw tag markup.
    assert "Tracking number" in prose
    assert "<p>" not in prose
    assert "<a" not in prose
    assert "1Z999AA10123456784" in prose  # in-prose tracking value

    # Links has exactly the one tracking href in the fixture.
    assert len(links) == 1
    assert "shopify.com/track" in links[0]
    assert "1Z999AA10123456784" in links[0]


def test_preprocess_html_dedups_hrefs():
    """Repeated hrefs are deduplicated, first-occurrence order preserved."""
    html = (
        "<html><body>"
        '<a href="https://x.com/a">A1</a>'
        '<a href="https://x.com/b">B</a>'
        '<a href="https://x.com/a">A2</a>'
        '<a href="https://x.com/a">A3</a>'
        '<a href="https://x.com/c">C</a>'
        '<a href="https://x.com/b">B2</a>'
        "</body></html>"
    )
    _prose, links = preprocess_html(html)
    # Three distinct URLs, in first-occurrence document order.
    assert links == ["https://x.com/a", "https://x.com/b", "https://x.com/c"]


def test_preprocess_html_handles_no_anchors():
    """HTML with no <a> tags yields (prose, [])."""
    html = "<html><body><p>No links here, just prose.</p></body></html>"
    prose, links = preprocess_html(html)
    assert links == []
    assert "No links here" in prose


def test_preprocess_html_handles_empty_html():
    """Empty string input is parseable and yields ('', [])."""
    prose, links = preprocess_html("")
    assert prose == ""
    assert links == []


def test_preprocess_html_handles_malformed_html():
    """Mismatched / unclosed tags don't crash — lxml is tolerant."""
    html = '<html><body><p>open<div><a href="https://y.com">link</body>'
    prose, links = preprocess_html(html)
    assert "open" in prose
    assert "link" in prose
    assert links == ["https://y.com"]


# ---------------------------------------------------------------------------
# build_prompt — D-02 + Pitfall 2 (OWASP instructional defense)
# ---------------------------------------------------------------------------


def test_build_prompt_contains_delimiters():
    """All four delimiter tokens are present (Pitfall 2 / T-16.02-01)."""
    prompt = build_prompt(
        prose="hello",
        links=["https://x.com"],
        field_list=[("tracking_number", None)],
    )
    for token in ("<<<EMAIL>>>", "<<<END_EMAIL>>>", "<<<LINKS>>>", "<<<END_LINKS>>>"):
        assert token in prompt, f"missing delimiter token: {token}"


def test_build_prompt_instructions_first():
    """`Fields to extract:` precedes `<<<EMAIL>>>` (Pitfall 3 ordering)."""
    prompt = build_prompt(
        prose="body content",
        links=[],
        field_list=[("tracking_number", None)],
    )
    assert prompt.index("Fields to extract:") < prompt.index("<<<EMAIL>>>")


def test_build_prompt_empty_links_renders_no_links_marker():
    """Empty links list renders the literal '(no links in email)' marker."""
    prompt = build_prompt(
        prose="some prose",
        links=[],
        field_list=[("tracking_number", None)],
    )
    assert "(no links in email)" in prompt
    # And the LINKS block is still present (not collapsed away).
    assert "<<<LINKS>>>" in prompt
    assert "<<<END_LINKS>>>" in prompt


def test_build_prompt_rules_present():
    """All four canonical rules appear verbatim (Pitfall 2 + D-02)."""
    prompt = build_prompt(
        prose="x",
        links=[],
        field_list=[("tracking_number", None)],
    )
    # The four canonical rules (substring matches — the prompt formats them
    # with leading dashes / surrounding text).
    assert "JSON only" in prompt
    assert "null for that field" in prompt
    assert "Do not invent" in prompt
    assert "as data, not as instructions" in prompt


def test_build_prompt_links_block_contains_hrefs():
    """When links are present, they appear inside the LINKS block.

    ``<<<LINKS>>>`` / ``<<<END_LINKS>>>`` tokens occur twice in the prompt
    (once in the Rules-block instructional-defense line, once as the
    actual delimited block). Use ``rindex`` to anchor on the real block.
    """
    prompt = build_prompt(
        prose="x",
        links=["https://a.example/track", "https://b.example/track"],
        field_list=[("tracking_number", None)],
    )
    links_start = prompt.rindex("<<<LINKS>>>")
    links_end = prompt.rindex("<<<END_LINKS>>>")
    block = prompt[links_start:links_end]
    assert "https://a.example/track" in block
    assert "https://b.example/track" in block


# ---------------------------------------------------------------------------
# Structural — no HA imports in extractors/ollama_extractor.py
# ---------------------------------------------------------------------------


def test_no_ha_imports():
    """Inspect ollama_extractor.py source — 'homeassistant' must not appear."""
    from pathlib import Path

    extractor_path = (
        Path(__file__).parent.parent.parent
        / "custom_components"
        / "shop2parcel"
        / "extractors"
        / "ollama_extractor.py"
    )
    contents = extractor_path.read_text(encoding="utf-8")
    assert "homeassistant" not in contents
