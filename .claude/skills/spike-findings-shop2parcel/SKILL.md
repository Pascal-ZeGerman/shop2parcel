---
name: spike-findings-shop2parcel
description: Implementation blueprint from spike experiments. Requirements, proven patterns, and confirmation that the MRG-05 grounding gate and Stage-1 scoping fix are already shipped and validated end-to-end for Shop2Parcel's email-parsing diagnostics and Stage-2 LLM order-name extraction. Also covers a validated structural fix for USPS digest per-package sender association (shipped in Phase 36), US-carrier coverage (DHL shipped in Phase 36, its pre-POST validator gap fixed by 260807-tpu; Amazon confirmed out of scope) against a real independent fixture corpus, and a user-configurable sender-exclusion filter (options-flow UI, no YAML, shipped in 260807-qw1) that must never exclude USPS Informed Delivery. Auto-loaded during implementation work.
---

<context>
## Project: Shop2Parcel

Shop2Parcel is a Home Assistant integration that parses Shopify/carrier shipping emails and
forwards tracking data to parcelapp.net. Spikes covered two areas: (1) debugging tools and a
validated fix for the Stage-1 regex/HTML `EmailParser`'s `<p>`-only scoping gap, and (2) whether
the existing Stage-2 Ollama LLM path reliably produces a meaningful `order_name`/`order_summary`
("shop — product") — it does, most of the time, but with a real, confirmed hallucination risk
that compounds with the Stage-1 scoping gap. A complete grounding-gate design (MRG-05) was built
and validated across 40+ live-model samples, including a structural fix for its one confirmed
blind spot (generic sender labels).

**Second round (spikes 014-018):** triggered by two specific real production entries the user
flagged as wrong. Fetching the actual emails (via the production `GmailClient`, live OAuth token)
overturned both original hypotheses: the "fabricated" FedEx tracking number was real, correctly
extracted, and checksum-valid (nothing to fix); one of two "missing description" cases was
genuinely undata upstream, but the other was a real, 100%-reproducible false-negative — real
sender text the model failed to use despite the prompt already permitting it. Three independent
fixes were tried (block-aware text rendering, a few-shot prompt example, an explicit instruction)
and all three failed to fix it; the few-shot attempt also surfaced a real gap in the MRG-05 gate
design (doesn't catch cross-shipment misattribution in multi-package digest emails). Real USPS
Informed Delivery digest and FedEx Delivery Manager emails — shapes with zero prior fixture
coverage — are now part of this project's spike corpus.

**Third round (spikes 019-020, integration):** proposed as "wire the candidates into a real
implementation and check for integration risk" — but investigation found both candidates (spike
013's Stage-1 fix, the MRG-05 grounding gate from 007/011/012) had **already shipped to
production** between the first and second spike rounds, discovered by reading `email_parser.py`
and `merge.py` directly rather than assuming. Spike 019 found the real digest/carrier-direct
corpus never even reaches the code 013 patches (a separate carrier registry resolves it first);
spike 020 ran the real shipped pipeline end-to-end against the full real+synthetic corpus (7
cases, 35 live samples) with zero regressions, confirming defense-in-depth (MRG-03 + MRG-05)
works as designed against real production data, not just synthetic fixtures.

**Fourth round (spikes 021-024):** two goals — expand US-carrier coverage using an independent
real fixture corpus, and validate a fix for the multi-package USPS digest sender-association gap
018 left open. Spike 022 confirmed the multi-shipment extraction mechanism (`extra_shipments`) was
already fully wired end-to-end (unverified until now — spike 020 loaded the same corpus file but
never exercised it) and that spike 018's cross-attribution risk stays latent under the real
production prompt, but the real gap holds: neither sibling's description gets populated with real
sender text (0/5). Spike 023 closed it with a deterministic Stage-1 structural fix (reading USPS's
own HTML template ids) — 5/5 correct, where three LLM prompt-engineering attempts had already
failed. Spike 021 scanned the MIT-licensed Home-Assistant-Mail-And-Packages project's real `.eml`
fixture corpus and found FedEx/USPS/UPS already hold up well, Amazon's "0/9 miss" is a
product-scope question (no tracking number in the emails, no parcelapp carrier code) not a parser
bug, and DHL is a real, tractable gap — validated in spike 024 with a candidate mirroring the
existing carrier-registry pattern exactly.

**Fifth round (spikes 025-027):** triggered by quick task 260806-v2j's enriched carrier-format-
rejection DEBUG logging (deployed live 2026-08-06), which surfaced real subject/sender pairs for
every rejection for the first time. The user flagged two live examples — a community newsletter
(never a shipment) and a USPS Informed Delivery digest (which prior spike history says sometimes
genuinely carries tracking data) — and asked to (a) ground-truth the specific USPS rejection,
(b) classify a broader sample of real senders as reliable-vs-noise, and (c) design a
user-configurable exclusion filter. Spike 025 found both flagged USPS "misses" were actually
genuinely empty digest days (verified against USPS's own `total-packages`/`no-packages-today`
template markers) — not bugs. Spike 026 aggregated the live enriched-log evidence itself (no new
Gmail fetch needed) into a clean reliable-vs-noise classification across 14 real sender domains.
Spike 027 designed and validated a user-configurable sender-exclusion filter (options-flow UI, no
YAML, exact-domain-match only) against the full real corpus with zero false exclusions.

**Implementation round (2026-08-07, NOT a spike round — created no new spikes):** Three quick
tasks. (1) Spike 027's sender-exclusion filter was implemented and shipped (quick task
260807-qw1). (2) Spikes 023 and 024's candidates were found already shipped — in Phase 36,
completed 2026-07-24 — by reading `email_parser.py` directly (quick task 260807-usps-dhl-check,
investigation only, no commits). This is the second time in this project's history that a
candidate assumed outstanding turned out to be already live; the same thing happened in the third
round with spikes 019/020. (3) Investigating the DHL validator question surfaced a real production
bug: Phase 36's DHL support had been dead-ended at every pre-POST gate since it shipped, so no DHL
parcel had ever reached parcelapp.net. Fixed by quick task 260807-tpu with a carrier-aware
additive widening of `validate_carrier_format`, wired through MRG-04 and all four production
pre-POST gates, with `_TRACKING_PATTERNS` left unchanged.

Spike sessions wrapped: 2026-07-15, 2026-07-16, 2026-07-23, 2026-07-24, 2026-08-07
</context>

<requirements>
## Requirements

- Debug/diagnostic tools must import the actual production modules (`EmailParser`,
  `OllamaExtractor`, `merge_llm_authoritative`) — never reimplement extraction or merge logic.
- No new pip dependencies for diagnostics — `bs4`, `lxml`, `aiohttp` are already in `.venv`.
- **The MRG-05 grounding/verification gate for Stage-2 `order_name`/`order_summary` promotion on
  a Stage-1-blank field is ALREADY SHIPPED IN PRODUCTION** (confirmed live in `merge.py`/
  `coordinator.py` by spike 020, 2026-07-23) — `tracking_number` has had its own equivalent
  (MRG-04, carrier-format gate) for longer. **See `references/mrg-05-grounding-gate.md` for where
  it lives and how it was confirmed working end-to-end against real data. Do not re-derive it or
  treat it as an outstanding implementation task.**
- **The MRG-05 gate must never treat sender/subject header tokens as grounding evidence** — ground
  only against body-only prose, even if/when Stage-2 prompt enrichment (Subject/From context) is
  ever added for real. This is a confirmed, closed blind spot — see the same reference.
- **Do not assume `temperature=0` means deterministic model output against this Ollama
  deployment.** A byte-identical prompt produced meaningfully different hallucination rates
  (4/4, 1/5, 0/10, 6/6) across separate live-testing sessions. Never trust a single live-model
  call as representative — resample before drawing conclusions, in spikes and in real tests alike.
- **The companion Stage-1 fix is ALSO ALREADY SHIPPED**: `_parse_html_template` broadens only the
  tracking-number shape-sniffing pass to `<div>`/`<span>`, keeps `order_name`/`carrier_name` label
  regexes `<p>`/`<td>`-scoped (marked `SC-3` in the source). Confirmed zero regressions/false
  positives, and confirmed it has zero effect on the real digest/carrier-direct email corpus
  (that corpus never reaches this function — a separate carrier registry resolves it first). See
  `references/email-parsing-diagnostics.md`.
- Ollama server for local testing: `192.168.0.190:11434`, model `qwen2.5:1.5b`, reachable via LAN
  but outside the command sandbox's network allowlist — scripts calling it need
  `dangerouslyDisableSandbox: true`.
- **Do not build a fix for a flagged production entry (a suspected false positive, a suspected
  fabrication) without first fetching and reading the actual source email.** Two independent
  hypotheses in this project's history (a suspected false-positive FedEx tracking number, an
  assumed-uniform "empty order_summary means the same thing every time") both turned out wrong
  once the real email was fetched — see `references/email-parsing-diagnostics.md` and
  `references/stage2-llm-order-extraction.md`.
- **Three prompt/preprocessing changes were tried and failed to fix the one confirmed real
  Stage-2 false-negative (a digest email with real, unused sender text) — do not re-attempt any
  of these without new evidence:** block-aware text rendering, a few-shot prompt example, an
  explicit digest-shape instruction. See `references/stage2-llm-order-extraction.md` "What to
  Avoid" for the full investigation trail. The only untested lever is a larger/different Ollama
  model.
- **The MRG-05 grounding gate design (above) does NOT catch cross-shipment misattribution in
  multi-package digest emails** — it verifies textual presence, not per-shipment attribution.
  This is a known, evidence-backed gap in the current design, not a solved problem — see
  `references/mrg-05-grounding-gate.md` "Known Gap" before extending Stage-2 to handle digest
  emails or tuning the prompt to extract more aggressively from thin/sparse fields.
- **Do NOT rely on the Stage-2 LLM for per-package sender/description in multi-package USPS
  digests** — a deterministic Stage-1 structural fix (reading USPS's own
  `pra-shipper-name-id`/`pra-tracking-number-id` template ids) SHIPPED in Phase 36 as
  `_extract_usps_shippers`. See `references/usps-digest-multi-shipment.md`.
- **Do not build Amazon carrier support without first confirming parcelapp.net has a supported
  carrier code for it** — Amazon shipping emails embed no third-party tracking number, and
  `carrier_codes.py` has no `"amazon"` entry today. See `references/us-carrier-coverage.md`.
- **DHL shipped in Phase 36**, mirroring the existing `CARRIER_REGISTRY` pattern exactly. The
  local-vs-shared-validator question was resolved twice: option (a), a local-only validator, in
  Phase 36; then reversed by quick task 260807-tpu (2026-08-07) into the carrier-aware
  `validate_carrier_format(value, *, carrier_name=None)` form. See
  `references/us-carrier-coverage.md`.
- **A USPS Informed Delivery "no tracking found" result is NOT automatically evidence of a
  parsing bug** — it sends a digest every day regardless of whether any package is arriving, and
  a genuinely empty digest correctly produces no extraction. Check the specific email's own
  `total-packages`/`no-packages-today` template markers before assuming a miss. See
  `references/sender-filtering.md`.
- **Any sender-exclusion filter must never be able to exclude USPS Informed Delivery**, by design
  and structurally (exact domain match only, never suffix/substring — a suffix match on a
  plausible entry like `usps.com` would also match the real USPS Informed Delivery sender
  domain). No YAML file, no code-baked default exclusion list — this is a user-managed
  options-flow list by explicit user choice. This shipped in quick task 260807-qw1 (2026-08-07),
  so these rules are constraints on live code, not on a future build. See
  `references/sender-filtering.md`.
- **A shipped carrier parser with green unit tests is not evidence the carrier reaches
  parcelapp.net** — every production POST path re-gates through `validate_carrier_format`, and a
  shape that gate rejects fails silently and invisibly. See the DHL incident (2026-07-24 to
  2026-08-07) in `references/us-carrier-coverage.md`.
</requirements>

<findings_index>
## Feature Areas

| Area | Reference | Key Finding |
|------|-----------|-------------|
| Email Parsing Diagnostics (Stage 1) | references/email-parsing-diagnostics.md | `<p>`-only HTML scoping was Stage-1's #1 real-world failure mode, AND directly amplified Stage-2 hallucination exposure — not just a recall problem. The split-scope fix (broaden tracking-number sniffing only) is **already shipped** (`SC-3` in `email_parser.py`), confirmed zero regressions/false positives. A suspected real-world FedEx false-positive (spike 014) turned out to be a real, checksum-valid, correctly-extracted tracking number. **Spike 019: the real digest/carrier-direct corpus never reaches this fix's code path at all** — a separate carrier registry resolves it first; also surfaced a latent, currently-masked false positive. |
| Stage-2 LLM Order-Name Extraction | references/stage2-llm-order-extraction.md | The order-name/summary LLM enrichment feature already exists and works most of the time on real data — but the model fabricates a plausible-sounding brand+product for specific prompt shapes, and the rate is genuinely non-deterministic (not the fixed reproducible behavior first assumed). `order_summary`, not `order_name`, carries the real risk. Real USPS Informed Delivery digest emails (spike 016) surfaced a genuine, 100%-reproducible false-negative — real sender text the model won't use even though the prompt permits it — that three independent fixes (rendering, few-shot, explicit instruction) all failed to resolve (spikes 017-018), reconfirmed under the real shipped pipeline in spike 020. |
| MRG-05 Grounding Gate | references/mrg-05-grounding-gate.md | **Already shipped in production** (confirmed by reading `merge.py`/`coordinator.py`, spike 020) — not a design proposal. Validated across 40+ live-model samples plus a 35-sample real+synthetic end-to-end run with zero false passes/rejects on every failure mode tested. Includes the structural fix for its one confirmed blind spot. **Known gap (spike 018): does not catch cross-shipment misattribution in multi-package digest emails** — verifies text presence, not per-shipment attribution — still unresolved. |
| USPS Digest Multi-Shipment | references/usps-digest-multi-shipment.md | Multi-shipment extraction (`extra_shipments`) was already fully wired end-to-end before this round — confirmed for the first time (spike 022). Real gap: the Stage-2 LLM never surfaces per-package sender text (0/5), but a validated deterministic Stage-1 structural fix (reading USPS's own template ids) closes it 5/5 with zero merge-layer changes (spike 023). Spike 023's structural fix SHIPPED in Phase 36 (2026-07-24) as `_extract_usps_shippers`, confirmed 2026-08-07. |
| US Carrier Coverage | references/us-carrier-coverage.md | Scanned a real, independent MIT-licensed fixture corpus (spike 021): FedEx/USPS/UPS hold up well; Amazon's "0/9 miss" is a product-scope question (no tracking number in the emails, no parcelapp carrier code), not a parser bug; spike 024's DHL candidate shipped in Phase 36 (2026-07-24) mirroring the existing carrier-registry pattern exactly, was then found non-functional at every pre-POST gate, and was unblocked by quick task 260807-tpu's carrier-aware gate change on 2026-08-07. |
| Sender/Subject Filtering | references/sender-filtering.md | Two live-flagged USPS Informed Delivery "misses" (spike 025) confirmed to be genuinely empty digest days, not bugs, via USPS's own template markers. Real enriched-log evidence (spike 026, no new fetch needed) classified 14 real sender domains into a clean reliable-vs-noise split. A user-configurable exclusion filter (spike 027, options-flow UI, exact-match-only) validated at 100% against the real corpus — zero false exclusions, USPS always protected; spike 027's filter shipped in quick task 260807-qw1 (2026-08-07). |

## Source Files

Original spike source files are preserved in `sources/` for complete reference, including the
synthetic email corpus used to reproduce and isolate the hallucination
(`sources/003-stage2-extraction-quality/corpus/`), real fetched production emails — a FedEx
Delivery Manager notification and two USPS Informed Delivery digests
(`sources/014-tracking-fp-root-cause/corpus/`) — the full-pipeline integration harness that
confirmed both fixes are live in production (`sources/020-full-pipeline-integration/`), the
per-sibling digest verification and structural-sender-extraction candidates
(`sources/022-digest-same-blob-stage2-risk/`, `sources/023-structural-sender-extraction/`), the
US-carrier corpus scan plus DHL candidate — including the real MIT-licensed fixture corpus itself
(`sources/021-moralmunky-corpus-coverage/corpus/`, `sources/024-moralmunky-gap-fix-prototype/`),
two more real USPS Informed Delivery digests confirming genuinely-empty-day behavior
(`sources/025-usps-digest-ground-truth/corpus/`), the live activity-log corpus dump used for
sender classification (`sources/026-passed-filter-corpus-classification/corpus/`), and the
validated sender-exclusion matcher + its corpus validation script
(`sources/027-yaml-configurable-sender-exclusion/`).
</findings_index>

<metadata>
## Processed Spikes

- 001-email-parse-tracer
- 002-corpus-scanner
- 003-stage2-extraction-quality
- 004-prompt-context-enrichment
- 005-live-routing-confirmation
- 006-stage1-scoping-vs-stage2-exposure
- 007-grounding-gate-verification
- 008-generic-sender-label-denylist
- 009-stage1-scoping-fix
- 010-repeated-sampling-hallucination-rate
- 011-grounding-gate-merge-integration
- 012-sender-subject-exclusion-gate
- 013-conservative-stage1-broadening
- 014-tracking-fp-root-cause
- 016-description-real-email-diagnosis
- 017-rendered-vs-flat-prose-extraction
- 018-fewshot-prompt-fix
- 019-digest-emails-stage1-fix
- 020-full-pipeline-integration
- 021-moralmunky-corpus-coverage
- 022-digest-same-blob-stage2-risk
- 023-structural-sender-extraction
- 024-moralmunky-gap-fix-prototype
- 025-usps-digest-ground-truth
- 026-passed-filter-corpus-classification
- 027-yaml-configurable-sender-exclusion
</metadata>
