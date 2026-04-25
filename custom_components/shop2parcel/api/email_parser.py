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


@dataclass(slots=True)
class ShipmentData:
    """Structured output from EmailParser. Coordinator data type for Phase 4.

    carrier_name: raw Shopify string (e.g. "UPS", "Canada Post").
    Caller passes carrier_name to carrier_codes.normalize_carrier() before POSTing.
    message_id: Gmail message ID — used as stable unique entity ID in Phase 5.
    email_date: Unix timestamp (seconds) from Gmail internalDate.
    """

    tracking_number: str
    carrier_name: str
    order_name: str     # e.g. "#1234"
    message_id: str     # Gmail message ID for deduplication
    email_date: int     # Unix timestamp (seconds)


# Known tracking number format patterns (EMAIL-04).
# Patterns are bounded quantifiers — no ReDoS risk (ASVS V5).
_TRACKING_PATTERNS = [
    re.compile(r"^1Z[A-Z0-9]{16}$"),           # UPS: 1Z999AA10123456784
    re.compile(r"^[0-9]{20,22}$"),             # USPS domestic
    re.compile(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$"), # USPS international
    re.compile(r"^[0-9]{12,15}$"),             # FedEx
    re.compile(r"^[0-9]{10,11}$"),             # DHL (assumed)
]


def _looks_like_tracking(s: str) -> bool:
    """Return True if s matches any known carrier tracking number format."""
    return any(p.match(s) for p in _TRACKING_PATTERNS)


class EmailParser:
    """Parse Shopify shipping confirmation emails using dual-strategy approach.

    EMAIL-03: HTML template strategy first, regex fallback second.
    """

    def parse(
        self, html: str, message_id: str, email_date: int
    ) -> ShipmentData | None:
        """Parse email HTML. Returns ShipmentData or None if unparseable."""
        result = self._parse_html_template(html, message_id, email_date)
        if result:
            return result
        return self._parse_regex_fallback(html, message_id, email_date)

    def _parse_html_template(
        self, html: str, message_id: str, email_date: int
    ) -> ShipmentData | None:
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
                m = re.search(
                    r"\bvia\s+([A-Za-z][A-Za-z ]+?)(?:\s+with|\s*$|\.)", text
                )
                if m:
                    carrier_name = m.group(1).strip()
            if not tracking_number:
                m = re.search(r"\b([A-Z0-9]{10,40})\b", text)
                if m and _looks_like_tracking(m.group(1)):
                    tracking_number = m.group(1)

        if tracking_number and order_name:
            return ShipmentData(
                tracking_number=tracking_number,
                carrier_name=carrier_name or "Unknown",
                order_name=order_name,
                message_id=message_id,
                email_date=email_date,
            )
        return None

    def _parse_regex_fallback(
        self, html: str, message_id: str, email_date: int
    ) -> ShipmentData | None:
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
            r"(?:shipped?|carrier|via)\s+(?:by\s+)?([A-Za-z][A-Za-z ]{2,20}?)(?:\s|$|\.)",
            text,
            re.IGNORECASE,
        )
        if tracking and order:
            return ShipmentData(
                tracking_number=tracking.group(1),
                carrier_name=carrier.group(1).strip() if carrier else "Unknown",
                order_name=f"#{order.group(1)}",
                message_id=message_id,
                email_date=email_date,
            )
        return None
