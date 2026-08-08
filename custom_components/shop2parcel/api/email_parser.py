"""Tiered email parser for Shopify shipping confirmation emails.

Strategy 1 (primary): BeautifulSoup on <p> and <td> text patterns + href fallback.
  Shopify embeds tracking info as prose in <p>/<td> elements; no CSS classes on
  tracking data. Href fallback catches tracking numbers embedded in anchor URLs.

Strategy 2 (Tier 1 regex): stdlib re with labeled keyword anchors + href fallback.
  Used when HTML strategy fails (custom merchant templates, non-Shopify shippers).

Strategy 3 (Tier 2 broad scan): bare token sweep — no keyword gate.
  Used when Tier 1 finds no labeled tracking. Collects all tracking-shaped tokens
  from full text and hrefs; returns best (longest) match. Maximises recall at the
  cost of precision — false positives are filtered in a later phase.

EMAIL-03 locks the dual-strategy requirement; Tier 2 is an extension.
No HA imports (D-01/D-03).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .carrier_codes import normalize_carrier

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ShipmentData:
    """Structured output from EmailParser. Coordinator data type for Phase 4.

    carrier_name: raw Shopify string (e.g. "UPS", "Canada Post").
    Caller passes carrier_name to carrier_codes.normalize_carrier() before POSTing.
    message_id: stable identifier — Gmail message ID or IMAP UID as string.
    email_date: Unix timestamp (seconds) from Gmail internalDate (0 for IMAP).
    """

    tracking_number: str
    carrier_name: str
    order_name: str  # e.g. "#1234" or "#AB-1234"; "" for direct carrier emails
    message_id: str  # stable message identifier — Gmail message ID (Gmail path) or IMAP UID as string (IMAP path)
    email_date: int  # Unix timestamp (seconds)
    custom_attributes: dict[str, str | None] = field(
        default_factory=dict
    )  # FLD-03: user-added Stage-2 extraction fields; surfaced as sensor attributes, never POSTed
    # LOH-SUMMARY: Stage-2 LLM merchant+contents summary; the parcelapp description source;
    # never POSTed as a tracking field. Defaults None so all existing positional/keyword
    # constructors remain valid (non-breaking). Populated only by merge_llm_authoritative
    # (via the locked-field replace loop) or by the gmail Stage-2 fallback ShipmentData builder.
    order_summary: str | None = None


@dataclass(slots=True, frozen=True)
class ParseResult:
    """Phase 7 (DIAG-01): instrumented return type for EmailParser.parse().

    Always fully populated — `keyword_hits` always has exactly the keys
    "tracking_regex", "order_regex", "carrier_regex" with bool values, even
    on HTML-strategy parses (all False in that case — D-07). This guarantees
    the coordinator can iterate the dict without key guards.

    `candidate_tokens` is populated by Tier 2 (broad scan) with all tracking-shaped
    tokens found — used for diagnostic surfacing so skip reasons can be sharpened.
    """

    shipment: ShipmentData | None
    skip_reason: (
        str | None
    )  # "no_template_match" | "no_tracking_label" | "tracking_invalid" | "no_tracking_pattern" | None
    strategy_used: str | None  # "html_template" | "regex_fallback" | "broad_regex" | None
    keyword_hits: dict[str, bool]  # keys always: tracking_regex, order_regex, carrier_regex
    candidate_tokens: list[str] = field(default_factory=list)
    # Additional shipments from multi-package digest emails (e.g. USPS Informed Delivery).
    # Empty for all single-shipment sources; populated only by _parse_usps when findall
    # yields more than one valid tracking number.
    extra_shipments: list[ShipmentData] = field(default_factory=list)


# Known tracking number format patterns (EMAIL-04).
# Patterns are bounded quantifiers — no ReDoS risk (ASVS V5).
# R5: DHL bare-digit pattern (^[0-9]{10,11}$) removed — too broad, causes false positives.
_TRACKING_PATTERNS = [
    re.compile(r"^1Z[A-Z0-9]{16}$"),  # UPS: 1Z999AA10123456784
    re.compile(r"^9[12345][0-9]{15,24}$"),  # USPS domestic: IMpb 91-95 (91=Priority Mail Express)
    re.compile(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$"),  # USPS international
    re.compile(
        r"^(?:[0-9]{12}|[0-9]{15}|[0-9]{20})$"
    ),  # FedEx: Express=12, Ground=15, SmartPost=20
    # ShipBob (.planning/debug/shipbob-carrier-unsupported.md): SB + 8-23
    # alnum chars (10-25 total). Safe to add to this shared list — unlike
    # DHL's deliberately-excluded bare-digit shape (Decision 1, 36-01-PLAN.md)
    # — because the literal "SB" letter prefix is a low-collision anchor
    # structurally analogous to UPS's "1Z" above, not a bare-digit shape.
    # Range is a considered generalization from the one confirmed real sample
    # (SBAAAAQLCQ6U4P269, SB+15=17 chars) — documented blind spot, no second
    # independent sample was obtainable.
    re.compile(r"^SB[A-Z0-9]{8,23}$"),
]


# Strategy constants (D-07) — module-level string constants for ParseResult.strategy_used.
# Tests import these to avoid bare string comparisons. Values are stable contract.
STRATEGY_HTML = "html_template"
STRATEGY_UPS = "ups_template"
STRATEGY_USPS = "usps_template"
STRATEGY_FEDEX = "fedex_template"
STRATEGY_DHL = "dhl_template"
STRATEGY_SHIPBOB = "shipbob_template"
STRATEGY_REGEX = "regex_fallback"
STRATEGY_BROAD_REGEX = "broad_regex"


# MRG-05/SC-3 split-scope constants — exported so tests can assert against them
# directly (mirrors the STRATEGY_* export convention above). order_name/carrier_name
# label regexes stay LABEL_TAGS-scoped (unchanged) to avoid picking up footer/support
# boilerplate like "Reference Order #99999 when you contact support" as the real
# order number. The tracking-number shape-sniffing pass is broadened to TRACKING_TAGS
# to recover custom-Shopify-theme emails that put shipment data in <div>/<span>
# instead of <p>/<td> — no new regex is introduced, the existing bounded
# _looks_like_tracking() check just runs over more elements (ASVS V5, no ReDoS
# risk change).
LABEL_TAGS = ["p", "td"]
TRACKING_TAGS = ["p", "td", "div", "span"]


# Carrier-specific extraction regex — compiled at import, bounded quantifiers (ASVS V5).
# Used by carrier template parse_fn before _looks_like_tracking() validation.
# T-ReDoS mitigation: every quantifier is bounded; no `+` or `*` on character classes.
_UPS_TRACKING_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")
_USPS_TRACKING_RE = re.compile(r"\b(9[12345][0-9]{15,24})\b")
_FEDEX_TRACKING_RE = re.compile(
    r"(?:tracking\s+(?:number|#|no\.?|id\b)\s*:?\s*)([0-9]{12}|[0-9]{15}|[0-9]{20})\b",
    re.IGNORECASE,
)
# ShipBob (.planning/debug/shipbob-carrier-unsupported.md): bare bounded token
# match (mirrors _UPS_TRACKING_RE's structure) — no label anchor required,
# since the "SB" prefix itself is the low-collision anchor (like UPS's "1Z").
_SHIPBOB_TRACKING_RE = re.compile(r"\b(SB[A-Z0-9]{8,23})\b")


def _looks_like_tracking(s: str) -> bool:
    """Return True if s matches any known carrier tracking number format."""
    return any(p.match(s) for p in _TRACKING_PATTERNS)


def validate_carrier_format(
    value: str | None, *, carrier_name: str | None = None
) -> tuple[str, bool, str | None]:
    """Validate a carrier tracking number against all known carrier patterns.

    Strips internal ``[ -]`` separators and uppercases the input to produce the
    single canonical clean form (D-03). This clean form is the value used for
    validation, dedup, and the parcelapp POST — callers must not normalise further.

    ``carrier_name`` (quick-260807-tpu, additive/optional, keyword-only): the
    default ``None`` preserves the EXACT prior carrier-agnostic behaviour for
    every existing caller and every existing test — this call path is
    byte-identical to the pre-change function for every input. When supplied
    and it resolves to DHL (``_is_dhl_carrier``), an additional OR-branch also
    accepts DHL's bare 9-11-digit waybill shape via ``_dhl_looks_like_tracking``.
    That shape is deliberately absent from the shared ``_TRACKING_PATTERNS``
    list (Decision 1, 36-01-PLAN.md — it's too broad to trust without carrier
    context); carrier context is what makes accepting it safe here, and without
    carrier context it stays rejected. Finding 2 (36-quick-tpu-PLAN.md): this
    shared gate is the sole reason a Phase 36 DHL shipment could not previously
    be POSTed — every production POST path re-gates through this function.

    Returns:
        ``(clean_value, ok, reason)`` where:

        * ``clean_value`` — separator-stripped, uppercased form (always returned,
          even on failure, so callers can log the cleaned value per D-06).
        * ``ok`` — ``True`` if the clean form matches a carrier pattern (the
          shared carrier-agnostic list, OR — additively — the DHL shape when
          carrier context says DHL).
        * ``reason`` — ``"empty"`` when the input is blank/None,
          ``"no_carrier_match"`` when no pattern matches, or ``None`` on success.

    Reuses ``_looks_like_tracking()`` and ``_dhl_looks_like_tracking()``
    internally — no parallel reimplementation of pattern logic (D-01); no
    second DHL regex is introduced. The DHL branch is expressed as a single
    boolean OR, never a switch: it can only ever convert a rejection into an
    acceptance, so a valid UPS/USPS/FedEx/ShipBob number still passes even when
    ``carrier_name`` says DHL — a switch would instead regress a real UPS
    number carried on a shipment whose carrier field says DHL, which is
    explicitly forbidden. The separator strip uses a bounded char class
    ``re.sub(r"[ -]", "", ...)``; the reused DHL regex (``_dhl_looks_like_tracking``)
    already has bounded quantifiers — no new ReDoS surface is introduced
    (ASVS V5).
    """
    clean = re.sub(r"[ -]", "", value or "").upper()
    if not clean:
        return clean, False, "empty"
    ok = _looks_like_tracking(clean) or (
        _is_dhl_carrier(carrier_name) and _dhl_looks_like_tracking(clean)
    )
    if not ok:
        return clean, False, "no_carrier_match"
    return clean, True, None


def build_keyword_matcher(query: str) -> tuple[re.Pattern[str] | None, list[str]]:
    """Compile a stored gmail_query keyword string into a local narrowing filter.

    Follow-up to gmail-query-drops-emails (.planning/debug/gmail-query-drops-emails.md):
    Gmail's server-side search engine does not reliably return the full union of
    results for a long chain of bare ``OR``-joined keyword terms, so keyword
    narrowing was moved out of the Gmail List API call entirely. This function
    re-implements that narrowing locally, applied to each fetched message's
    subject + body right before the (comparatively expensive) EmailParser tiers
    run — a CPU-cost pre-filter, not a correctness gate.

    Deliberately coarse and over-inclusive: the failure this whole task follows
    up on was silent UNDER-inclusion (real shipment emails dropped with no
    trace), so every design choice below biases toward matching too much rather
    than too little.

    Algorithm:
    1. Tokenise on any run of whitespace after stripping the input.
    2. Discard the bare boolean joiner tokens "OR"/"AND" (case-sensitive,
       matching Gmail's own uppercase-only operator convention) — they are
       separators, not keywords. Gmail's space-as-AND semantics are
       deliberately approximated as OR here; over-matching is the safe
       direction for a pre-filter.
    3. Strip surrounding parentheses and double-quote characters from each
       remaining token.
    4. Route to ``dropped`` (never ``keywords``) any token that: contains a
       colon (a Gmail field operator such as ``from:`` or ``label:``), starts
       with a hyphen (Gmail negation), or contains no word character at all.
       A naive substring match on a field-operator token can never match plain
       email text, so silently keeping it would blackhole that user's stored
       customisation — the exact "silently drops everything" failure shape
       this task follows up on.
    5. Discard tokens that are empty after stripping — they belong in neither
       list.
    6. If no usable keyword remains, return ``(None, dropped)`` — the fail-open
       contract: callers must treat ``None`` as "match everything", never as
       "match nothing".
    7. Otherwise compile ONE case-insensitive alternation with word-boundary
       anchors on both sides, running ``re.escape`` over every keyword. A flat,
       unnested, non-quantified alternation over escaped literals is linear in
       input length regardless of the (attacker-controllable) email body it is
       run against — see T-i5r-01 in this task's threat model. No new ReDoS
       surface is introduced (ASVS V5).

    Returns:
        ``(pattern, dropped)`` where ``pattern`` is ``None`` when the query is
        blank/whitespace-only or contains only operator/negation tokens
        (fail-open — callers must treat this as "match everything"), and
        ``dropped`` lists every token that was excluded from the compiled
        pattern for operator/negation/empty reasons.
    """
    dropped: list[str] = []
    keywords: list[str] = []
    for raw_token in query.strip().split():
        if raw_token in ("OR", "AND"):
            continue
        token = raw_token.strip("()").strip('"')
        if not token:
            continue
        if ":" in token or token.startswith("-") or not re.search(r"\w", token):
            dropped.append(token)
            continue
        keywords.append(token)

    if not keywords:
        return None, dropped

    alternation = "|".join(re.escape(kw) for kw in keywords)
    pattern = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)
    return pattern, dropped


def matches_keyword_filter(pattern: re.Pattern[str] | None, *texts: str) -> bool:
    """Return True if any supplied text matches the compiled keyword pattern.

    ``pattern is None`` is the fail-open contract from build_keyword_matcher —
    always returns True regardless of ``texts`` (no keyword narrowing active).
    Otherwise returns True as soon as the pattern matches inside any non-empty
    text among ``texts`` (subject-only and body-only matches both pass);
    ``None``/empty strings among ``texts`` are tolerated without raising.
    """
    if pattern is None:
        return True
    return any(text and pattern.search(text) for text in texts)


def extract_sender_domain(sender: str) -> str:
    """Extract the lowercased domain from an email 'From' header value.

    Sibling of build_sender_exclusion_matcher below — together they form the
    EXCLUDE-biased half of sender filtering, deliberately the opposite safety
    direction of build_keyword_matcher/matches_keyword_filter above (which
    bias toward matching too much because their own failure mode was silent
    under-matching).

    Handles both a bare address ("no-reply@shopify.com") and a
    ``Display Name <addr@domain>`` header value — ``re.search`` finds the
    first ``@`` wherever it occurs. Returns "" for a sender with no "@" and
    for an empty/None input (the ``sender or ""`` guard), so callers can
    treat "" as "never excluded" — build_sender_exclusion_matcher never
    stores the empty string as a configured domain (blank entries are
    dropped), so "" can never accidentally match a configured exclusion.

    T-qw1-01 (ASVS V5): ``@([\\w.-]+)`` is a single bounded character class
    with one non-nested quantifier — linear in input length even against an
    attacker-controllable From header. No ReDoS surface added.
    """
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""


def build_sender_exclusion_matcher(excluded_domains: list[str]) -> Callable[[str], bool]:
    """Compile a stored sender-exclusion domain list into a matcher closure.

    EXCLUDE-biased — the deliberate opposite safety direction of
    build_keyword_matcher/matches_keyword_filter above. Those functions
    correctly bias toward matching too much (their failure mode was silent
    under-matching, dropping real shipment emails). This filter must bias
    the opposite way, toward excluding too little: an over-eager exclusion
    here silently and permanently drops real data with no visible error,
    which is strictly worse than the wasted Stage-1/Stage-2 pass it exists
    to avoid.

    Membership is an EXACT ``set`` lookup — never a suffix, substring, or
    ``endswith`` check. This is not a style preference: a suffix match on an
    entry like "usps.com" would also match
    "email.informeddelivery.usps.com" and silently, permanently drop USPS
    Informed Delivery digests, which send a legitimate daily email
    regardless of whether any package is actually arriving (D-01). Because
    membership is exact, a short parent-domain entry can never reach a
    longer subdomain.

    An empty or all-blank ``excluded_domains`` list means exclude nothing
    (D-03 fail-open) — the default, unconfigured behaviour, and the only
    behaviour byte-for-byte-identical polling depends on.
    """
    normalized = {d.strip().lower().lstrip("@") for d in excluded_domains if d.strip()}
    if not normalized:

        def _exclude_nothing(_sender: str) -> bool:
            return False

        return _exclude_nothing

    def _matcher(sender: str) -> bool:
        return extract_sender_domain(sender) in normalized

    return _matcher


def _infer_carrier(tracking: str) -> str:
    """Infer carrier from tracking number shape. Used by Tier 2 and href fallbacks."""
    if re.match(r"^1Z[A-Z0-9]{16}$", tracking):
        return "UPS"
    if re.match(r"^9[12345][0-9]{15,24}$", tracking):
        return "USPS"
    if re.match(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$", tracking):
        return "USPS"
    if re.match(r"^(?:[0-9]{12}|[0-9]{15}|[0-9]{20})$", tracking):
        return "FedEx"
    if re.match(r"^SB[A-Z0-9]{8,23}$", tracking):
        return "ShipBob"
    return "Unknown"


def _extract_tracking_from_hrefs(soup: BeautifulSoup) -> str | None:
    """Scan <a href> tags for tracking numbers in query params or path segments.

    Checks URL query parameters first (e.g. ?tracknum=1Z...), then path segments.
    Returns the first valid tracking number found, uppercased.
    """
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not isinstance(href, str):
            continue
        try:
            parsed = urlparse(href)
            for values in parse_qs(parsed.query).values():
                for value in values:
                    upper = value.upper()
                    if _looks_like_tracking(upper):
                        return upper
            for segment in parsed.path.strip("/").split("/"):
                upper = segment.upper()
                if _looks_like_tracking(upper):
                    return upper
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Failed to parse href %r: %s", href, exc)
            continue
    return None


# ---------------------------------------------------------------------------
# Carrier template registry (Phase 8, D-04 / D-05).
# Each carrier email format gets a (detect_fn, parse_fn) tuple. parse() iterates
# CARRIER_REGISTRY before falling through to the existing Shopify dual-strategy.
# Detection is HTML-fingerprint-based (no sender header parameter — D-04).
# ---------------------------------------------------------------------------


# WR-03: carrier-domain detection regexes. The former bare substring match
# ("ups.com" in html) also fired on unrelated domains that merely END in the
# same letters — groups.com, meetups.com, signups.com, pickups.com — all
# plausible in marketing/newsletter emails. A false detection both ran the
# carrier regexes over an unrelated email AND permanently suppressed the
# Tier-2 broad scan for it (parse() carrier_detected gate). Anchor the domain
# to a host/word boundary: preceded by start-of-string or a non-domain-label
# character (whitespace, quote, '/', '@', '.', '=', '(' or '>'), followed by
# a word boundary. Subdomains ("www.ups.com", "tools.usps.com") still match
# via the '.' in the boundary class. Bounded patterns — no ReDoS risk
# (ASVS V5). Body-based detection remains best-effort classification against
# accidental misrouting, NOT a trust decision (senders control body content).
_UPS_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])ups\.com\b")
_USPS_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])usps\.com\b")
_FEDEX_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])fedex\.com\b")
_DHL_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])dhl\.com\b")
# ShipBob (.planning/debug/shipbob-carrier-unsupported.md): anchored on the
# customer-facing tracking subdomain, not the bare apex domain — ShipBob is a
# 3PL fulfillment platform, its marketing/merchant-portal domain is unrelated
# to the tracking link merchants embed in shipment emails.
_SHIPBOB_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])track\.shipbob\.com\b")

# DHL Express waybill/AWB numbers are commonly 9-11 digits (10 in practice). NOT
# covered by the shared _TRACKING_PATTERNS list (UPS/USPS/FedEx only — see R5
# comment above) — Decision 1 (36-01-PLAN.md): local-only validation, never
# extend the shared list/MRG-04 validator with this shape. Bounded quantifiers
# only — no ReDoS risk (ASVS V5).
_DHL_TRACKING_RE = re.compile(r"waybill\s+number[^0-9]{0,10}(\d{9,11})", re.IGNORECASE)


def _dhl_looks_like_tracking(s: str) -> bool:
    """Local candidate validator for DHL's 9-11 digit waybill shape.

    Deliberately NOT reusing the shared _looks_like_tracking()/_TRACKING_PATTERNS
    list — DHL's bare-digit shape isn't (and per Decision 1 in 36-01-PLAN.md must
    never be) in that shared list, since it feeds merge.py's MRG-04 Stage-2
    promotion gate. A bare 9-11-digit shape there would let the LLM promote any
    unlabeled digit-string guess as a "valid" DHL tracking number. This local
    validator only ever runs against a match already gated by _DHL_TRACKING_RE's
    'waybill number' label anchor inside _parse_dhl, so it can never be reached
    from the Stage-2 path.
    """
    return bool(re.match(r"^\d{9,11}$", s))


def _is_dhl_carrier(carrier_name: str | None) -> bool:
    """True when carrier_name resolves to DHL via the single source of truth
    (api/carrier_codes.py::normalize_carrier — same package, no HA imports,
    and carrier_codes.py imports nothing from this module, so no import cycle).

    Guards the ``None`` case before calling ``normalize_carrier``, which calls
    ``.strip()`` internally and would raise on ``None``. Shared by all five
    carrier-aware gate call sites (Task 1-3, quick-260807-tpu) so DHL
    recognition has exactly one definition.
    """
    if carrier_name is None:
        return False
    return normalize_carrier(carrier_name) == "dhl"


def _detect_ups(html: str) -> bool:
    """Return True if html is a UPS shipping notification email.

    Marker: boundary-anchored 'ups.com' (WR-03) AND 'shopify' not in html —
    prevents misclassifying Shopify merchant emails for UPS-fulfilled orders
    (Pitfall 1 in RESEARCH.md). T-Spoof mitigation.

    The 'mcinfo@ups.com' sender check was removed because extract_html_body()
    returns only the MIME text/html part (never email headers), so the sender
    address is never present in the html argument passed here.
    """
    html_lower = html.lower()
    return bool(_UPS_DOMAIN_RE.search(html_lower)) and "shopify" not in html_lower


def _detect_usps(html: str) -> bool:
    """Return True if html is a USPS shipping notification email.

    Marker: boundary-anchored 'usps.com' (WR-03) AND 'shopify' not present —
    prevents misclassifying Shopify merchant emails for USPS-fulfilled orders
    (T-Spoof mitigation, matching _detect_ups pattern).
    """
    html_lower = html.lower()
    return bool(_USPS_DOMAIN_RE.search(html_lower)) and "shopify" not in html_lower


def _detect_fedex(html: str) -> bool:
    """Return True if html is a FedEx shipping notification email.

    Marker: boundary-anchored 'fedex.com' (WR-03) AND 'shopify' not present —
    prevents misclassifying Shopify merchant emails for FedEx-fulfilled orders
    (T-Spoof mitigation, matching _detect_ups pattern).
    """
    html_lower = html.lower()
    return bool(_FEDEX_DOMAIN_RE.search(html_lower)) and "shopify" not in html_lower


def _detect_dhl(html: str) -> bool:
    """Return True if html is a DHL Express shipping notification email.

    Marker: boundary-anchored 'dhl.com' (WR-03) AND 'shopify' not present —
    prevents misclassifying Shopify merchant emails for DHL-fulfilled orders
    (T-Spoof mitigation, matching _detect_ups pattern).
    """
    html_lower = html.lower()
    return bool(_DHL_DOMAIN_RE.search(html_lower)) and "shopify" not in html_lower


def _detect_shipbob(html: str) -> bool:
    """Return True if html contains a ShipBob 3PL tracking link.

    Marker: boundary-anchored 'track.shipbob.com' (WR-03) — deliberately
    WITHOUT the "shopify not in html" exclusion _detect_ups/_detect_usps/
    _detect_fedex/_detect_dhl apply. ShipBob is a fulfillment platform (3PL),
    not a last-mile carrier that sends its own direct emails — its tracking
    link legitimately co-occurs inside a merchant/Klaviyo/Shopify-templated
    email (.planning/debug/shipbob-carrier-unsupported.md), unlike UPS/USPS/
    FedEx/DHL's own direct carrier emails.
    """
    html_lower = html.lower()
    return bool(_SHIPBOB_DOMAIN_RE.search(html_lower))


def _parse_ups(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from UPS shipping notification email.

    UPS direct emails embed tracking in <td>/<a> — not <p>. Strategy: full
    get_text() + carrier-specific bounded regex + _looks_like_tracking() validator.
    Href fallback handles emails where TN appears only in link query params.
    order_name='' for direct carrier emails (no Shopify order number present —
    sensor entity, coordinator, and parcelapp 'description' all accept empty
    string per Phase 5 design).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _UPS_TRACKING_RE.search(text)
    if m and _looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="UPS",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_UPS,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    tn = _extract_tracking_from_hrefs(soup)
    if tn and _looks_like_tracking(tn):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=tn,
                carrier_name="UPS",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_UPS,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _extract_usps_shippers(html: str) -> list[str]:
    """Structurally extract per-package shipper names from a USPS Informed
    Delivery digest, in document order (spikes 022, 023 -- USPS-STRUCT-01).

    USPS's own digest template pairs a shipper-name span
    (id="pra-shipper-name-id", empty when no sender data upstream) with a
    tracking-number span (id="pra-tracking-number-id") in the same package
    block, one pair per physical package, in document order -- the same order
    _parse_usps's own tracking_numbers findall already returns. Mirrors that
    exact extraction (findall over get_text(), same dict.fromkeys dedup) so
    the two counts always agree when USPS's real ids are present.

    find_all(id="pra-shipper-name-id") returns exactly one span per physical
    package -- unlike pra-tracking-number-id, it has no "-secondary" mobile-
    view duplicate, so no dedup is needed here.

    Guarded on an exact count match: if len(shippers) != len(tracking_numbers)
    (real ids entirely absent, as in the synthetic pytest fixtures, or present
    but mismatched), returns [] rather than guessing by partial index -- callers
    must treat [] as "no structural pairing available" and leave order_summary
    unset (today's behavior).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    raw = _USPS_TRACKING_RE.findall(text)
    tracking_numbers = list(dict.fromkeys(m for m in raw if _looks_like_tracking(m)))

    shipper_spans = soup.find_all(id="pra-shipper-name-id")
    shippers = [s.get_text(strip=True) for s in shipper_spans]

    if len(shippers) != len(tracking_numbers):
        return []
    return shippers


def _parse_usps(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number(s) from a USPS email.

    Uses findall (not search) so that multi-package digest emails (e.g. USPS
    Informed Delivery daily digest) produce one ShipmentData per tracking
    number. The first valid match becomes ParseResult.shipment; any additional
    matches go into ParseResult.extra_shipments. dict.fromkeys deduplicates
    while preserving order (same TN appearing twice in the HTML counts once).
    Href fallback runs only when the body text contains no valid tracking numbers.

    When USPS's real pra-shipper-name-id/pra-tracking-number-id template ids
    are present and their counts match the tracking numbers found here
    (USPS-STRUCT-01, spikes 022/023), each sibling's order_summary is
    populated from its paired shipper (empty span -> None). Otherwise
    order_summary stays None, matching today's behavior.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    raw = _USPS_TRACKING_RE.findall(text)
    tracking_numbers = list(dict.fromkeys(m for m in raw if _looks_like_tracking(m)))
    if tracking_numbers:
        shippers = _extract_usps_shippers(html)
        shipments = [
            ShipmentData(
                tracking_number=tn,
                carrier_name="USPS",
                order_name="",
                order_summary=(shippers[idx] or None) if shippers else None,
                message_id=message_id,
                email_date=email_date,
            )
            for idx, tn in enumerate(tracking_numbers)
        ]
        return ParseResult(
            shipment=shipments[0],
            extra_shipments=shipments[1:],
            skip_reason=None,
            strategy_used=STRATEGY_USPS,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    tn = _extract_tracking_from_hrefs(soup)
    if tn and _looks_like_tracking(tn):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=tn,
                carrier_name="USPS",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_USPS,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _parse_fedex(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from FedEx shipping notification email.

    FedEx uses 12-20 digit tracking numbers (Express 12, Ground 15, SmartPost 20);
    the _FEDEX_TRACKING_RE and _TRACKING_PATTERNS FedEx entries were widened to
    the full 12-20 range in Task 1.
    Href fallback handles FedEx Delivery Manager emails where TN appears only
    in ?trknbr= query params, not as labeled text.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _FEDEX_TRACKING_RE.search(text)
    if m and _looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="FedEx",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_FEDEX,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    tn = _extract_tracking_from_hrefs(soup)
    if tn and _looks_like_tracking(tn):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=tn,
                carrier_name="FedEx",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_FEDEX,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _parse_dhl(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract waybill number from DHL Express shipping notification email.

    Mirrors _parse_ups's structure exactly (labeled regex, no href fallback —
    not needed for this fixture shape per spike 024). order_name='' for direct
    carrier emails (no Shopify order number present). Uses the local
    _dhl_looks_like_tracking() validator, NOT the shared _looks_like_tracking()
    (Decision 1, 36-01-PLAN.md).

    quick-260807-tpu: this DHL waybill can only reach parcelapp because the four
    production pre-POST re-gates (coordinator.py's worker + drain, gmail_coordinator.py's
    and imap_coordinator.py's inline POST gates) now pass carrier context into
    validate_carrier_format(). A future change that drops the carrier_name argument
    at any of those call sites silently re-breaks DHL end to end — this comment is
    the back-reference that makes that coupling discoverable from the parser side.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _DHL_TRACKING_RE.search(text)
    if m and _dhl_looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="DHL Express",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_DHL,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _parse_shipbob(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from a ShipBob 3PL shipping notification email.

    Mirrors _parse_ups/_parse_dhl's structure exactly (text regex first, href
    fallback second — the href fallback matters here since ShipBob's real
    primary tracking button is often an obfuscated merchant click-tracking
    redirect URL; only the MSO-fallback anchor or body prose expose the raw
    tracking token — see shipbob-carrier-unsupported.md Evidence).

    carrier_name is HARDCODED to "ShipBob" rather than derived from body
    prose ("via X" extraction) — ShipBob's true underlying last-mile carrier
    (e.g. OnTrac) is only ever exposed via authenticated ShipBob merchant API
    access, never recoverable from the customer-facing email alone
    (confirmed via developer.shipbob.com/guides/tracking). This deliberately
    avoids re-introducing the carrier_name="Unknown" -> normalize_carrier()
    -> "pholder" failure shape already resolved once in this codebase (see
    .planning/debug/resolved/parcelapp-unknown-carrier-code.md).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _SHIPBOB_TRACKING_RE.search(text)
    if m and _looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="ShipBob",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_SHIPBOB,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    tn = _extract_tracking_from_hrefs(soup)
    if tn and _looks_like_tracking(tn):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=tn,
                carrier_name="ShipBob",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_SHIPBOB,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


# Registry order: UPS -> USPS -> FedEx -> DHL -> ShipBob -> (fallthrough to
# Shopify in parse()). First match wins. Order matters per RESEARCH.md
# ordering analysis. ShipBob is appended last, after the four direct-carrier
# detectors — its detect_fn deliberately has no "shopify not in html"
# exclusion (see _detect_shipbob), so it must never shadow a more specific
# direct-carrier match earlier in the list.
_CarrierEntry = tuple[
    Callable[[str], bool],
    Callable[[str, str, int], ParseResult],
]
CARRIER_REGISTRY: list[_CarrierEntry] = [
    (_detect_ups, _parse_ups),
    (_detect_usps, _parse_usps),
    (_detect_fedex, _parse_fedex),
    (_detect_dhl, _parse_dhl),
    (_detect_shipbob, _parse_shipbob),
]


class EmailParser:
    """Parse Shopify shipping confirmation emails using tiered strategy approach.

    EMAIL-03: HTML template strategy first, Tier 1 regex second, Tier 2 broad scan third.
    """

    def __init__(self, enable_broad_scan: bool = False) -> None:
        """Initialize parser with optional Tier 2 broad-scan gate (PR4-C2).

        Tier 2 sweeps all alphanumeric tracking-shaped tokens with no keyword
        anchor, so it produces false positives. Default OFF — opt-in via
        config entry option CONF_ENABLE_BROAD_SCAN.
        """
        self._enable_broad_scan = enable_broad_scan

    def parse(self, html: str, message_id: str, email_date: int) -> ParseResult:
        """Parse email HTML. Returns ParseResult always — never None.

        Carrier template registry (D-05) is consulted first: each (detect_fn,
        parse_fn) tuple in CARRIER_REGISTRY is evaluated in order, first match
        wins. If no registry entry matches, falls through to tiered Shopify
        strategies: HTML template -> Tier 1 regex -> Tier 2 broad scan.
        shipment is None when all strategies fail; skip_reason indicates which
        stage failed (D-02).
        """
        carrier_detected = False
        for detect_fn, parse_fn in CARRIER_REGISTRY:
            if detect_fn(html):
                _LOGGER.debug(
                    "Carrier template detected (%s) for message %s",
                    detect_fn.__name__,
                    message_id,
                )
                carrier_result = parse_fn(html, message_id, email_date)
                if carrier_result.shipment is not None:
                    _LOGGER.debug(
                        "Carrier template matched — TN=%s strategy=%s message=%s",
                        carrier_result.shipment.tracking_number,
                        carrier_result.strategy_used,
                        message_id,
                    )
                    return carrier_result
                carrier_detected = True
                break  # detected but extraction failed — fall through to Shopify HTML + Tier 1 only
        html_result = self._parse_html_template(html, message_id, email_date)
        if html_result.shipment is not None:
            return html_result
        tier1_result = self._parse_regex_tier1(html, message_id, email_date)
        if tier1_result.shipment is not None:
            return tier1_result
        # PR4-I2: when a carrier was detected, do NOT fall through to Tier 2
        # broad scan — the email is carrier-specific and broad scan would pick
        # up order numbers, phone numbers, etc.
        # PR4-C2: Tier 2 broad scan is opt-in (default OFF).
        if carrier_detected or not self._enable_broad_scan:
            _LOGGER.debug(
                "No match after all templates and regex fallback for message %s", message_id
            )
            return ParseResult(
                shipment=None,
                skip_reason="no_tracking_pattern",
                strategy_used=None,
                keyword_hits={
                    "tracking_regex": False,
                    "order_regex": False,
                    "carrier_regex": False,
                },
            )
        return self._parse_regex_tier2(html, message_id, email_date)

    def _parse_html_template(self, html: str, message_id: str, email_date: int) -> ParseResult:
        """Strategy 1: BeautifulSoup on <p> and <td> text patterns + href fallback.

        Shopify standard template embeds tracking info as prose in <p> elements;
        many merchant templates use <td>. No CSS classes or IDs on tracking data —
        parse by text pattern only. Order is optional (alphanumeric IDs supported).
        Href fallback catches tracking numbers embedded in anchor URLs.
        """
        soup = BeautifulSoup(html, "lxml")
        tracking_number = carrier_name = order_name = None

        # SC-3: order_name/carrier_name label regexes stay LABEL_TAGS-scoped
        # ([p, td]) — UNCHANGED scope. Broadening this pass to div/span would
        # pick up footer/support boilerplate like "Reference Order #99999 when
        # you contact support" as the real order number (confirmed false-positive
        # risk — see module docstring above LABEL_TAGS/TRACKING_TAGS).
        for elem in soup.find_all(LABEL_TAGS):
            text = elem.get_text(separator=" ", strip=True)
            if not order_name:
                # IN-02: require an 'order' anchor + '#'/':' separator (mirrors the
                # Tier-1 PR4-C1 tightening). The old bare '#token' pattern grabbed
                # the first '#'-prefixed token in ANY <p>/<td> — '#1A rated!',
                # social '#ShipDay' tags — and that junk value became order_name
                # and could reach the POSTed parcelapp description. \b blocks
                # 'ordered ...' matches; the optional second '#' accepts both
                # 'Order #1234' and 'Order: #1234'.
                m = re.search(r"order\b\s*[#:]\s*#?([A-Z0-9][\w\-]{1,30})", text, re.IGNORECASE)
                if m:
                    order_name = f"#{m.group(1).upper()}"
            if not carrier_name:
                # PR4-I4: non-greedy quantifier + IGNORECASE to match Tier 1 behavior.
                m = re.search(
                    r"\bvia\s+([A-Za-z][A-Za-z ]{1,29}?)(?:\s+(?:with|on|by|for|to)\b|\s*$|\.)",
                    text,
                    re.IGNORECASE,
                )
                if m:
                    carrier_name = m.group(1).strip()

        # SC-3: tracking-number shape-sniffing pass is BROADENED to TRACKING_TAGS
        # ([p, td, div, span]) — recovers custom-Shopify-theme emails that embed
        # shipment data in <div>/<span> instead of <p>/<td>. No new regex is
        # introduced; the already-bounded _looks_like_tracking() check just runs
        # over more elements (ASVS V5 — no ReDoS risk change).
        for elem in soup.find_all(TRACKING_TAGS):
            if tracking_number:
                break
            text = elem.get_text(separator=" ", strip=True)
            for candidate in re.findall(r"\b([A-Za-z0-9]{10,40})\b", text, re.IGNORECASE):
                if _looks_like_tracking(candidate.upper()):
                    tracking_number = candidate.upper()
                    break

        if not tracking_number:
            tracking_number = _extract_tracking_from_hrefs(soup)

        if tracking_number:
            return ParseResult(
                shipment=ShipmentData(
                    tracking_number=tracking_number,
                    carrier_name=carrier_name or "Unknown",
                    order_name=order_name or "",
                    message_id=message_id,
                    email_date=email_date,
                ),
                skip_reason=None,
                strategy_used=STRATEGY_HTML,
                keyword_hits={
                    "tracking_regex": False,
                    "order_regex": False,
                    "carrier_regex": False,
                },
            )
        return ParseResult(
            shipment=None,
            skip_reason="no_template_match",
            strategy_used=None,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )

    def _parse_regex_tier1(self, html: str, message_id: str, email_date: int) -> ParseResult:
        """Strategy 2: labeled keyword regex on full plain text + href fallback.

        Handles custom merchant templates and non-Shopify shipping emails.
        Requires a 'Tracking number:' label anchor for the tracking field.
        Order is optional; alphanumeric order IDs (e.g. #AB-1234) are supported.
        All quantifiers are bounded (max 40 chars) — no ReDoS risk.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator=" ")
        tracking = re.search(
            r"(?:tracking\s+(?:number|#|no\.?)\s*:?\s*)([A-Z0-9]{10,40})\b",
            text,
            re.IGNORECASE,
        )
        # PR4-C1: '#' or ':' is now required after 'order' to prevent false positives
        # like "Your order has shipped" -> order_name="#HAS".
        order = re.search(r"order\s*[#:]\s*([A-Z0-9][\w\-]{1,30})", text, re.IGNORECASE)
        # PR4-I4: non-greedy inner quantifier prevents "shipped via UPS with care"
        # from yielding carrier_name="UPS with care".
        carrier = re.search(
            r"(?:via|carrier)\s+(?:by\s+)?([A-Za-z][A-Za-z ]{2,29}?)(?:\s+(?:with|on|by|for|to)\b|\s*$|\.)"
            r"|shipped\s+by\s+([A-Za-z][A-Za-z ]{2,29}?)(?:\s+(?:with|on|by|for|to)\b|\s*$|\.)",
            text,
            re.IGNORECASE,
        )
        hits = {
            "tracking_regex": tracking is not None,
            "order_regex": order is not None,
            "carrier_regex": carrier is not None,
        }

        def _href_fallback_result() -> ParseResult | None:
            """Try the href fallback; None when no valid TN sits in a link.

            IN-01: shared by the no-label AND labelled-but-invalid branches — the
            latter previously returned tracking_invalid without ever consulting the
            hrefs, so an email whose 'Tracking number:' label captured a regional
            carrier code missed a valid carrier TN sitting in its tracking link.
            """
            href_tracking = _extract_tracking_from_hrefs(soup)
            if not (href_tracking and _looks_like_tracking(href_tracking)):
                return None
            _LOGGER.debug(
                "Tier 1 href fallback matched TN=%s for message %s",
                href_tracking,
                message_id,
            )
            return ParseResult(
                shipment=ShipmentData(
                    tracking_number=href_tracking,
                    carrier_name=(
                        next(
                            (g for g in (carrier.group(1), carrier.group(2)) if g),
                            "Unknown",
                        ).strip()
                        if carrier
                        else _infer_carrier(href_tracking)
                    ),
                    order_name=f"#{order.group(1).upper()}" if order else "",
                    message_id=message_id,
                    email_date=email_date,
                ),
                skip_reason=None,
                strategy_used=STRATEGY_REGEX,
                keyword_hits=hits,
            )

        if not tracking:
            href_result = _href_fallback_result()
            if href_result is not None:
                return href_result
            return ParseResult(
                shipment=None,
                skip_reason="no_tracking_label",
                strategy_used=None,
                keyword_hits=hits,
            )

        raw_tracking = tracking.group(1).upper()
        if not _looks_like_tracking(raw_tracking):
            # IN-01: consistent recall with the no-label branch above — consult the
            # href fallback before declaring the labelled token invalid.
            href_result = _href_fallback_result()
            if href_result is not None:
                return href_result
            return ParseResult(
                shipment=None,
                skip_reason="tracking_invalid",
                strategy_used=None,
                keyword_hits=hits,
            )

        return ParseResult(
            shipment=ShipmentData(
                tracking_number=raw_tracking,
                carrier_name=(
                    next(
                        (g for g in (carrier.group(1), carrier.group(2)) if g),
                        "Unknown",
                    ).strip()
                    if carrier
                    else "Unknown"
                ),
                order_name=f"#{order.group(1).upper()}" if order else "",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_REGEX,
            keyword_hits=hits,
        )

    def _parse_regex_tier2(self, html: str, message_id: str, email_date: int) -> ParseResult:
        """Strategy 3: broad token sweep — no keyword gate, maximum recall.

        Collects ALL tracking-shaped tokens from full text and href URLs.
        Returns the best (longest) match with carrier inferred from shape.
        Populates candidate_tokens with every token found for diagnostic use.
        False positives are expected here and filtered in a later phase.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator=" ")

        candidates: list[str] = []
        for token in re.findall(r"\b([A-Za-z0-9]{10,40})\b", text):
            upper = token.upper()
            if _looks_like_tracking(upper) and upper not in candidates:
                candidates.append(upper)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not isinstance(href, str):
                continue
            try:
                parsed = urlparse(href)
                for values in parse_qs(parsed.query).values():
                    for value in values:
                        upper = value.upper()
                        if _looks_like_tracking(upper) and upper not in candidates:
                            candidates.append(upper)
                for segment in parsed.path.strip("/").split("/"):
                    upper = segment.upper()
                    if _looks_like_tracking(upper) and upper not in candidates:
                        candidates.append(upper)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Failed to parse Tier 2 href %r: %s", href, exc)
                continue

        if not candidates:
            return ParseResult(
                shipment=None,
                skip_reason="no_tracking_pattern",
                strategy_used=None,
                keyword_hits={
                    "tracking_regex": False,
                    "order_regex": False,
                    "carrier_regex": False,
                },
                candidate_tokens=[],
            )

        best = max(candidates, key=len)
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=best,
                carrier_name=_infer_carrier(best),
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_BROAD_REGEX,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
            candidate_tokens=candidates,
        )
