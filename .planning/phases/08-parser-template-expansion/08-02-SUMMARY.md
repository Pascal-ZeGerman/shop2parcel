---
phase: 08-parser-template-expansion
plan: "02"
subsystem: api
tags: [email-parser, carrier-templates, carrier-registry, tdd-green, ups, usps, fedex]
dependency_graph:
  requires:
    - phase: 08-01
      provides: carrier-html-fixtures + RED test stubs for 10 new carrier-template tests
  provides:
    - STRATEGY_HTML/UPS/USPS/FEDEX/REGEX constants in email_parser.py
    - CARRIER_REGISTRY with 3 (detect_fn, parse_fn) tuples
    - _detect_ups/_detect_usps/_detect_fedex HTML-fingerprint detection functions
    - _parse_ups/_parse_usps/_parse_fedex carrier-specific extraction functions
    - EmailParser.parse() registry-first dispatch
  affects:
    - coordinator.py (consumes ParseResult.strategy_used diagnostic field)
    - 08-CONTEXT.md, 08-RESEARCH.md (carrier registry pattern documented)
tech_stack:
  added: []
  patterns:
    - "Module-level CARRIER_REGISTRY list of (detect_fn, parse_fn) tuples — first-match-wins dispatch"
    - "HTML fingerprint detection (carrier domain string in raw HTML, no sender header)"
    - "Carrier-specific bounded-quantifier regex compiled at import time (ASVS V5 T-ReDoS mitigation)"
    - "T-Spoof mitigation: UPS detector uses mcinfo@ups.com primary + ups.com+not-shopify guard"
key_files:
  created: []
  modified:
    - custom_components/shop2parcel/api/email_parser.py
decisions:
  - "CARRIER_REGISTRY pattern (D-05): module-level list of (detect_fn, parse_fn) tuples, parse() iterates first-match-wins before falling through to existing Shopify path"
  - "Detection is HTML-fingerprint-based (D-04): no from_address parameter added to parse() signature"
  - "UPS detection uses two-tier logic: mcinfo@ups.com primary marker (transactional sender), ups.com+not-shopify fallback to prevent misclassifying Shopify merchant UPS emails (T-Spoof)"
  - "order_name='' for direct carrier emails: no Shopify order number present in UPS/USPS/FedEx notifications; Phase 5 sensor+coordinator accept empty string"
  - "STRATEGY_* constants replace bare string literals in _parse_html_template and _parse_regex_fallback (D-07)"
  - "Registry order UPS->USPS->FedEx: UPS is highest-volume US carrier (first reduces average iteration), FedEx last (lowest false-positive risk from 12-20 digit regex)"
metrics:
  duration: "~12 minutes"
  completed: "2026-05-02"
  tasks: 2
  files: 1
requirements: [PARSE-01, PARSE-02, PARSE-03, PARSE-04, PARSE-05, PARSE-06, PARSE-07, PARSE-08, PARSE-09, PARSE-10, PARSE-13]
---

# Phase 08 Plan 02: Carrier Template Registry Implementation (GREEN) Summary

**Carrier-template registry pattern added to email_parser.py: 5 STRATEGY_* constants, 3 carrier detect/parse function pairs, CARRIER_REGISTRY list, updated parse() dispatch, widened _TRACKING_PATTERNS for USPS/FedEx — all 25 tests green**

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add STRATEGY_* constants, carrier regex, update _TRACKING_PATTERNS | 88014e6 | custom_components/shop2parcel/api/email_parser.py |
| 2 | Add detect/parse fns for UPS/USPS/FedEx, CARRIER_REGISTRY, update parse() | ab15060 | custom_components/shop2parcel/api/email_parser.py |

## What Was Built

### STRATEGY_* Constants (5 new)

```python
STRATEGY_HTML = "html_template"    # existing Shopify HTML strategy
STRATEGY_UPS = "ups_template"      # new
STRATEGY_USPS = "usps_template"    # new
STRATEGY_FEDEX = "fedex_template"  # new
STRATEGY_REGEX = "regex_fallback"  # existing regex fallback
```

Bare string literals `strategy_used="html_template"` and `strategy_used="regex_fallback"` in `_parse_html_template` and `_parse_regex_fallback` were replaced with constant references. Tests import constants directly — no bare string comparisons.

### Carrier-Specific Regex (3 new, compiled at import)

```python
_UPS_TRACKING_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")
_USPS_TRACKING_RE = re.compile(r"\b(9[2345][0-9]{15,26})\b")
_FEDEX_TRACKING_RE = re.compile(r"\b([0-9]{12,20})\b")
```

All use bounded quantifiers — no ReDoS risk (T-ReDoS mitigation, ASVS V5).

### _TRACKING_PATTERNS Updates

| Pattern | Before | After | Reason |
|---------|--------|-------|--------|
| USPS domestic | `^[0-9]{20,22}$` | `^9[2345][0-9]{15,26}$` | Tighter prefix anchor (9+[2345]); extended length for 26-digit form used in USPS fixture |
| FedEx | `^[0-9]{12,15}$` | `^[0-9]{12,20}$` | Extended for FedEx SmartPost 20-digit numbers used in FedEx fixture |

The USPS change is tighter AND wider: the `9[2345]` prefix eliminates false positives from other digit strings, while `{15,26}` extends acceptance beyond the previous 22-digit cap.

### Carrier Detect/Parse Function Pairs (6 new module-level functions)

**Detection functions** — HTML-fingerprint-based, no sender header parameter (D-04):

| Function | Primary Marker | Guard |
|----------|---------------|-------|
| `_detect_ups` | `mcinfo@ups.com` | fallback: `ups.com AND not shopify` |
| `_detect_usps` | `usps.com` | none needed (low false-positive risk) |
| `_detect_fedex` | `fedex.com` | none needed (low false-positive risk) |

**Parse functions** — full `get_text()` + carrier regex + `_looks_like_tracking()` validation:

| Function | Regex Used | carrier_name | order_name | strategy_used |
|----------|-----------|-------------|-----------|--------------|
| `_parse_ups` | `_UPS_TRACKING_RE` | `"UPS"` | `""` | `STRATEGY_UPS` |
| `_parse_usps` | `_USPS_TRACKING_RE` | `"USPS"` | `""` | `STRATEGY_USPS` |
| `_parse_fedex` | `_FEDEX_TRACKING_RE` | `"FedEx"` | `""` | `STRATEGY_FEDEX` |

All parse_fn return `keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False}` — carrier templates do not run the fallback regex (PARSE-13).

### CARRIER_REGISTRY

```python
CARRIER_REGISTRY: list[tuple] = [
    (_detect_ups, _parse_ups),
    (_detect_usps, _parse_usps),
    (_detect_fedex, _parse_fedex),
]
```

Registry order: UPS first (highest-volume US carrier reduces average iteration), FedEx last (the 12-20 digit pattern has broadest match surface so placed after USPS's tighter prefix-anchored pattern).

### Updated EmailParser.parse()

```python
def parse(self, html: str, message_id: str, email_date: int) -> ParseResult:
    for detect_fn, parse_fn in CARRIER_REGISTRY:
        if detect_fn(html):
            return parse_fn(html, message_id, email_date)
    # No registry match — fall through to existing Shopify dual-strategy.
    html_result = self._parse_html_template(html, message_id, email_date)
    if html_result.shipment is not None:
        return html_result
    return self._parse_regex_fallback(html, message_id, email_date)
```

Signature unchanged — no `from_address` parameter added (D-04).

## Test Results

```
.venv/bin/pytest tests/api/test_email_parser.py -q
25 passed in 0.62s
```

```
.venv/bin/pytest -q
144 passed in 2.79s
```

10 tests (Plan 01 RED stubs) turned GREEN. All 15 pre-existing tests remain GREEN.

## T-Spoof Mitigation Summary

The `_detect_ups` guard prevents misclassifying Shopify merchant emails for UPS-fulfilled orders:

- The Shopify test fixture (`shopify_shipping_email.html`) contains `https://www.ups.com/track?...` in a `<p>` element.
- If `_detect_ups` used only `"ups.com" in html`, it would fire on this Shopify email.
- The two-tier guard: `mcinfo@ups.com` (UPS's transactional sender) is the primary fingerprint. The secondary guard `"ups.com" in html AND "shopify" not in html` prevents the Shopify fixture (which contains "shopify" in its HTML comment header) from triggering the UPS path.
- Test `test_ups_detect_fn_not_triggered_on_shopify_html` validates the guard against the Shopify fixture.

## T-ReDoS Mitigation Summary

All new regex patterns use bounded quantifiers:
- `_UPS_TRACKING_RE`: `{16}` — exact fixed length
- `_USPS_TRACKING_RE`: `{15,26}` — bounded range
- `_FEDEX_TRACKING_RE`: `{12,20}` — bounded range
- Updated `_TRACKING_PATTERNS` USPS: `{15,26}` — bounded
- Updated `_TRACKING_PATTERNS` FedEx: `{12,20}` — bounded

None of the new patterns use `+` or `*` on `[0-9]` or `[A-Z0-9]` character classes. All are compiled once at import time via `re.compile(...)`.

## no-HA-imports Rule Preserved

No `homeassistant.*` imports were added. `grep -c homeassistant custom_components/shop2parcel/api/email_parser.py` returns `0`. The `test_no_ha_imports` test continues to pass.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all carrier handlers extract real tracking numbers from fixtures and return populated `ShipmentData`. No placeholder values.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The `_FEDEX_TRACKING_RE` pattern `[0-9]{12,20}` is broad — it will match any 12-20 digit number. However:
1. `_detect_fedex` gates the parse on `"fedex.com" in html.lower()` first, so the regex only runs on FedEx-fingerprinted emails.
2. `_looks_like_tracking()` validation runs on the regex match result.
3. This is consistent with the plan's T-08-MissingTracking mitigation — false-positive extraction returns `ParseResult(shipment=..., skip_reason=None)` which the coordinator will attempt to forward; the parcelapp.net API will reject invalid tracking numbers via HTTP 400, which the coordinator handles via `UpdateFailed`.

This surface is within the plan's threat model (T-08-ReDoS, T-08-Spoof, T-08-MissingTracking) and no additional mitigations are required.

## Self-Check: PASSED

- `custom_components/shop2parcel/api/email_parser.py` — FOUND (346 lines)
- Commit `88014e6` — FOUND (feat(08-02): add STRATEGY_* constants...)
- Commit `ab15060` — FOUND (feat(08-02): add carrier registry...)
- `25 passed` in `tests/api/test_email_parser.py` — VERIFIED
- `144 passed` in full suite — VERIFIED
- `homeassistant` count = 0 in email_parser.py — VERIFIED
