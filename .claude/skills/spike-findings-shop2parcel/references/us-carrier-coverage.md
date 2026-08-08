# US Carrier Coverage (Home-Assistant-Mail-And-Packages Corpus)

## Requirements

- Any new carrier parser must mirror the existing `CARRIER_REGISTRY` pattern exactly
  (`_detect_X`/`_parse_X` pair, boundary-anchored domain regex per WR-03, `STRATEGY_X` naming
  convention exported for tests).
- Before reusing the shared `_looks_like_tracking()`/`_TRACKING_PATTERNS` validator for a new
  carrier, confirm the carrier's tracking-number shape is actually covered — it only covers
  UPS/USPS/FedEx shapes today. A carrier with a different shape will silently fail validation if
  the shared validator is reused unmodified. A carrier-aware escape hatch now exists: pass
  `carrier_name` into `validate_carrier_format(value, *, carrier_name=None)` to additively widen
  acceptance for one carrier without touching the shared `_TRACKING_PATTERNS` list — see the
  resolved-history block at the end of the DHL subsection below.
- Do NOT build Amazon carrier support without first confirming parcelapp.net has a supported
  carrier code for it (`https://api.parcel.app/external/supported_carriers.json`, referenced in
  `carrier_codes.py`'s own docstring) — as of this spike round, `"amazon"` is not in
  `carrier_codes.py`'s mapping at all.
- Before treating a new carrier as done, trace its tracking-number shape through every production
  pre-POST gate, not just through the parser. `validate_carrier_format` is the shared chokepoint
  every POST path re-gates through (the worker, the Stage-2 drain re-gate, and the Gmail/IMAP
  inline fallbacks); a carrier whose shape that function does not accept will parse, store, and
  log correctly while silently never being POSTed. This is exactly what happened to DHL between
  Phase 36 (2026-07-24) and quick task 260807-tpu (2026-08-07) — see What to Avoid below.

## How to Build It

**DHL Express — shipped in Phase 36, completed 2026-07-24.** Lives today in
`custom_components/shop2parcel/api/email_parser.py`: `STRATEGY_DHL`, `_dhl_looks_like_tracking`,
`_detect_dhl`, `_parse_dhl`, and its `CARRIER_REGISTRY` entry. The shipped shape mirrors
`_detect_ups`/`_parse_ups` exactly (see
`sources/024-moralmunky-gap-fix-prototype/run_dhl_gap_check.py` for the validated spike-024
prototype it was built from):

```python
_DHL_DOMAIN_RE = re.compile(r"(?:^|[\s\"'/@.=(>])dhl\.com\b")
_DHL_TRACKING_RE = re.compile(r"waybill\s+number[^0-9]{0,10}(\d{9,11})", re.IGNORECASE)

def _detect_dhl(html: str) -> bool:
    html_lower = html.lower()
    return bool(_DHL_DOMAIN_RE.search(html_lower)) and "shopify" not in html_lower

def _parse_dhl(html: str, message_id: str, email_date: int) -> ParseResult:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _DHL_TRACKING_RE.search(text)
    if m and _dhl_looks_like_tracking(m.group(1)):  # local validator, see below — NOT _looks_like_tracking
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="DHL Express",  # matches carrier_codes.py's existing "dhl express" -> "dhl" key
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_DHL,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(shipment=None, skip_reason="no_template_match", strategy_used=None,
                        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False})
```

It is registered in `CARRIER_REGISTRY` after the other three entries (order matters — see
RESEARCH.md ordering analysis referenced in `email_parser.py`'s own comments).

`carrier_codes.py` already maps `"dhl express"` → `"dhl"` — this mapping shipped unchanged.

**Implementation-choice history — resolved, not open:**
1. Phase 36 shipped option (a): a local `_parse_dhl`-scoped validator (`_dhl_looks_like_tracking`),
   deliberately leaving `_TRACKING_PATTERNS`, `_infer_carrier`, `validate_carrier_format`, and
   `merge.py` untouched (Decision 1, `36-01-PLAN.md`).
2. That choice was REVERSED by quick task 260807-tpu on 2026-08-07, because it made DHL support
   non-functional in production (see What to Avoid below).
3. The current shape: `validate_carrier_format(value, *, carrier_name=None)` — an additive
   OR-widening, not a switch. When `carrier_name` resolves to DHL, DHL's bare 9-11-digit shape is
   additionally accepted by reusing the existing local `_dhl_looks_like_tracking`;
   `carrier_name=None` is byte-identical to the pre-change behaviour. `_TRACKING_PATTERNS` was NOT
   extended, so the R5 bare-digit false-positive fix stays closed for every other carrier and for
   every carrier-context-free callsite. This is wired through MRG-04 (using `stage1.carrier_name`,
   never the LLM's own carrier claim) and through all four production pre-POST gates: the worker
   (`job.shipment.carrier_name`), the Stage-2 drain re-gate, and the Gmail and IMAP inline
   fallbacks.

**Amazon — do not build a parser fix.** Spike 021 traced the "0/9 miss" to two root causes, neither
of which a regex change fixes:
1. These fixtures embed no third-party carrier tracking number at all — only Amazon's own
   `orderId`/`packageId` behind an Amazon-hosted tracking redirect
   (`amazon.com/gp/your-account/ship-track?...`). Amazon Logistics self-delivery uses a distinct
   `TBA`-prefixed tracking ID format, not present in any of the 9 fixtures examined.
2. `carrier_codes.py` has no `"amazon"` parcelapp carrier code — even a successfully-extracted
   Amazon order ID would have nowhere valid to POST.
   Resolve the product question first (does parcelapp support Amazon tracking, under what carrier
   code, for what tracking-ID shape) before writing any parser code.

## What to Avoid

- **Shipping a carrier parser with green unit tests is not evidence the carrier works
  end-to-end.** Phase 36's DHL support was completely non-functional in production for two weeks
  (2026-07-24 to 2026-08-07): every DHL tracking number was silently rejected at every pre-POST
  gate — `validate_carrier_format` did not yet know DHL's bare 9-11-digit waybill shape — so no
  DHL parcel ever actually reached parcelapp.net. Phase 36's own unit tests passed the whole time
  because they exercised the parser, not the pre-POST gate chain. Fixed by quick task 260807-tpu's
  carrier-aware `validate_carrier_format(value, *, carrier_name=None)` widening.
- **Copy-pasting `_looks_like_tracking()` for a new carrier without checking its shape is covered.**
  This was the historical setup for the incident above: DHL's 10-digit shape silently failed
  against the shared `_TRACKING_PATTERNS` list, which is exactly why `_parse_dhl` needed its own
  local `_dhl_looks_like_tracking` validator in the first place — and why that local-only choice
  later needed the carrier-aware gate widening to become reachable from the pre-POST gate chain.
- **Trusting a single-fixture "first match wins" as fully validated.** The one available DHL
  fixture (`dhl_out_for_delivery.eml`) contains TWO distinct 10-digit numbers — an English
  `"waybill number 4212345678"` sentence and a Spanish `"número de guía 4294142591"` table-value —
  apparently a multi-locale bundled template. The candidate's `.search()` deterministically picks
  the first (English) match, consistent with `_parse_ups`/`_parse_fedex`'s own convention, but this
  is untested against a second independent DHL sample. Get a second real DHL fixture before
  treating this as fully proven.
- **Building Amazon support from the "0/9 miss" alone** — it reads like a parser gap but isn't one;
  see Requirements above.

## Constraints

- Source corpus: MIT-licensed `Home-Assistant-Mail-And-Packages` project
  (github.com/moralmunky/Home-Assistant-Mail-And-Packages), `tests/test_emails/` — real captured
  `.eml` MIME dumps, not synthetic HTML. 19 US-relevant fixtures copied into
  `sources/021-moralmunky-corpus-coverage/corpus/` (license verified before copying).
- Non-US carriers/locales in the source repo (`amazon_uk_*`, `amazon_delivered_it`,
  `amazon_shipped_it`, `auspost_*`, `dpd_com_pl_*`, `gls_*`, `hermes_*`, `inpost_pl_*`,
  `poczta_polska_*`, `royal_mail_uk_*`) were explicitly excluded — out of scope per the "US
  relevant" framing this corpus expansion was requested under.
- Confirmed against the real fixture corpus: FedEx 2/2, USPS 4/4 (including two USPS Informed
  Delivery digest shapes new to this project — `informed_delivery_missing_mailpiece.eml`,
  `informed_delivery_no_mail.eml`, worth adding to `tests/fixtures/` regardless of any other
  change), UPS 1/1-with-an-HTML-body (the other UPS fixture is plain-text-only, structurally
  unreachable, not a parser gap).
- DHL candidate validated with 0/6 false positives against the real pytest regression corpus
  (`tests/fixtures/*.html`).

## Origin

Synthesized from spikes: 021, 024
Source files available in: `sources/021-moralmunky-corpus-coverage/`,
`sources/024-moralmunky-gap-fix-prototype/`

Shipped/reversed history: Phase 36 (2026-07-24) shipped the spike 024 candidate; quick task
260807-tpu (2026-08-07) reversed its Decision 1 (neither quick task was itself a spike).
