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
    skip_reason: str | None  # "no_template_match" | "no_tracking_label" | "tracking_invalid" | "no_tracking_pattern" | None
    strategy_used: str | None  # "html_template" | "regex_fallback" | "broad_regex" | None
    keyword_hits: dict[str, bool]  # keys always: tracking_regex, order_regex, carrier_regex
    candidate_tokens: list[str] = field(default_factory=list)


# Known tracking number format patterns (EMAIL-04).
# Patterns are bounded quantifiers — no ReDoS risk (ASVS V5).
_TRACKING_PATTERNS = [
    re.compile(r"^1Z[A-Z0-9]{16}$"),  # UPS: 1Z999AA10123456784
    re.compile(r"^9[12345][0-9]{15,24}$"),  # USPS domestic: IMpb 91-95 (91=Priority Mail Express)
    re.compile(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$"),  # USPS international
    re.compile(r"^[0-9]{12,20}$"),  # FedEx (Phase 8: extended for SmartPost up to 20 digits)
    re.compile(r"^[0-9]{10,11}$"),  # DHL (assumed)
]


# Strategy constants (D-07) — module-level string constants for ParseResult.strategy_used.
# Tests import these to avoid bare string comparisons. Values are stable contract.
STRATEGY_HTML = "html_template"
STRATEGY_UPS = "ups_template"
STRATEGY_USPS = "usps_template"
STRATEGY_FEDEX = "fedex_template"
STRATEGY_REGEX = "regex_fallback"
STRATEGY_BROAD_REGEX = "broad_regex"


# Carrier-specific extraction regex — compiled at import, bounded quantifiers (ASVS V5).
# Used by carrier template parse_fn before _looks_like_tracking() validation.
# T-ReDoS mitigation: every quantifier is bounded; no `+` or `*` on character classes.
_UPS_TRACKING_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")
_USPS_TRACKING_RE = re.compile(r"\b(9[12345][0-9]{15,24})\b")
_FEDEX_TRACKING_RE = re.compile(
    r"(?:tracking\s+(?:number|#|no\.?)\s*:?\s*)([0-9]{12,20})\b",
    re.IGNORECASE,
)


def _looks_like_tracking(s: str) -> bool:
    """Return True if s matches any known carrier tracking number format."""
    return any(p.match(s) for p in _TRACKING_PATTERNS)


def _infer_carrier(tracking: str) -> str:
    """Infer carrier from tracking number shape. Used by Tier 2 and href fallbacks."""
    if re.match(r"^1Z[A-Z0-9]{16}$", tracking):
        return "UPS"
    if re.match(r"^9[12345][0-9]{15,24}$", tracking):
        return "USPS"
    if re.match(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$", tracking):
        return "USPS"
    if re.match(r"^[0-9]{12,20}$", tracking):
        return "FedEx"
    return "Unknown"


def _extract_tracking_from_hrefs(soup: BeautifulSoup) -> str | None:
    """Scan <a href> tags for tracking numbers in query params or path segments.

    Checks URL query parameters first (e.g. ?tracknum=1Z...), then path segments.
    Returns the first valid tracking number found, uppercased.
    """
    for a in soup.find_all("a", href=True):
        href = a["href"]
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


def _detect_ups(html: str) -> bool:
    """Return True if html is a UPS shipping notification email.

    Marker: 'ups.com' in html AND 'shopify' not in html — prevents
    misclassifying Shopify merchant emails for UPS-fulfilled orders (Pitfall 1
    in RESEARCH.md). T-Spoof mitigation.

    The 'mcinfo@ups.com' sender check was removed because extract_html_body()
    returns only the MIME text/html part (never email headers), so the sender
    address is never present in the html argument passed here.
    """
    html_lower = html.lower()
    return "ups.com" in html_lower and "shopify" not in html_lower


def _detect_usps(html: str) -> bool:
    """Return True if html is a USPS shipping notification email.

    Marker: 'usps.com' AND 'shopify' not present — prevents misclassifying
    Shopify merchant emails for USPS-fulfilled orders (T-Spoof mitigation,
    matching _detect_ups pattern).
    """
    html_lower = html.lower()
    return "usps.com" in html_lower and "shopify" not in html_lower


def _detect_fedex(html: str) -> bool:
    """Return True if html is a FedEx shipping notification email.

    Marker: 'fedex.com' AND 'shopify' not present — prevents misclassifying
    Shopify merchant emails for FedEx-fulfilled orders (T-Spoof mitigation,
    matching _detect_ups pattern).
    """
    html_lower = html.lower()
    return "fedex.com" in html_lower and "shopify" not in html_lower


def _parse_ups(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from UPS shipping notification email.

    UPS direct emails embed tracking in <td>/<a> — not <p>. Strategy: full
    get_text() + carrier-specific bounded regex + _looks_like_tracking() validator.
    order_name='' for direct carrier emails (no Shopify order number present —
    sensor entity, coordinator, and parcelapp 'description' all accept empty
    string per Phase 5 design).
    """
    text = BeautifulSoup(html, "lxml").get_text(separator=" ")
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
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


def _parse_usps(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from USPS shipping notification email.

    USPS uses 17-26 digit tracking numbers starting with 9[12345] (IMpb format);
    the _USPS_TRACKING_RE pattern is the carrier-specific extractor and
    _TRACKING_PATTERNS USPS entry was widened in Task 1 so _looks_like_tracking
    accepts the full 26-digit form.
    """
    text = BeautifulSoup(html, "lxml").get_text(separator=" ")
    m = _USPS_TRACKING_RE.search(text)
    if m and _looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
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
    """
    text = BeautifulSoup(html, "lxml").get_text(separator=" ")
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
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )


# Registry order: UPS -> USPS -> FedEx -> (fallthrough to Shopify in parse()).
# First match wins. Order matters per RESEARCH.md ordering analysis.
_CarrierEntry = tuple[
    Callable[[str], bool],
    Callable[[str, str, int], ParseResult],
]
CARRIER_REGISTRY: list[_CarrierEntry] = [
    (_detect_ups, _parse_ups),
    (_detect_usps, _parse_usps),
    (_detect_fedex, _parse_fedex),
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
                carrier_result = parse_fn(html, message_id, email_date)
                if carrier_result.shipment is not None:
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
            return ParseResult(
                shipment=None,
                skip_reason="no_tracking_pattern",
                strategy_used=None,
                keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
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

        for elem in soup.find_all(["p", "td"]):
            text = elem.get_text(separator=" ", strip=True)
            if not order_name:
                m = re.search(r"#([A-Z0-9][\w\-]{1,30})", text, re.IGNORECASE)
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
            if not tracking_number:
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

        if not tracking:
            href_tracking = _extract_tracking_from_hrefs(soup)
            if href_tracking:
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
            return ParseResult(
                shipment=None,
                skip_reason="no_tracking_label",
                strategy_used=None,
                keyword_hits=hits,
            )

        raw_tracking = tracking.group(1).upper()
        if not _looks_like_tracking(raw_tracking):
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
                keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
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
