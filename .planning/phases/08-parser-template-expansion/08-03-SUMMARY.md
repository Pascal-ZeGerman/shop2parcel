---
phase: 08-parser-template-expansion
plan: 03
subsystem: api
tags: [gmail, email-parser, const, options-flow]

# Dependency graph
requires:
  - phase: 04-coordinator-forwarding
    provides: options_flow.py with DEFAULT_GMAIL_QUERY constant import
provides:
  - DEFAULT_GMAIL_QUERY extended to from:-anchored combined query covering Shopify, UPS, USPS, FedEx
affects: [09-imap-multi-account]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gmail from:-anchored query with OR'd sender addresses plus subject filter for multi-carrier email capture"

key-files:
  created: []
  modified:
    - custom_components/shop2parcel/const.py

key-decisions:
  - "D-03 implemented: DEFAULT_GMAIL_QUERY extended from Shopify-only to combined from:+subject query targeting four known carrier sender addresses (Shopify, UPS, USPS, FedEx) with broad subject filter"
  - "from:-anchored approach selected over subject-only: RESEARCH.md confirmed real UPS/USPS/FedEx notification subjects do not contain 'shipped' — they use 'out for delivery', 'delivered', 'scheduled for delivery'"
  - "Existing config entries retain old CONF_GMAIL_QUERY value until user manually re-saves Options (expected behavior per Pitfall 5 — no automatic migration needed)"

patterns-established: []

requirements-completed: [PARSE-11]

# Metrics
duration: 4min
completed: 2026-05-02
---

# Phase 08 Plan 03: DEFAULT_GMAIL_QUERY Combined Carrier Query Summary

**DEFAULT_GMAIL_QUERY in const.py updated from Shopify-only sender filter to a four-carrier from:-anchored query (Shopify + UPS + USPS + FedEx) with broad subject scope**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-02T03:19:00Z
- **Completed:** 2026-05-02T03:23:23Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Updated `DEFAULT_GMAIL_QUERY` constant to capture Shopify merchant emails AND direct carrier notifications from UPS, USPS, and FedEx
- Preserved the constant name so `options_flow.py` and `test_options_flow.py` required no code changes
- All 4 options flow tests pass with the new value (they import by name, so they get the new string automatically)

## New DEFAULT_GMAIL_QUERY value

```
(from:no-reply@shopify.com OR from:mcinfo@ups.com OR from:auto-reply@usps.com OR from:TrackingUpdates@fedex.com) subject:(shipped OR delivered OR tracking OR package)
```

The `from:` anchor approach was selected because RESEARCH.md confirmed that real UPS/USPS/FedEx notification emails do NOT contain "shipped" in their subjects — they use "out for delivery", "scheduled for delivery", and similar delivery-focused language. A subject-only query (the original D-03 hypothesis) would miss all carrier notifications.

## Task Commits

Each task was committed atomically:

1. **Task 1: Update DEFAULT_GMAIL_QUERY to combined from:+subject query** - `9cb2dd6` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `custom_components/shop2parcel/const.py` — `DEFAULT_GMAIL_QUERY` updated from single-line Shopify-only string to multi-line concatenated query covering 4 carrier senders

## Decisions Made

- Used `from:` sender anchors as primary filter strategy (not `subject:`-only): RESEARCH.md Gmail Query Update Research section proves subject-only approach doesn't match real UPS/USPS/FedEx notification subjects
- Added `subject:(shipped OR delivered OR tracking OR package)` as secondary scope limiter: keeps result set bounded to shipping-relevant emails even when filtering by from: address
- Kept constant name `DEFAULT_GMAIL_QUERY` unchanged to avoid breaking the two existing importers

## Migration Note

**Existing installs:** Config entries that already have `CONF_GMAIL_QUERY` stored in `entry.options` retain the old value (`from:no-reply@shopify.com subject:shipped`) until the user opens the Options form and re-saves. This is expected HA behavior — `default=` in the Options schema only applies when the key is absent from `entry.options`. A future docs update (likely in the README) should note that existing users need to re-save Options to adopt the new default that captures carrier emails. This is Pitfall 5 from RESEARCH.md.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required. Users with existing installs who want the new default must manually open the Options form in HA and save without changes; this writes the new default to `entry.options`.

## Next Phase Readiness

- `const.py` provides the correct default query for carrier email capture
- Plans 08-01 (carrier templates) and 08-02 (tests + fixtures) in the same wave add the parser templates that consume these emails
- After wave merge, the full carrier template pipeline is operational: Gmail query captures carrier emails → carrier templates parse them → tracking numbers flow to parcelapp.net

---

## Self-Check

**Checking created/modified files exist:**
- `custom_components/shop2parcel/const.py` — FOUND (verified via Read tool)

**Checking commits exist:**
- `9cb2dd6` — FOUND (feat(08-03): update DEFAULT_GMAIL_QUERY...)

**Checking Python import:**
- `DEFAULT_GMAIL_QUERY` evaluates to exactly `(from:no-reply@shopify.com OR from:mcinfo@ups.com OR from:auto-reply@usps.com OR from:TrackingUpdates@fedex.com) subject:(shipped OR delivered OR tracking OR package)` — VERIFIED

**Test suite:**
- `tests/test_options_flow.py` — 4 passed — VERIFIED

## Self-Check: PASSED

---
*Phase: 08-parser-template-expansion*
*Completed: 2026-05-02*
