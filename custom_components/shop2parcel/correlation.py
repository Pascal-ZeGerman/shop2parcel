"""Promoted spike asset (spikes 028/029/030, D-07): a sender-agnostic contamination check for
Phase 37's correlated-email sweep.

Before a correlated-match email (found by searching a shipment's own mailbox for its tracking
number or order number) is fed to Stage-2 for naming, this module answers: does this email
describe MORE than one shipment? If so, it is "contaminated" and must not be used for naming this
shipment via the generic path — skip it and retry on the next sweep cycle (safe default, never
guess).

This is a pre-gate, complementary to (not a replacement for) MRG-05's ``validate_grounding()`` in
``merge.py``: MRG-05 checks whether a naming value is textually present in body-only prose at all;
``is_contaminated()`` checks whether the source email is scoped to a SINGLE shipment in the first
place. Both apply before a correlated match can rename anything.

Spike 028 confirmed the real, reachable threat is not coincidental token collision between
unrelated orders (never observed across 27 corpus files) — it's an email that legitimately
contains the target shipment's own tracking/order token but ALSO contains one or more OTHER
tracking/order-shaped tokens, meaning the email describes more than one shipment (the confirmed
real example: a multi-package USPS Informed Delivery digest).

Known v1 limitation (D-07, deliberately accepted, not fixed here): real USPS/Informed-Delivery
template boilerplate (a "sign up for text alerts" link, a ``mailpiece=`` marketing param) can
trigger a false "contaminated" flag on a genuine single-shipment email. This is safe-by-default —
a false positive only means the shipment keeps its current name and retries on the next sweep; it
never produces a WRONG name. The fix (a known-template denylist or a token-proximity window) is a
fast-follow, not part of this phase.
"""

from __future__ import annotations

import re

from .api.email_parser import _TRACKING_PATTERNS

# Same broad token-shape scan as spike 028: any 9-30 char alnum run that matches a real
# tracking-number pattern.
_TRACKING_CANDIDATE_RE = re.compile(r"\b[0-9A-Z]{9,30}\b")

# Tightened from spike 028's ORDER_CONTEXT_RE: require the captured token to actually look like
# a code (at least one digit, not pure alphabetic) so "order ... contains ..." style prose can't
# match. Still deliberately loose on shape (order-number formats vary by retailer) but this one
# guard eliminates the false positive spike 028 found in 6 corpus files.
_ORDER_CONTEXT_RE = re.compile(
    r"(?:order\s*(?:#|number|no\.?|num)?\s*[:#]?\s*)([A-Za-z0-9][A-Za-z0-9\-_]{4,24})",
    re.IGNORECASE,
)


def _looks_like_order_code(token: str) -> bool:
    """Reject pure-alphabetic matches (e.g. "contains", "was", "has") -- a real order number
    always has at least one digit."""
    return any(ch.isdigit() for ch in token)


def extract_tracking_tokens(text: str) -> set[str]:
    upper = text.upper()
    candidates = _TRACKING_CANDIDATE_RE.findall(upper)
    return {c for c in candidates if any(p.match(c) for p in _TRACKING_PATTERNS)}


def extract_order_tokens(text: str) -> set[str]:
    raw = {m.group(1) for m in _ORDER_CONTEXT_RE.finditer(text)}
    return {t for t in raw if _looks_like_order_code(t)}


def _normalize_order_id(value: str | None) -> str:
    """Stage-1's own order_name regex sometimes captures a leading '#' and sometimes
    doesn't (observed: 'shopify_shipping_email.html' -> '#1234', this module's own
    ORDER_CONTEXT_RE capture group -> '1234' with no '#'). Strip it so a shipment's
    own order number compares equal to itself regardless of which extractor produced
    which representation -- see spike 029's Investigation Trail (bloomwild false positive)."""
    return (value or "").upper().lstrip("#").strip()


def find_other_shipment_tokens(
    source_text: str,
    target_tracking: str | None = None,
    target_order: str | None = None,
) -> set[str]:
    """Return every tracking/order-shaped token in source_text OTHER than the target
    token(s) used to correlate this email to the shipment being named.

    Order-token branch (RESEARCH.md Pitfall 1 patch): spike 028's evidence-backed threat is an
    email describing multiple shipments, not a single-order email. When the target shipment's own
    order identifier is not yet known (``target_order`` is empty/None -- the exact "Amazon - Shoes"
    motivating case: a carrier email with a tracking number but no order number, correlated
    forward to find the order-confirmation email that does have one), adopting a LONE order-shaped
    token is exactly this feature's purpose, not a contamination signal -- so it is NOT counted as
    "other" in that case. Only flag order tokens in this branch when the email itself contains more
    than one DISTINCT normalized order-shaped token -- that shape (multi-order receipt,
    multi-package digest) is the confirmed real threat from spike 028's evidence. This is the one
    not-safe-by-default decision in this module's design (RESEARCH.md Assumption A3): a single-order
    email matched only by tracking number that happens to describe a genuinely different order is
    the residual risk, accepted for v1.
    """
    others: set[str] = set()
    target_tracking_norm = (target_tracking or "").upper()
    target_order_norm = _normalize_order_id(target_order)
    for t in extract_tracking_tokens(source_text):
        if t.upper() != target_tracking_norm:
            others.add(t)
    order_tokens = extract_order_tokens(source_text)
    if target_order_norm:
        # Known target: every order token whose normalized form differs from the target is
        # "other" -- unchanged from the original spike behavior.
        others.update(o for o in order_tokens if _normalize_order_id(o) != target_order_norm)
    elif len({_normalize_order_id(o) for o in order_tokens}) > 1:
        # Unknown target: only flag when the email itself describes MULTIPLE distinct orders.
        others.update(order_tokens)
    return others


def is_contaminated(
    source_text: str,
    target_tracking: str | None = None,
    target_order: str | None = None,
) -> tuple[bool, set[str]]:
    """The candidate gate. Returns (contaminated, other_tokens_found).

    contaminated=True means: this correlated-match email mentions at least one OTHER
    shipment's tracking/order token, so its text is not safely scoped to the target shipment.
    Phase 37's naming path should NOT feed this email to Stage-2 for naming (safe default:
    skip, don't attempt a same-block scoping trick -- that's carrier-template-specific, per
    _extract_usps_shippers, and doesn't generalize to arbitrary senders).
    """
    others = find_other_shipment_tokens(source_text, target_tracking, target_order)
    return bool(others), others
