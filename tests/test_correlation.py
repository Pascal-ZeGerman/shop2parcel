"""Regression coverage for custom_components.shop2parcel.correlation.

Ports spike 029's Category A/B/C contamination-check cases (spikes 028/029/030) plus the new
RESEARCH.md Pitfall-1 case: a correlated match with an unknown target order must NOT be
false-flagged as contaminated just because it (correctly) contains its own order number. See
correlation.py's module docstring and RESEARCH.md's "Common Pitfalls / Pitfall 1" section for the
full rationale behind the patched `find_other_shipment_tokens` this module tests.

The five cases in TestKnownRealShapes below (see `test_known_real_shapes` below) encode
previously-validated evidence from spikes 028/029/030 (`.planning/spikes/029-attribution-
heuristic-candidate/README.md`'s Category A/B/C taxonomy and
`run_attribution_test.py`'s corpus cases), not freshly-invented expectations. Every one of these
five cases is driven by the *tracking*-token branch or by a known `target_order`, neither of which
RESEARCH.md's Pitfall-1 patch touches, so all five expectations are unchanged from the spike
verdicts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.shop2parcel.correlation import is_contaminated, sanitize_search_terms

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "correlation"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_unknown_target_order_single_order_is_not_contaminated() -> None:
    """Pitfall-1 RED case: the unpatched spike code returns True here. A single-order Amazon
    confirmation whose only token is the order number must be usable for naming even when the
    target shipment's own order identifier is not yet known (target_order=None)."""
    html = _load("amazon_shoes_confirmation.html")
    contaminated, others = is_contaminated(
        html, target_tracking="1Z888BB29876543210", target_order=None
    )
    assert contaminated is False
    assert others == set()


def test_known_target_order_unchanged() -> None:
    """Known-target path unchanged: the same fixture, correlated by its own (already-known)
    order number, is still not contaminated."""
    html = _load("amazon_shoes_confirmation.html")
    contaminated, others = is_contaminated(
        html, target_tracking=None, target_order="113-5838173-8241820"
    )
    assert contaminated is False
    assert others == set()


def test_multi_order_unknown_target_is_contaminated() -> None:
    """A synthetic body containing two distinct order-shaped tokens and no tracking token,
    called with target_order=None, is contaminated -- the confirmed real threat shape
    (multi-order receipt) must still be flagged."""
    html = """<html><body>
    <p>Order #10015 shipped separately from order #10026.</p>
    <p>Both items are on their way.</p>
    </body></html>"""
    contaminated, others = is_contaminated(html, target_tracking=None, target_order=None)
    assert contaminated is True
    assert others == {"10015", "10026"}


def test_duplicate_order_token_normalizes_to_one_distinct_order() -> None:
    """The same order number written twice (once bare, once with a leading hash), called with
    target_order=None, normalizes to a single distinct order -- the multi-order branch must not
    fire."""
    html = """<html><body>
    <p>Your order #10015 has shipped.</p>
    <p>Reference: order 10015 for your records.</p>
    </body></html>"""
    contaminated, others = is_contaminated(html, target_tracking=None, target_order=None)
    assert contaminated is False
    assert others == set()


def test_find_other_shipment_tokens_known_target_keeps_original_behavior() -> None:
    """With a non-empty target_order, every order token whose normalized form differs from the
    target is returned -- the original (pre-patch) behavior for the known-target path."""
    from custom_components.shop2parcel.correlation import find_other_shipment_tokens

    html = """<html><body>
    <p>Order #10015 shipped. A separate order #10026 shipped too.</p>
    </body></html>"""
    others = find_other_shipment_tokens(html, target_tracking=None, target_order="10015")
    assert others == {"10026"}


@pytest.mark.parametrize(
    ("fixture_name", "target_tracking", "target_order", "expect_contaminated", "expect_in_others"),
    [
        pytest.param(
            "multi_order_receipt.html",
            "1Z999AA10123456784",
            None,
            True,
            "1Z888BB29876543210",
            id="multi_order_receipt_digest_order_1001_perspective",
        ),
        pytest.param(
            "multi_order_receipt.html",
            "1Z888BB29876543210",
            None,
            True,
            "1Z999AA10123456784",
            id="multi_order_receipt_digest_order_1002_perspective",
        ),
        pytest.param(
            "forwarded_old_order.html",
            "1Z777CC30987654321",
            None,
            True,
            None,
            id="forwarded_digest_quoting_old_order",
        ),
        pytest.param(
            "clean_single_with_noise.html",
            "1Z888BB29876543210",
            None,
            False,
            None,
            id="clean_single_with_noise_not_flagged",
        ),
        pytest.param(
            "amazon_shoes_confirmation.html",
            None,
            "113-5838173-8241820",
            False,
            None,
            id="amazon_known_order_not_flagged",
        ),
    ],
)
def test_known_real_shapes(
    fixture_name: str,
    target_tracking: str | None,
    target_order: str | None,
    expect_contaminated: bool,
    expect_in_others: str | None,
) -> None:
    """Spike 029 Category A (confirmed real digest threat) + Category B/C (false-positive and
    adversarial-clean checks) — see module docstring for provenance. None of these five cases
    exercise RESEARCH.md's Pitfall-1 patch (all use a known target_order or the tracking-token
    branch), so all five expectations are unchanged from the original spike verdicts."""
    html = _load(fixture_name)
    contaminated, others = is_contaminated(html, target_tracking, target_order)
    assert contaminated is expect_contaminated
    if expect_in_others is not None:
        assert expect_in_others in others


# ---------------------------------------------------------------------------
# Plan 37-08 Task 1: sanitize_search_terms (T-37-25/T-37-26 allowlist guard)
# ---------------------------------------------------------------------------


def test_sanitize_search_terms_plain_tracking_number_unchanged() -> None:
    """A plain alphanumeric tracking number passes through unchanged."""
    assert sanitize_search_terms(["1Z999AA10123456784"]) == ["1Z999AA10123456784"]


def test_sanitize_search_terms_hyphenated_amazon_order_unchanged() -> None:
    """A hyphenated order number such as the Amazon three-part form passes through
    unchanged."""
    assert sanitize_search_terms(["113-5838173-8241820"]) == ["113-5838173-8241820"]


def test_sanitize_search_terms_carriage_return_dropped() -> None:
    """A term containing a carriage return is dropped entirely, not escaped."""
    assert sanitize_search_terms(["1Z999AA1012345\r6784"]) == []


def test_sanitize_search_terms_line_feed_dropped() -> None:
    """A term containing a line feed is dropped entirely, not escaped."""
    assert sanitize_search_terms(["1Z999AA1012345\n6784"]) == []


def test_sanitize_search_terms_newline_command_injection_dropped() -> None:
    """A term whose newline is followed by text resembling an additional IMAP command is
    dropped in its entirety -- the whole term, not just the injected suffix."""
    malicious = "1Z999AA10123456784\r\nA1 STORE 1 +FLAGS (\\Deleted)"
    assert sanitize_search_terms([malicious]) == []


def test_sanitize_search_terms_double_quote_dropped() -> None:
    """A term containing a double quote is dropped."""
    assert sanitize_search_terms(['1Z999"AA10123456784']) == []


def test_sanitize_search_terms_space_dropped() -> None:
    """A term containing a space is dropped."""
    assert sanitize_search_terms(["a b"]) == []


def test_sanitize_search_terms_gmail_operator_colon_dropped() -> None:
    """A term containing a Gmail search operator colon is dropped."""
    assert sanitize_search_terms(["from:evil@example.com"]) == []


def test_sanitize_search_terms_too_short_dropped() -> None:
    """A term shorter than 4 characters is dropped."""
    assert sanitize_search_terms(["abc"]) == []


def test_sanitize_search_terms_too_long_dropped() -> None:
    """A term longer than 64 characters is dropped."""
    assert sanitize_search_terms(["A" * 65]) == []


def test_sanitize_search_terms_max_length_boundary_kept() -> None:
    """A term of exactly 64 characters is kept (inclusive upper bound)."""
    term = "A" * 64
    assert sanitize_search_terms([term]) == [term]


def test_sanitize_search_terms_empty_input_yields_empty_output() -> None:
    """Empty input yields empty output."""
    assert sanitize_search_terms([]) == []


def test_sanitize_search_terms_duplicates_collapsed_preserving_order() -> None:
    """Duplicate terms are collapsed while preserving first-seen order."""
    assert sanitize_search_terms(["1Z999AA10123456784", "1Z999AA10123456784"]) == [
        "1Z999AA10123456784"
    ]
    assert sanitize_search_terms(["ORDER-1001", "TRACK-2002", "ORDER-1001"]) == [
        "ORDER-1001",
        "TRACK-2002",
    ]


def test_sanitize_search_terms_none_and_blank_entries_dropped() -> None:
    """None and blank/whitespace-only entries are dropped silently."""
    assert sanitize_search_terms([None, "", "   ", "1Z999AA10123456784"]) == ["1Z999AA10123456784"]
