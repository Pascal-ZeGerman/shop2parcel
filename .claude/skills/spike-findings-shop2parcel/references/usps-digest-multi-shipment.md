# USPS Digest Multi-Shipment Sender Association

## Requirements

- Debug/implementation code must import the actual production `EmailParser`/`OllamaExtractor`/
  `merge_llm_authoritative_with_grounding` — never reimplement extraction or merge logic.
- Do NOT rely on the Stage-2 LLM to extract per-package sender/description for multi-package USPS
  Informed Delivery digests — it reliably declines even when the text is present, unambiguous, and
  explicitly permitted by the prompt (0/5 across two independent spikes, 016 and 022; three
  separate prompt-engineering fixes already failed in spike 018 — rendering, few-shot, explicit
  instruction. Do not re-attempt any of these three without new evidence).
- A Stage-1 structural fix (reading USPS's own HTML template ids) SHIPPED in Phase 36, completed
  2026-07-24, and lives in `custom_components/shop2parcel/api/email_parser.py` as
  `_extract_usps_shippers`, called from `_parse_usps` — see "How to Build It" below.
- The shipped implementation carries a `len(shippers) == len(tracking_numbers)` guard before
  assigning structural sender data, and it must not be removed — the synthetic pytest fixtures
  (`tests/fixtures/usps_digest.html`, `usps_shipping.html`) do not use USPS's real template ids at
  all and would show a count mismatch (0 shippers vs. N tracking numbers) if this guard were
  skipped, mis-pairing sender data.

## How to Build It

**The multi-shipment extraction mechanism already exists — do not rebuild it.**
`email_parser.py::_parse_usps` (lines ~313-364) already uses `findall` (not `search`) to return
every tracking number in a USPS digest: the first as `ParseResult.shipment`, the rest as
`ParseResult.extra_shipments`. `gmail_coordinator.py:565` and the IMAP equivalent already loop over
`[result.shipment, *result.extra_shipments]`, giving each sibling its own storage key and its own
`_enqueue_stage2(...)` call. This was confirmed working end-to-end by spike 022 (0/5 cross-
attribution across both siblings under the real production prompt).

**What's missing: per-package sender extraction.** USPS Informed Delivery digest HTML pairs a
shipper-name span with a tracking-number span in the same package block, in document order, using
USPS's own literal template ids:

```html
<span id="pra-shipper-name-id">PRIMARY KIDS INC</span>  <!-- empty when no sender data upstream -->
...
<span id="pra-tracking-number-id">9261290316868031775213</span>
```

`_extract_usps_shippers` is live production code inside `_parse_usps` — the block below reproduces
the validated spike-023 prototype it was built from, so a reader who needs the current text should
read `email_parser.py` rather than trusting this copy (see
`sources/023-structural-sender-extraction/run_structural_sender_check.py` for the original):

```python
def _extract_usps_shippers(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    raw = _USPS_TRACKING_RE.findall(text)
    tracking_numbers = list(dict.fromkeys(m for m in raw if _looks_like_tracking(m)))

    shipper_spans = soup.find_all(id="pra-shipper-name-id")
    shippers = [s.get_text(strip=True) for s in shipper_spans]

    if len(shippers) != len(tracking_numbers):
        # Guard: pairing assumption doesn't hold (e.g. synthetic fixture without
        # real template ids) — skip structural assignment entirely, fall back
        # to today's order_summary=None behavior. Do NOT guess by partial index.
        return []
    return shippers
```

Then, when building each sibling's `ShipmentData` in `_parse_usps`, set
`order_summary=shipper or None` from the paired shipper (or `None` if the guard tripped or the
span was empty — matches the existing "no sender data upstream" case, which is correct behavior,
not a bug).

**No merge.py changes needed.** `merge_llm_authoritative`'s existing `_stage1_missing()` semantics
already make a Stage-1-populated `order_summary` win outright over Stage-2 — a populated value from
this structural extraction is never overwritten, and the MRG-05 grounding gate is simply never
invoked for that field (there's nothing to gate). Validated: 5/5 correct on the real target case
with zero regression to the merge/grounding architecture.

## What to Avoid

- **Prompt engineering for this exact failure mode.** Three independent attempts already failed
  (spike 018: block-aware rendering, few-shot example, explicit digest-shape instruction) — the
  model declines to use present, permitted, unambiguous merchant text in this specific digest
  shape. Do not re-attempt without new evidence (e.g. a different/larger Ollama model — the only
  untested lever, per spike 017/018).
- **Assuming shipper-span-count-matches-tracking-count without checking.** The existing synthetic
  pytest fixtures (`usps_digest.html`) have 0 shipper spans against 3 real tracking numbers — a
  naive index-zip without the count guard would silently mis-pair or crash.
- **Reusing the shared `_looks_like_tracking()`/`_TRACKING_PATTERNS` validator for anything beyond
  tracking-number shape validation.** It has nothing to do with sender extraction — this is a
  separate, unrelated concern.
- **Trusting a single sample as representative for Stage-2 behavior.** `temperature=0` is NOT
  reliably deterministic against this Ollama deployment — always resample (N=5 minimum, per
  established convention) before drawing conclusions about extraction reliability.

## Constraints

- USPS's `pra-shipper-name-id`/`pra-tracking-number-id` ids are specific to the Informed Delivery
  Daily Digest template — confirmed present in 3 real digest emails (2-package and 1-package
  shapes both tested), absent from the synthetic pytest fixtures.
- `pra-tracking-number-id` has a `-secondary` duplicate (hidden mobile-view copy of the same
  number); `pra-shipper-name-id` does not — `find_all(id="pra-shipper-name-id")` returns exactly
  one span per physical package, no dedup needed.
- The real digest corpus used for validation lives at
  `sources/014-tracking-fp-root-cause/corpus/spike009_9361289691066362556322_usps.html`
  (confirmed byte-content-identical to the user's supplied 2-package example — msg
  `19f7a215e530abd5`, 2026-07-19 digest) and
  `sources/014-tracking-fp-root-cause/corpus/spike010_9261290316868031775213_usps.html`
  (a different, 1-package real digest — regression-checked in spike 023).

## Origin

Synthesized from spikes: 022, 023 (building on 014, 016, 018, 019, 020)
Source files available in: `sources/022-digest-same-blob-stage2-risk/`,
`sources/023-structural-sender-extraction/`

Shipped/confirmed history: spike 023's candidate shipped in Phase 36 (2026-07-24); quick task
260807-usps-dhl-check (2026-08-07) confirmed this by reading `email_parser.py` directly, not by
re-running the spike (neither quick task was itself a spike).
