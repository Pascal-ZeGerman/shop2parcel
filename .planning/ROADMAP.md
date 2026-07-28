# Roadmap: Shop2Parcel

## Milestones

- ✅ **v1.0 MVP** — Phases 1–9 (shipped 2026-05-04) — archived at [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)
- ✅ **v1.1 Debug-Ready** — Phases 10–12 (shipped 2026-05-17) — archived at [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)
- ✅ **v1.2 Debug Switch** — Phases 13–14 (shipped 2026-06-05) — archived at [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)
- ✅ **v1.3 AI-based Email Analysis** — Phases 15–22 (shipped 2026-06-17) — archived at [.planning/milestones/v1.3-ROADMAP.md](./milestones/v1.3-ROADMAP.md)
- 📋 **v1.4** — Planned
- ✅ **v1.5 Shared Pools & IMAP Parity** — Phases 29–34 (shipped 2026-07-20) — archived at [.planning/milestones/v1.5-ROADMAP.md](./milestones/v1.5-ROADMAP.md)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1–9) — SHIPPED 2026-05-04</summary>

- [x] Phase 1: Project Setup (1/1 plan) — completed 2026-04-xx
- [x] Phase 2: API Clients (3/3 plans) — completed 2026-04-xx
- [x] Phase 3: Config Flow (3/3 plans) — completed 2026-04-xx
- [x] Phase 4: Coordinator + Polling (3/3 plans) — completed 2026-04-xx
- [x] Phase 5: Sensor Entities (2/2 plans) — completed 2026-04-xx
- [x] Phase 6: CI + HACS Packaging (4/4 plans) — completed 2026-05-04
- [x] Phase 7: Diagnostic Tooling (3/3 plans) — completed 2026-05-xx
- [x] Phase 8: Parser Template Expansion (3/3 plans) — completed 2026-05-xx
- [x] Phase 9: IMAP Support + Multi-Account (5/5 plans) — completed 2026-05-04

See: [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)

</details>

<details>
<summary>✅ v1.1 Debug-Ready (Phases 10–12) — SHIPPED 2026-05-17</summary>

- [x] Phase 10: Full-Window Scanning + Tracking Dedup (3/3 plans) — completed 2026-05-xx
- [x] Phase 11: Activity Log + Debug Logging (3/3 plans) — completed 2026-05-xx
- [x] Phase 12: Tech Debt (3/3 plans) — completed 2026-05-17

See: [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)

</details>

<details>
<summary>✅ v1.2 Debug Switch (Phases 13–14) — SHIPPED 2026-06-05</summary>

- [x] Phase 13: Dedup Store Fix (3/3 plans) — completed 2026-05-xx
- [x] Phase 13.1: Sensor Restore on Restart (3/3 plans) — completed 2026-05-xx
- [x] Phase 14: Debug Dry-Run Mode (3/3 plans) — completed 2026-06-05

See: [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)

</details>

<details>
<summary>✅ v1.3 AI-based Email Analysis (Phases 15–22) — SHIPPED 2026-06-17</summary>

- [x] Phase 15: OllamaClient Foundation (4/4 plans) — completed 2026-06-06
- [x] Phase 16: OllamaExtractor + Schema Composition (3/3 plans) — completed 2026-06-09
- [x] Phase 17: Config-Flow Expansion (4/4 plans) — completed 2026-06-11
- [x] Phase 18: Queue Plumbing (2/2 plans) — completed 2026-06-12
- [x] Phase 19: Worker Spawn + Poll Loop Flip (2/2 plans) — completed 2026-06-12
- [x] Phase 20: Merge + Quota Guards (3/3 plans) — completed 2026-06-15
- [x] Phase 21: Failure Surface + Diagnostics (3/3 plans) — completed 2026-06-17
- [x] Phase 22: README Setup + End-to-End Validation (2/2 plans) — completed 2026-06-16

See: [.planning/milestones/v1.3-ROADMAP.md](./milestones/v1.3-ROADMAP.md)

</details>

### 📋 v1.4 (In progress — phases 24/25 deferred, 26–28 shipped)

- [x] Phase 23: Decouple Stage-2 LLM Extraction from parcelapp POST + honor debug-mode POST suppression — extraction runs independent of quota/debug; POST gated separately (completed 2026-06-24)
- ~~Phase 24: Custom Extraction Field Persistence~~ — deferred; no directory, superseded by v1.5 scope
- ~~Phase 25: Stage-2 Observability~~ — deferred; observability absorbed into Phase 26 operational-health rework
- [x] Phase 26: Operational Health Sensor Rework — replace one-way per-shipment sensors with live coordinator-owned health counters (total_forwarded, used_today, quota sentinel); additive store keys; Gmail+IMAP parity (completed 2026-06-24)
- [x] Phase 27: Subject-Only Gmail Filter + Hybrid Ollama Gatekeeper — subject-line pre-filter, inline Ollama fallback gatekeeper with volume guards, first-refresh skip, wall-clock budget (completed 2026-06-30)
- [x] Phase 28: Carrier Format Pre-POST Validation + Full-Body Gmail Scan — validate_carrier_format() gate pre-POST, DHL short-number branch removal, full-body fallback for wide-net Gmail scan (completed 2026-06-30)

<details>
<summary>✅ v1.5 Shared Pools & IMAP Parity (Phases 29–34) — SHIPPED 2026-07-20</summary>

- [x] Phase 29: Hub Skeleton + Foundational Safety (2/2 plans) — completed 2026-07-09
- [x] Phase 30: Shared Dedup (3/3 plans) — completed 2026-07-15
- [x] Phase 31: Shared Budget (5/5 plans) — completed 2026-07-17
- [x] Phase 32: Shared Stage-2 Queue + Worker (5/5 plans) — completed 2026-07-20
- [x] Phase 33: IMAP Parity (5/5 plans) — completed 2026-07-20
- [x] Phase 34: Multi-Account Lifecycle Tests + Global Sensors (6/6 plans) — completed 2026-07-20

See: [.planning/milestones/v1.5-ROADMAP.md](./milestones/v1.5-ROADMAP.md)

</details>

### Independent Phases (not tied to a milestone's requirement set)

- [x] **Phase 35: MRG-05 Grounding Gate + Stage-1 Scoping Fix** - Add the validated grounding gate (MRG-05) for Stage-2 `order_name`/`order_summary` in `merge.py`, with structural sender/subject exclusion; broaden Stage-1 tracking-number recall conservatively in `email_parser.py`. From spike findings, not part of v1.5 scope. (completed 2026-07-17)
- [x] **Phase 36: DHL Carrier Support + USPS Digest Sender Extraction** - Validated `_detect_dhl`/`_parse_dhl` pair + structural per-package sender extraction for USPS digests, from spike findings 021-024. (completed 2026-07-24)

## Phase Details

### Phase 23: Decouple Stage-2 LLM extraction from parcelapp POST and honor debug-mode POST suppression

**Goal:** Stage-2 LLM extraction runs for every dequeued job independent of parcelapp quota or debug state; the parcelapp POST is gated separately (debug dry-run suppresses it, quota-deferred items persist post-pending and drain without re-extracting), and the quota-skip WARNING flood is throttled — fixing the production defect where a debug "dry run" made real POSTs and a full quota silenced Ollama (0 parses / 4,565 WARNINGs).
**Requirements**: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 (see 23-CONTEXT.md)
**Depends on:** Phase 22
**Plans:** 4/4 plans complete

Plans:

- [x] 23-01-PLAN.md — Wave 0: invert the quota-gate test + add failing AC-1..AC-5/AC-8 test scaffolds
- [x] 23-02-PLAN.md — Wave 1: add _pending_posts + quota-WARNING throttle flag; persist/hydrate pending_posts store key (no version bump)
- [x] 23-03-PLAN.md — Wave 2: move quota guard after extractor; debug dry-run early return; quota-defer persistence + throttled WARNING
- [x] 23-04-PLAN.md — Wave 3: _async_drain_pending_posts + worker-loop wiring + IMAP parity; full-suite/lint/type gate

---

### Phase 26: Operational Health Sensor Rework

**Goal:** Replace one-way per-shipment sensors with coordinator-owned operational-health counters (total_forwarded, last_forwarded_ts, used_today, quota_is_exhausted, currently_tracked_count); add UTC-date rollover guard; additive store keys with ASVS V5 type guards; Gmail+IMAP parity at all 4 POST-success sites.
**Depends on:** Phase 23
**Plans:** 3/3 plans complete

Plans:

- [x] 26-01-PLAN.md — Coordinator counter properties + _record_forward() helper; all 4 POST-success sites wired
- [x] 26-02-PLAN.md — Sensor entity rework replacing one-way attributes with coordinator-backed properties
- [x] 26-03-PLAN.md — Quota sentinel sensor + store persistence + store migration coverage

---

### Phase 27: Subject-Only Gmail Filter + Hybrid Ollama Gatekeeper

**Goal:** Fix "0 messages every poll" production defect and make the email→tracking pipeline forward genuine shipments while rejecting non-shipment mail. Implements subject-line pre-filter (configurable default), inline Ollama fallback gatekeeper with volume guards (per-poll cap, LRU cache), first-refresh skip flag, and wall-clock budget deadline.
**Depends on:** Phase 26
**Plans:** 3/3 plans complete

Plans:

- [x] 27-01-PLAN.md — Subject-only default Gmail query + volume-guard constants + options flow strings
- [x] 27-02-PLAN.md — Inline Ollama fallback gatekeeper + LRU cache + per-poll cap wired in GmailCoordinator
- [x] 27-03-PLAN.md — First-refresh skip (_first_refresh_done flag) + wall-clock budget; IMAP parity + cross-type tests

---

### Phase 28: Carrier Format Pre-POST Validation + Full-Body Gmail Scan

**Goal:** Close two safety gaps Phase 27 opened when it widened the email net: validate_carrier_format() gate pre-POST rejects obviously-wrong tracking strings; DHL short-number branch removed from _looks_like_tracking; full-body fallback ensures wide-net Gmail scan still extracts tracking numbers from message body when subject-only hit doesn't carry a number. Adds CarrierFormatRejectionsSensor (14th diagnostic entity).
**Depends on:** Phase 27
**Plans:** 5/5 plans complete

Plans:

- [x] 28-01-PLAN.md — validate_carrier_format() + DHL branch removal (TDD)
- [x] 28-02-PLAN.md — Pre-POST gate wired in coordinator drain loop + quota path
- [x] 28-03-PLAN.md — PollStats.carrier_format_rejected_total counter + record_carrier_format_rejection()
- [x] 28-04-PLAN.md — Full-body Gmail fallback scan when subject-only hit yields no tracking number
- [x] 28-05-PLAN.md — CarrierFormatRejectionsSensor diagnostic entity (R4, D-09)

---

### Phase 35: MRG-05 Grounding Gate + Stage-1 Scoping Fix

**Goal**: A grounding/verification gate (MRG-05) rejects fabricated Stage-2 `order_name`/`order_summary` values before they reach parcelapp — validated to catch pure fabrication, platform-name conflation, and generic-sender-label parroting with zero false rejections across 40+ live-model samples; Stage-1's tracking-number recall is broadened to `<div>`/`<span>` without introducing the confirmed footer-boilerplate false positive
**Depends on**: None — independent of the v1.5 hub work; touches `merge.py` and `api/email_parser.py` only, not `hub.py` or coordinator files
**Requirements**: TBD (new scope from spike findings, not part of original v1.5 requirements)
**Success Criteria** (what must be TRUE):

  1. A Stage-2 `order_name`/`order_summary` value on a Stage-1-blank field is discarded (falls back to the Stage-1 sentinel) when it has no grounded content token in the body-only prose that was fed to the model — matching MRG-04's discard-not-conflict semantics
  2. The gate never treats sender/subject header tokens as grounding evidence, even when the Stage-2 prompt is enriched with envelope context — closing the confirmed "Customer Care"-style generic-label blind spot structurally, not via an enumerated list
  3. `_parse_html_template`'s tracking-number shape-sniffing scans `<div>`/`<span>` in addition to `<p>`/`<td>`, while `order_name`/`carrier_name` label regexes remain `<p>`/`<td>`-scoped — recovering the `custom_theme_div_only.html`-style real fixture with zero regressions on the existing fixture corpus
  4. The seed test corpus from `.claude/skills/spike-findings-shop2parcel/sources/007-grounding-gate-verification/`, `010-repeated-sampling-hallucination-rate/`, `011-grounding-gate-merge-integration/`, and `012-sender-subject-exclusion-gate/` is captured as real unit tests, not left as spike-only scripts

**Plans**: 4/4 plans complete

**Wave 1** *(parallel — disjoint files)*

- [x] 35-01-PLAN.md — MRG-05 gate in merge.py: validate_grounding / merge_llm_authoritative_with_grounding (4-tuple) + unit tests [tdd] [SC-1, SC-2]
- [x] 35-02-PLAN.md — Stage-1 split-scope fix in email_parser.py: LABEL_TAGS/TRACKING_TAGS + div/span recall + Pitfall-4 test inversion [tdd] [SC-3]

**Wave 2** *(blocked on 35-01)*

- [x] 35-03-PLAN.md — coordinator worker wiring: PollStats.grounding_rejected_total counter + prose recompute + grounding wrapper (merge path) [SC-1]

**Wave 3** *(blocked on 35-01/35-02/35-03)*

- [x] 35-04-PLAN.md — gmail inline-fallback grounding gate (Pitfall-2 site) + phase gate (full suite + ruff + mypy; SC-4 corpus capture) [SC-1, SC-4]

---

## Coverage Map

v1.5 requirement coverage (24/24 mapped) archived at [.planning/milestones/v1.5-REQUIREMENTS.md](./milestones/v1.5-REQUIREMENTS.md). Requirements for the next milestone are defined via `/gsd-new-milestone`.

---

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1–9 (collapsed) | v1.0 | 29/29 | Complete | 2026-05-04 |
| 10–12 (collapsed) | v1.1 | 9/9 | Complete | 2026-05-17 |
| 13, 13.1, 14 (collapsed) | v1.2 | 9/9 | Complete | 2026-06-05 |
| 15. OllamaClient Foundation | v1.3 | 4/4 | Complete | 2026-06-06 |
| 16. OllamaExtractor + Schema Composition | v1.3 | 3/3 | Complete | 2026-06-09 |
| 17. Config-Flow Expansion | v1.3 | 4/4 | Complete | 2026-06-11 |
| 18. Queue Plumbing (transitional) | v1.3 | 2/2 | Complete | 2026-06-12 |
| 19. Worker Spawn + Poll Loop Flip | v1.3 | 2/2 | Complete | 2026-06-12 |
| 20. Merge + Quota Guards (CRITICAL) | v1.3 | 3/3 | Complete | 2026-06-15 |
| 21. Failure Surface + Diagnostics | v1.3 | 3/3 | Complete | 2026-06-17 |
| 22. README Setup + End-to-End Validation | v1.3 | 2/2 | Complete | 2026-06-16 |
| 23. Decouple Stage-2 from POST | v1.4 | 4/4 | Complete | 2026-06-24 |
| 26. Operational Health Sensor Rework | v1.4 | 3/3 | Complete | 2026-06-24 |
| 27. Subject-Only Gmail Filter + Hybrid Ollama Gatekeeper | v1.4 | 3/3 | Complete | 2026-06-30 |
| 28. Carrier Format Pre-POST Validation + Full-Body Gmail Scan | v1.4 | 5/5 | Complete | 2026-06-30 |
| 29–34 (collapsed) | v1.5 | 26/26 | Complete | 2026-07-20 |
| 35. MRG-05 Grounding Gate + Stage-1 Scoping Fix | (independent) | 4/4 | Complete | 2026-07-17 |
| 36. DHL Carrier Support + USPS Digest Sender Extraction | (independent) | 2/2 | Complete | 2026-07-24 |

---

## Key Design Constraints for v1.5 (Pitfall Mitigations)

| Pitfall | Phase | Mitigation |
|---------|-------|------------|
| Singleton constructor race | 29 | asyncio.Lock created synchronously (before first await) in hass.data[DOMAIN]; hub construction guarded behind it |
| Shared worker torn down on single-entry unload | 29, 32 | hass.async_create_background_task (not entry-scoped); reference-counted hub.attach/detach; teardown only when _ref_count == 0 |
| Non-atomic FCFS budget decrement | 31 | try_consume() is a synchronous method with no await between check and increment; lock-free by design (single worker serializes all POSTs) |
| _first_refresh_done never set in IMAP | 29 | One-line fix added to ImapCoordinator success path in Phase 29, before any parity or fallback work |
| Store migration quota double-counting | 30, 31 | used_today=0 on migration day; max() across all quota_exhausted_until values; union-merge TNs capped at 1000 |

---
*Roadmap last updated: 2026-07-28 — v1.5 Shared Pools & IMAP Parity archived (shipped 2026-07-20); Phases 35 and 36 shipped as independent, milestone-agnostic phases.*
*Phase numbering is continuous across all milestones; last v1.3/v1.4 phase: 28*
