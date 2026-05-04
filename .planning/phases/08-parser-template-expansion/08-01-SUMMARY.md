---
phase: 08-parser-template-expansion
plan: "01"
subsystem: tests/fixtures + tests/api
tags: [fixtures, tdd, red-state, email-parser, carrier-templates]
dependency_graph:
  requires: []
  provides: [carrier-html-fixtures, carrier-test-stubs-red]
  affects: [08-02-PLAN.md]
tech_stack:
  added: []
  patterns: [minimal-synthetic-html-fixture, pytest-fixture-loading, tdd-red-state]
key_files:
  created:
    - tests/fixtures/ups_shipping.html
    - tests/fixtures/usps_shipping.html
    - tests/fixtures/fedex_shipping.html
  modified:
    - tests/api/test_email_parser.py
decisions:
  - "Fixture content matches PATTERNS.md exactly — verbatim HTML with carrier fingerprint and tracking number"
  - "Module-level STRATEGY_* import causes collection failure (RED state) — entire file collects as error until Plan 02 ships"
  - "FIXTURE_DIR replaces FIXTURE_PATH as directory reference; FIXTURE_PATH kept for backward compat"
metrics:
  duration: "~5 minutes"
  completed: "2026-05-02"
  tasks: 2
  files: 4
requirements: [PARSE-12]
---

# Phase 08 Plan 01: Carrier HTML Fixtures + Test Stubs (RED) Summary

**One-liner:** Synthetic UPS/USPS/FedEx HTML fixtures with carrier fingerprints and 10 RED test stubs that describe the carrier-template registry contract for Plan 02 to implement.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create three carrier HTML fixtures | 6cccd7f | tests/fixtures/ups_shipping.html, tests/fixtures/usps_shipping.html, tests/fixtures/fedex_shipping.html |
| 2 | Add carrier-template test stubs (RED) | 55186d8 | tests/api/test_email_parser.py |

## What Was Built

### Task 1: Carrier HTML Fixtures

Three synthetic HTML fixture files created in `tests/fixtures/`:

| File | Fingerprint | Tracking Number | Notes |
|------|-------------|-----------------|-------|
| `ups_shipping.html` | `mcinfo@ups.com` | `1Z0Y12345678031234` (18 chars, UPS format) | Also contains `ups.com` domain in href |
| `usps_shipping.html` | `usps.com` | `92123456508577307776690000` (26 digits, tests widened USPS regex) | usps.com appears in img src, tracking link, and tracking text |
| `fedex_shipping.html` | `fedex.com` | `61290912345678912345` (20 digits, tests widened FedEx regex) | fedex.com appears in img src and tracking link |

Each fixture begins with `<!-- Synthetic ...` comment and has `<!DOCTYPE html>`, matching the minimal-synthetic pattern from `shopify_shipping_email.html`. No PII.

### Task 2: Test Stubs (RED State)

Modified `tests/api/test_email_parser.py`:

- **Edit A:** Added `FIXTURE_DIR = Path(...)` alongside kept `FIXTURE_PATH` for backward compatibility
- **Edit B:** Added 3 new pytest fixtures (`ups_html`, `usps_html`, `fedex_html`) using `FIXTURE_DIR`
- **Edit C:** Extended import to include `STRATEGY_FEDEX`, `STRATEGY_HTML`, `STRATEGY_REGEX`, `STRATEGY_UPS`, `STRATEGY_USPS` (causes RED ImportError until Plan 02)
- **Edit D:** Appended 10 new test functions

**New test functions (10):**

| Function | Requirement | What it tests |
|----------|-------------|---------------|
| `test_strategy_constants_are_defined` | PARSE-01 / D-07 | All 5 STRATEGY_* constants with exact string values |
| `test_ups_template_extracts_tracking` | PARSE-04 | UPS tracking extraction, carrier_name, strategy_used, skip_reason |
| `test_usps_template_extracts_tracking` | PARSE-05 | USPS 26-digit tracking, STRATEGY_USPS |
| `test_fedex_template_extracts_tracking` | PARSE-06 | FedEx 20-digit SmartPost, STRATEGY_FEDEX |
| `test_ups_detect_fn_not_triggered_on_shopify_html` | PARSE-09 / T-Spoof | _detect_ups returns False on Shopify fixture |
| `test_usps_detect_fn_not_triggered_on_shopify_html` | PARSE-09 / T-Spoof | _detect_usps returns False on Shopify fixture |
| `test_fedex_detect_fn_not_triggered_on_shopify_html` | PARSE-09 / T-Spoof | _detect_fedex returns False on Shopify fixture |
| `test_registry_checks_carrier_templates_before_shopify_path` | PARSE-03 | Shopify fixture still routes to STRATEGY_HTML (no misclassification) |
| `test_ups_direct_email_has_empty_order_name` | PARSE-04 | Direct carrier emails have order_name="" |
| `test_ups_template_keyword_hits_all_false` | PARSE-13 | Carrier templates don't run fallback regex; keyword_hits all False |

**Total test count:** 25 (15 original preserved + 10 new stubs)

## RED State Confirmation

```
ImportError: cannot import name 'STRATEGY_FEDEX' from 
'custom_components.shop2parcel.api.email_parser'
```

Running `.venv/bin/pytest tests/api/test_email_parser.py --collect-only -q` fails at collection with the above ImportError. This is the expected RED state — Plan 02 adds the STRATEGY_* constants and carrier template implementations to turn these green.

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. Fixture files use synthetic data with no PII, fulfilling T-08-01 mitigation. The Shopify fixture guard test (T-08-02) is present as `test_ups_detect_fn_not_triggered_on_shopify_html`.

## Self-Check: PASSED

- `tests/fixtures/ups_shipping.html`: FOUND
- `tests/fixtures/usps_shipping.html`: FOUND
- `tests/fixtures/fedex_shipping.html`: FOUND
- `tests/api/test_email_parser.py`: FOUND (25 test functions)
- Commit `6cccd7f`: FOUND
- Commit `55186d8`: FOUND
- RED state (ImportError on STRATEGY_FEDEX): CONFIRMED
