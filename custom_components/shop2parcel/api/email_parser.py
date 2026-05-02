"""Dual-strategy email parser for Shopify shipping confirmation emails.

Strategy 1 (primary): BeautifulSoup + lxml HTML parsing.
  Parses <p> elements by text pattern — Shopify does NOT use CSS classes on tracking data.
  Pitfall: soup.find(class_="tracking-number") always returns None for Shopify emails.

Strategy 2 (fallback): stdlib re on plain text.
  Used when HTML strategy fails (custom merchant templates, non-Shopify shippers).
  EMAIL-03 locks this dual-strategy requirement.

No HA imports (D-01/D-03).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass(slots=True, frozen=True)
class ShipmentData:
    """Structured output from EmailParser. Coordinator data type for Phase 4.

    carrier_name: raw Shopify string (e.g. "UPS", "Canada Post").
    Caller passes carrier_name to carrier_codes.normalize_carrier() before POSTing.
    message_id: Gmail message ID — used as stable unique entity ID in Phase 5.
    email_date: Unix timestamp (seconds) from Gmail internalDate.
    """

    tracking_number: str
    carrier_name: str
    order_name: str  # e.g. "#1234"
    message_id: str  # Gmail message ID for deduplication
    email_date: int  # Unix timestamp (seconds)


@dataclass(slots=True, frozen=True)
class ParseResult:
    """Phase 7 (DIAG-01): instrumented return type for EmailParser.parse().

    Always fully populated — `keyword_hits` always has exactly the keys
    "tracking_regex", "order_regex", "carrier_regex" with bool values, even
    on HTML-strategy parses (all False in that case — D-07). This guarantees
    the coordinator can iterate the dict without key guards.
    """

    shipment: ShipmentData | None
    skip_reason: str | None          # "no_template_match" | "no_regex_match" | None
    strategy_used: str | None        # "html_template" | "regex_fallback" | None
    keyword_hits: dict[str, bool]    # keys always: tracking_regex, order_regex, carrier_regex


# Known tracking number format patterns (EMAIL-04).
# Patterns are bounded quantifiers — no ReDoS risk (ASVS V5).
_TRACKING_PATTERNS = [
    re.compile(r"^1Z[A-Z0-9]{16}$"),          # UPS: 1Z999AA10123456784
    re.compile(r"^9[2345][0-9]{15,26}$"),      # USPS domestic (Phase 8: 9+[2345] anchor, up to 28 digits total)
    re.compile(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$"), # USPS international
    re.compile(r"^[0-9]{12,20}$"),             # FedEx (Phase 8: extended for SmartPost up to 20 digits)
    re.compile(r"^[0-9]{10,11}$"),             # DHL (assumed)
]


# Strategy constants (D-07) — module-level string constants for ParseResult.strategy_used.
# Tests import these to avoid bare string comparisons. Values are stable contract.
STRATEGY_HTML = "html_template"
STRATEGY_UPS = "ups_template"
STRATEGY_USPS = "usps_template"
STRATEGY_FEDEX = "fedex_template"
STRATEGY_REGEX = "regex_fallback"


# Carrier-specific extraction regex — compiled at import, bounded quantifiers (ASVS V5).
# Used by carrier template parse_fn before _looks_like_tracking() validation.
# T-ReDoS mitigation: every quantifier is bounded; no `+` or `*` on character classes.
_UPS_TRACKING_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")
_USPS_TRACKING_RE = re.compile(r"\b(9[2345][0-9]{15,26})\b")
_FEDEX_TRACKING_RE = re.compile(r"\b([0-9]{12,20})\b")


def _looks_like_tracking(s: str) -> bool:
    """Return True if s matches any known carrier tracking number format."""
    return any(p.match(s) for p in _TRACKING_PATTERNS)


class EmailParser:
    """Parse Shopify shipping confirmation emails using dual-strategy approach.

    EMAIL-03: HTML template strategy first, regex fallback second.
    """

    def parse(self, html: str, message_id: str, email_date: int) -> ParseResult:
        """Parse email HTML. Returns ParseResult always — never None.

        shipment is None when both strategies fail; skip_reason indicates which
        stage failed (D-02).
        """
        html_result = self._parse_html_template(html, message_id, email_date)
        if html_result.shipment is not None:
            return html_result
        # HTML strategy failed; try regex fallback. The fallback's keyword_hits
        # supersedes the HTML strategy's all-False placeholder.
        return self._parse_regex_fallback(html, message_id, email_date)

    def _parse_html_template(
        self, html: str, message_id: str, email_date: int
    ) -> ParseResult:
        """Strategy 1: BeautifulSoup on <p> text patterns.

        Shopify standard template embeds tracking info as prose in <p> elements.
        No CSS classes or IDs on tracking data — parse by text pattern only.
        """
        soup = BeautifulSoup(html, "lxml")
        tracking_number = carrier_name = order_name = None

        for p in soup.find_all("p"):
            text = p.get_text(separator=" ", strip=True)
            if not order_name:
                m = re.search(r"#(\d+)", text)
                if m:
                    order_name = f"#{m.group(1)}"
            if not carrier_name:
                m = re.search(r"\bvia\s+([A-Za-z][A-Za-z ]+?)(?:\s+with|\s*$|\.)", text)
                if m:
                    carrier_name = m.group(1).strip()
            if not tracking_number:
                for candidate in re.findall(r"\b([A-Z0-9]{10,40})\b", text):
                    if _looks_like_tracking(candidate):
                        tracking_number = candidate
                        break

        if tracking_number and order_name:
            return ParseResult(
                shipment=ShipmentData(
                    tracking_number=tracking_number,
                    carrier_name=carrier_name or "Unknown",
                    order_name=order_name,
                    message_id=message_id,
                    email_date=email_date,
                ),
                skip_reason=None,
                strategy_used=STRATEGY_HTML,
                keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
            )
        return ParseResult(
            shipment=None,
            skip_reason="no_template_match",
            strategy_used=None,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )

    def _parse_regex_fallback(
        self, html: str, message_id: str, email_date: int
    ) -> ParseResult:
        """Strategy 2: strip HTML, apply keyword regex to plain text.

        Handles custom merchant templates and non-Shopify shipping emails.
        All quantifiers are bounded (max 40 chars) — no ReDoS risk.
        """
        text = BeautifulSoup(html, "lxml").get_text(separator=" ")
        tracking = re.search(
            r"(?:tracking\s+(?:number|#|no\.?)\s*:?\s*)([A-Z0-9]{10,40})\b",
            text,
            re.IGNORECASE,
        )
        order = re.search(r"order\s*#?\s*(\d+)", text, re.IGNORECASE)
        carrier = re.search(
            r"(?:shipped?|carrier|via)\s+(?:by\s+)?([A-Za-z][A-Za-z ]{2,20})(?:\s|$|\.)",
            text,
            re.IGNORECASE,
        )
        hits = {
            "tracking_regex": tracking is not None,
            "order_regex": order is not None,
            "carrier_regex": carrier is not None,
        }
        if tracking and order:
            return ParseResult(
                shipment=ShipmentData(
                    tracking_number=tracking.group(1),
                    carrier_name=carrier.group(1).strip() if carrier else "Unknown",
                    order_name=f"#{order.group(1)}",
                    message_id=message_id,
                    email_date=email_date,
                ),
                skip_reason=None,
                strategy_used=STRATEGY_REGEX,
                keyword_hits=hits,
            )
        return ParseResult(
            shipment=None,
            skip_reason="no_regex_match",
            strategy_used=None,
            keyword_hits=hits,
        )
