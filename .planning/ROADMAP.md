# Roadmap: Shop2Parcel

## Milestones

- ✅ **v1.0 MVP** — Phases 1–9 (shipped 2026-05-04) — archived at [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)
- ✅ **v1.1 Debug-Ready** — Phases 10–12 (shipped 2026-05-17) — archived at [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)
- ✅ **v1.2 Debug Switch** — Phases 13–14 (shipped 2026-06-05) — archived at [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)
- ✅ **v1.3 AI-based Email Analysis** — Phases 15–22 (shipped 2026-06-17) — archived at [.planning/milestones/v1.3-ROADMAP.md](./milestones/v1.3-ROADMAP.md)
- 📋 **v1.4** — Planned
- 📋 **v1.5 Shared Pools & IMAP Parity** — Phases 29–34 (in progress)

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

### 📋 v1.5 Shared Pools & IMAP Parity (Phases 29–34)

- [x] **Phase 29: Hub Skeleton + Foundational Safety** - Establish the Shop2ParcelHub singleton lifecycle, constructor-race lock, and fix the IMAP _first_refresh_done gap before any shared state moves (completed 2026-07-09)
- [ ] **Phase 30: Shared Dedup** - Move the submitted-tracking-number set to a global hub-owned store; migrate per-account sets with union-merge; no double-POSTs across accounts
- [ ] **Phase 31: Shared Budget** - Move the parcelapp 20/day budget to the hub; single FCFS try_consume(); single midnight timer; migrate per-account quota state conservatively
- [ ] **Phase 32: Shared Stage-2 Queue + Worker** - Move the asyncio queue and long-lived Ollama worker to the hub; per-account job routing via callback; per-account enqueue cap
- [ ] **Phase 33: IMAP Parity** - Port the inline Ollama fallback gatekeeper to IMAP; extract shared base-class guard; cross-parity tests assert Gmail↔IMAP behave identically
- [ ] **Phase 34: Multi-Account Lifecycle Tests + Global Sensors** - Lifecycle test suite (add/remove sequences, last-account teardown); global quota + queue-depth sensors; consolidated failure notification

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

### Phase 29: Hub Skeleton + Foundational Safety

**Goal**: The Shop2ParcelHub lifecycle contract exists and is provably race-free; every existing coordinator attaches to it at setup and detaches at removal; the IMAP _first_refresh_done gap is closed before any shared-state work begins
**Depends on**: Phase 23 (v1.3/v1.4 codebase)
**Requirements**: LIFE-01, LIFE-02, LIFE-03, LIFE-04, LIFE-05, PAR-02
**Success Criteria** (what must be TRUE):

  1. With two accounts setting up concurrently, HA logs show "shared hub created" exactly once — the constructor-race lock (asyncio.Lock installed synchronously before any await) prevents duplicate hub construction
  2. Removing a single account leaves the hub attached and all remaining accounts continue polling — refcount drops to (N-1), not zero, and no teardown fires
  3. Removing the last account logs hub teardown and the hass.data["__shared__"] key is absent — refcount reaching zero triggers full hub shutdown
  4. ImapCoordinator sets _first_refresh_done = True on its first successful poll — confirmed by a test asserting the flag is True after one successful _async_update_data call
  5. The shared worker task stub is registered via hass.async_create_background_task (not entry-scoped) — its task handle survives removal of the entry that created it

**Plans**: 2/2 plans complete

Plans:

- [x] 29-01-PLAN.md — Shop2ParcelHub singleton lifecycle (lock-guarded creation, refcount attach/detach, worker stub, shared store v1) + __init__.py wiring (LIFE-01..05)
- [x] 29-02-PLAN.md — IMAP _first_refresh_done first-refresh parity fix (PAR-02)

---

### Phase 30: Shared Dedup

**Goal**: All accounts draw from one global submitted-tracking-number set; a tracking number posted by any account is never re-submitted by another; the shared set persists across HA restart; existing per-account sets are migrated without loss
**Depends on**: Phase 29
**Requirements**: DEDUP-01, DEDUP-02, DEDUP-03
**Success Criteria** (what must be TRUE):

  1. When account A submits tracking number "1Z999", account B's poll skips that TN as already-submitted — hub.is_submitted() returns True for both accounts
  2. After an HA restart, the shared dedup set loads from the shop2parcel.__shared__ store (SHARED_STORAGE_VERSION=1, independent of per-entry STORAGE_VERSION=3) and the first poll does not re-submit any TN from the previous session
  3. A fresh install running the migration finds per-account submitted_tracking_numbers union-merged into the shared set, capped at 1000 LRU, with no TN appearing more than once — verified by a migration test asserting set cardinality and membership

**Plans**: TBD

---

### Phase 31: Shared Budget

**Goal**: All accounts draw from one shared parcelapp 20/day budget on a first-come-first-served basis; the budget state persists across restart; one hub timer resets it at the correct day boundary; migration from per-account quota state does not double-count
**Depends on**: Phase 30
**Requirements**: QUOTA-01, QUOTA-02, QUOTA-03, QUOTA-04, QUOTA-05
**Success Criteria** (what must be TRUE):

  1. After 20 successful POSTs from any mix of accounts, every subsequent enqueue attempt returns False from hub.try_consume() — no account can exceed the 20/day shared limit; try_consume() is a synchronous method with no await between check and increment
  2. The MAX_STAGE2_POSTS_PER_POLL cap is a single hub counter that resets per-poll — 10 accounts polling simultaneously cannot collectively exceed 5 stage-2 POSTs per hub poll cycle (not 10×5=50)
  3. After an HA restart, the budget counter and quota_exhausted_until timestamp reload from the shared store and the day's already-used quota is not reset to zero incorrectly
  4. One (and only one) midnight timer fires on the hub — confirmed by a test asserting a single timer callback registration after two accounts attach; no per-account timer storm
  5. Migration from per-account stores initializes used_today=0 on the migration day and takes the maximum quota_exhausted_until across all per-account stores — no account starts the day with an inflated budget

**Plans**: TBD

---

### Phase 32: Shared Stage-2 Queue + Worker

**Goal**: All accounts share one asyncio queue and one long-lived Ollama worker; each job's result routes back to the originating account's coordinator; one busy account cannot starve the queue; the worker survives single-account removal
**Depends on**: Phase 31
**Requirements**: WORK-01, WORK-02, WORK-03, WORK-04
**Success Criteria** (what must be TRUE):

  1. Two accounts each enqueue Stage-2 jobs; the single hub worker processes them FIFO and delivers each result to the correct account's _on_stage2_result callback — shipments appear under the right account's sensors, not the wrong one's
  2. When one account's coordinator is removed mid-queue, the worker continues processing remaining jobs for other accounts with no "queue is None" error and no dropped jobs for the remaining accounts
  3. A per-account enqueue cap (named constant) prevents one account from filling the shared queue — a second account can always enqueue at least one job even if the first account is at its individual cap
  4. The worker task is registered via hass.async_create_background_task — removing any single account's config entry does not cancel the task; it remains running until hub.async_shutdown() is called on last-account removal

**Plans**: TBD

---

### Phase 33: IMAP Parity

**Goal**: IMAP accounts run the same inline Ollama fallback path as Gmail accounts; the shared base-class guard enforces identical first-refresh skip and wall-clock budget for both coordinator types; cross-parity tests prove identical behavior for both
**Depends on**: Phase 32
**Requirements**: PAR-01, PAR-03, PAR-04
**Success Criteria** (what must be TRUE):

  1. On a Stage-1 miss with stage2_enabled, an IMAP account enqueues a Stage-2 job via the shared hub queue — confirmed by a test asserting hub.enqueue() is called for the IMAP path, matching the Gmail path under the same conditions
  2. On a Stage-1 miss with stage2_enabled, the IMAP coordinator does NOT mark the message as seen — the message remains re-inspectable on the next poll, matching Gmail's behavior
  3. The inline-fallback guard (_inline_fallback_allowed()) lives in the base class and is called identically by both GmailCoordinator and ImapCoordinator — a cross-parity test runs the same email scenario against both coordinator types and asserts identical enqueue outcomes
  4. First-refresh skip is active for IMAP: a Stage-1 miss on the very first poll does not trigger inline extraction — confirmed by a test asserting no hub.enqueue() call during first-refresh for an IMAP coordinator

**Plans**: TBD

---

### Phase 34: Multi-Account Lifecycle Tests + Global Sensors

**Goal**: The full multi-account add/remove lifecycle is verified by automated tests covering all edge cases; global hub sensors expose shared quota and queue depth to HA; hub-level consolidated failure notification replaces per-account duplicates
**Depends on**: Phase 33
**Requirements**: LIFE-01, DIAG-01, DIAG-02, DIAG-03
**Success Criteria** (what must be TRUE):

  1. A test exercising add→add→remove→add sequence for mixed Gmail/IMAP accounts passes: after each step, the remaining accounts have the correct refcount, the hub is intact, and sensors report correct per-account state
  2. A "quota remaining today" HA sensor (global) updates after each POST and reflects 20 minus hub._used_today — its state remains accurate across account add/remove events without resetting
  3. A "Stage-2 queue depth" HA sensor (global) reflects the current hub queue size — it increments when jobs are enqueued and decrements as the worker drains them
  4. After N consecutive Stage-2 failures, exactly one persistent HA notification fires from the hub — not N near-identical per-account notifications; adding or removing an account does not spawn additional duplicate notifications

**Plans**: TBD

---

## Coverage Map

| Requirement | Phase |
|-------------|-------|
| PAR-01 | Phase 33 |
| PAR-02 | Phase 29 |
| PAR-03 | Phase 33 |
| PAR-04 | Phase 33 |
| DEDUP-01 | Phase 30 |
| DEDUP-02 | Phase 30 |
| DEDUP-03 | Phase 30 |
| QUOTA-01 | Phase 31 |
| QUOTA-02 | Phase 31 |
| QUOTA-03 | Phase 31 |
| QUOTA-04 | Phase 31 |
| QUOTA-05 | Phase 31 |
| WORK-01 | Phase 32 |
| WORK-02 | Phase 32 |
| WORK-03 | Phase 32 |
| WORK-04 | Phase 32 |
| LIFE-01 | Phase 29 (skeleton) + Phase 34 (tests) |
| LIFE-02 | Phase 29 |
| LIFE-03 | Phase 29 |
| LIFE-04 | Phase 29 |
| LIFE-05 | Phase 29 |
| DIAG-01 | Phase 34 |
| DIAG-02 | Phase 34 |
| DIAG-03 | Phase 34 |

**Coverage: 24/24 v1.5 requirements mapped.**

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
| 29. Hub Skeleton + Foundational Safety | v1.5 | 2/2 | Complete    | 2026-07-09 |
| 30. Shared Dedup | v1.5 | 0/? | Not started | - |
| 31. Shared Budget | v1.5 | 0/? | Not started | - |
| 32. Shared Stage-2 Queue + Worker | v1.5 | 0/? | Not started | - |
| 33. IMAP Parity | v1.5 | 0/? | Not started | - |
| 34. Multi-Account Lifecycle Tests + Global Sensors | v1.5 | 0/? | Not started | - |

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
*Roadmap last updated: 2026-07-03 — v1.5 Shared Pools & IMAP Parity added (Phases 29–34)*
*Phase numbering is continuous across all milestones; last v1.3/v1.4 phase: 28*
