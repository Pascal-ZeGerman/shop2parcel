"""Regression coverage for custom_components.shop2parcel.correlation.

Ports spike 029's Category A/B/C contamination-check cases (spikes 028/029/030) plus the new
RESEARCH.md Pitfall-1 case: a correlated match with an unknown target order must NOT be
false-flagged as contaminated just because it (correctly) contains its own order number. See
correlation.py's module docstring and RESEARCH.md's "Common Pitfalls / Pitfall 1" section for the
full rationale behind the patched `find_other_shipment_tokens` this module tests.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.shop2parcel.correlation import is_contaminated

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
