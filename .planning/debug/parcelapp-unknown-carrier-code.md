---
slug: parcelapp-unknown-carrier-code
status: resolved
trigger: "It seems that we find valid tracking numbers, and i can see in my parcelapp access tgat they get posted, but they don't work there. Check if the numbers get handed over properly to parcel app"
created: 2026-05-16
updated: 2026-05-16
---

## Symptoms

- **Expected**: Tracking numbers posted to parcelapp are tracked and show shipment status updates
- **Actual**: Entries appear in parcelapp but show as "unknown / untracked carrier" — no status
- **Scope**: All posted tracking numbers across the board (not isolated to one carrier)
- **Error messages**: None in HA logs beyond the existing "description missing" 400 errors
- **Timeline**: Unknown — may have never worked correctly
- **Reproduction**: Any email-matched tracking number forwarded to parcelapp.net API

## Current Focus

```yaml
hypothesis: "CONFIRMED: _parse_fedex fails to extract tracking from FedEx Delivery Manager emails (regex requires 'Tracking number:' prefix not present), falls through to _parse_html_template which extracts via href fallback but sets carrier_name='Unknown', yielding carrier_code='pholder' to parcelapp.net"
test: "Verified with live simulation: _detect_fedex fires, _parse_fedex returns None (no regex match), _parse_html_template extracts TN via href but cannot infer carrier from text, normalize_carrier('Unknown') => 'pholder'"
expecting: "carrier_code='fedex' but actually posting carrier_code='pholder'"
next_action: "fix _parse_fedex to include href fallback when regex fails"
reasoning_checkpoint: "Confirmed via direct Python execution reproducing the exact failure path"
```

## Evidence

- timestamp: 2026-05-16T00:00:00Z
  finding: "normalize_carrier('FedEx') correctly returns 'fedex' — carrier_codes.py is NOT the bug"
  confidence: high

- timestamp: 2026-05-16T00:00:00Z
  finding: "_FEDEX_TRACKING_RE requires prefix 'tracking\\s+(?:number|#|no\\.?)\\s*:?\\s*' before digits — FedEx Delivery Manager emails do not include this text prefix in their body"
  confidence: high

- timestamp: 2026-05-16T00:00:00Z
  finding: "FedEx Delivery Manager emails have tracking numbers in hrefs: ?trknbr=380936866496 — the href fallback in _parse_fedex is missing (only _parse_html_template has it)"
  confidence: high

- timestamp: 2026-05-16T00:00:00Z
  finding: "Reproduced: simulated FedEx DM email -> detect_fedex=True, _parse_fedex returns None, _parse_html_template extracts TN='380936866496' via href but carrier_name='Unknown', normalize_carrier('Unknown')='pholder'"
  confidence: confirmed

- timestamp: 2026-05-16T00:00:00Z
  finding: "parcelapp.net carrier code 'fedex' exists and is valid (confirmed from supported_carriers.json). The code is never being sent — 'pholder' is sent instead."
  confidence: high

## Eliminated Hypotheses

- carrier_codes.py mapping wrong: normalize_carrier('FedEx') correctly returns 'fedex'. The mapping table is correct.
- parcelapp.net rejecting 'fedex' code: fedex is confirmed valid in supported_carriers.json.
- Tracking numbers invalid format: all 5 reported numbers (12-digit) pass _looks_like_tracking().

## Resolution

```yaml
root_cause: "_parse_fedex has no href fallback — when FedEx Delivery Manager emails lack the 'Tracking number:' text prefix, it returns None; _parse_html_template then picks up the TN via hrefs but sets carrier_name='Unknown'; normalize_carrier('Unknown') returns 'pholder' which is posted to parcelapp.net instead of 'fedex'"
fix: "Add href fallback to _parse_fedex: when _FEDEX_TRACKING_RE finds no match, call _extract_tracking_from_hrefs(soup); if found, return ShipmentData with carrier_name='FedEx' and strategy_used=STRATEGY_FEDEX"
verification: "Test with simulated FedEx Delivery Manager HTML where TN is in ?trknbr= href but not in text — result.shipment.carrier_name should be 'FedEx', normalize_carrier output should be 'fedex'"
files_changed:
  - custom_components/shop2parcel/api/email_parser.py
```
