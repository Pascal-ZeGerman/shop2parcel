---
status: resolved
trigger: "Diagnose tracking numbers sent to Parcel app — are they valid/resolvable?"
created: 2026-05-15T00:00:00Z
updated: 2026-05-15T00:00:00Z
symptoms_prefilled: true
goal: find_root_cause_only
---

## Current Focus

hypothesis: CONFIRMED — all fixture tracking numbers pass format validation; one bug found: IMAP path sends empty description when order_name is missing (direct carrier emails)
test: Traced full data flow; ran _looks_like_tracking on all fixture numbers; ran end-to-end pipeline
expecting: Root cause identified
next_action: N/A — diagnosed

## Symptoms

expected: Valid carrier tracking numbers (UPS/FedEx/USPS/DHL format) sent to Parcel API
actual: Tracking numbers are format-valid; one behavioral divergence in IMAP path description field
errors: None reported — concern is silent submission of invalid/empty tracking numbers
reproduction: Read code + fixtures to trace data flow
started: Ongoing concern about data quality

## Eliminated

- hypothesis: Tracking numbers fail format validation before submission
  evidence: All 7 fixture/test tracking numbers pass _looks_like_tracking(); each matches the correct carrier pattern
  timestamp: 2026-05-15

- hypothesis: Carrier code mapping fails silently (sends invalid carrier to parcelapp)
  evidence: UPS/USPS/FedEx all map correctly; only "Unknown" carrier_name produces "pholder" fallback which is a valid parcelapp code
  timestamp: 2026-05-15

## Evidence

- timestamp: 2026-05-15
  checked: _TRACKING_PATTERNS in email_parser.py + all fixture tracking numbers
  found: All fixture numbers pass validation; patterns correctly match each carrier
  implication: Format validation is sound — no tracking number reaches the API in an invalid format

- timestamp: 2026-05-15
  checked: End-to-end pipeline (parse -> carrier_code -> description) on all fixtures
  found: href_tracking.html and plain_text_tracking.txt produce carrier_name="Unknown" -> carrier_code="pholder". This is expected behavior (pholder is a valid parcelapp code).
  implication: Unknown carrier falls back to pholder — not an error, just less precise tracking

- timestamp: 2026-05-15
  checked: gmail_coordinator.py line 319 vs imap_coordinator.py line 303
  found: Gmail uses `description=shipment.order_name or shipment.tracking_number` (fallback). IMAP uses `description=shipment.order_name` (no fallback). Direct carrier emails (UPS/USPS/FedEx templates) always produce order_name="" — IMAP path sends empty description string.
  implication: IMAP users with direct carrier emails get description="" posted to parcelapp.net

- timestamp: 2026-05-15
  checked: _infer_carrier() test code uses 18-digit FedEx number (612909123456789123)
  found: No real FedEx format uses 18 digits. FedEx formats are exactly 12, 15, or 20 digits. The 12-20 range regex accepts 18 but no real carrier issues 18-digit numbers.
  implication: Test fixture uses a non-real-world number, but _looks_like_tracking still accepts it. In production this is moot — real emails won't have 18-digit numbers.

- timestamp: 2026-05-15
  checked: USPS fixture (26-digit: 92123456508577307776690000)
  found: Pattern ^9[12345][0-9]{15,24}$ accepts 26 digits. 92-prefix 26-digit numbers are uncommon in practice (real Priority Mail is 22 digits). The fixture is syntactically accepted but represents an atypical USPS format.
  implication: Pattern is permissive (accepts wider range than typical); no false negatives for real numbers

## Resolution

root_cause: |
  Two findings, neither prevents tracking numbers from being submitted:

  FINDING 1 (behavioral bug): IMAP coordinator sends empty description to parcelapp.net
  for direct carrier emails (UPS/USPS/FedEx). Gmail coordinator correctly falls back to
  the tracking_number when order_name is empty ("" or shipment.tracking_number). IMAP
  coordinator (imap_coordinator.py:303) passes description=shipment.order_name directly
  with no fallback. Direct carrier emails (strategy: ups_template, usps_template,
  fedex_template) always set order_name="" by design, so IMAP users always get
  description="" for these emails.

  FINDING 2 (format gap): The FedEx pattern ^[0-9]{12,20}$ accepts any 12-20 digit
  string. Real FedEx numbers are exactly 12, 15, or 20 digits — lengths 13, 14, 16, 17,
  18, 19 do not exist in real FedEx tracking. The broad range creates potential false
  positives if Tier 2 broad scan is enabled (it is off by default, so production impact
  is low). The 18-digit test number used in test_email_parser.py is not a real FedEx
  format.

  The tracking numbers themselves (from real emails) will be valid and resolvable if the
  email parser extracts them correctly. The format validation in _looks_like_tracking()
  is sound for all major carrier formats used by Shopify.

fix: N/A — diagnose only
verification: N/A
files_changed: []
