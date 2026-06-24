# Roadmap: Shop2Parcel

## Milestones

- ✅ **v1.0 MVP** — Phases 1–9 (shipped 2026-05-04) — archived at [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)
- ✅ **v1.1 Debug-Ready** — Phases 10–12 (shipped 2026-05-17) — archived at [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)
- ✅ **v1.2 Debug Switch** — Phases 13–14 (shipped 2026-06-05) — archived at [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)
- ✅ **v1.3 AI-based Email Analysis** — Phases 15–22 (shipped 2026-06-17) — archived at [.planning/milestones/v1.3-ROADMAP.md](./milestones/v1.3-ROADMAP.md)
- 📋 **v1.4** — Planned

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

### 📋 v1.4 (Planned)

- [x] Phase 23: Decouple Stage-2 LLM Extraction from parcelapp POST + honor debug-mode POST suppression — extraction runs independent of quota/debug; POST gated separately (active; bug-fix-driven) (completed 2026-06-24)
- [ ] Phase 24: Custom Extraction Field Persistence — STORAGE_VERSION 3→4 migration for `custom_attributes` write-back
- [ ] Phase 25: Stage-2 Observability — inference-latency rolling-average sensor, per-category failure breakdown
- [ ] Phase 26+: TBD

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
