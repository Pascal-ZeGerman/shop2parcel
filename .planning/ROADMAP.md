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
- [x] **Phase 30: Shared Dedup** - Move the submitted-tracking-number set to a global hub-owned store; migrate per-account sets with union-merge; no double-POSTs across accounts (completed 2026-07-15)
- [x] **Phase 31: Shared Budget** - Move the parcelapp 20/day budget to the hub; single FCFS try_consume(); single midnight timer; migrate per-account quota state conservatively (completed 2026-07-17)
- [x] **Phase 32: Shared Stage-2 Queue + Worker** - Move the asyncio queue and long-lived Ollama worker to the hub; per-account job routing via callback; per-account enqueue cap (completed 2026-07-20)
- [x] **Phase 33: IMAP Parity** - Port the inline Ollama fallback gatekeeper to IMAP; extract shared base-class guard; cross-parity tests assert Gmail↔IMAP behave identically (completed 2026-07-20)
- [x] **Phase 34: Multi-Account Lifecycle Tests + Global Sensors** - Lifecycle test suite (add/remove sequences, last-account teardown); global quota + queue-depth sensors; consolidated failure notification (completed 2026-07-20)
- [x] **Phase 35: MRG-05 Grounding Gate + Stage-1 Scoping Fix** - Add the validated grounding gate (MRG-05) for Stage-2 `order_name`/`order_summary` in `merge.py`, with structural sender/subject exclusion; broaden Stage-1 tracking-number recall conservatively in `email_parser.py`. Independent of the v1.5 hub work — added from spike findings, not part of the original v1.5 scope. (completed 2026-07-17)

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

**Plans**: 3/3 plans complete
**Wave 1**

- [x] 30-01-PLAN.md — Hub dedup core: check_and_mark / is_submitted / submitted_count / seed_from_list (FIFO cap) [DEDUP-01, DEDUP-03]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 30-02-PLAN.md — Hub persistence: async_load / async_save to shop2parcel.__shared__; restart round-trip [DEDUP-02]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 30-03-PLAN.md — Coordinator wiring + one-time migration + full test migration; cross-account/migration/restart integration tests [DEDUP-01, DEDUP-02, DEDUP-03]

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

**Plans**: 5/5 plans complete

**Wave 1**

- [x] 31-01-PLAN.md — Hub shared-budget sync mutators (try_consume/refund/record_quota_exhausted/poll-cap) + HUB_STAGE2_POLL_WINDOW [tdd] [QUOTA-01, QUOTA-02, QUOTA-04]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 31-02-PLAN.md — Hub additive quota persistence + corrupt guards + conservative migration seed [tdd] [QUOTA-03, QUOTA-05]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 31-03-PLAN.md — Three hub timers (midnight / quota-expiry / 30-min poll-window), no per-account storm [tdd] [QUOTA-03, QUOTA-04]

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 31-04-PLAN.md — Coordinator hub-delegation + Stage-2 drain/worker POST rewire + migrate-then-drop [QUOTA-01, QUOTA-02, QUOTA-03, QUOTA-05]
- [x] 31-05-PLAN.md — Gmail+IMAP inline POST rewire (the 2 extra sites) + D-07 debug save gate + P1 test [QUOTA-02]

*Wave 4 runs 31-04 ∥ 31-05 in parallel — disjoint files (coordinator.py + __init__.py vs gmail_/imap_coordinator.py).*

---

### Phase 32: Shared Stage-2 Queue + Worker

**Goal**: All accounts share one asyncio queue and one long-lived Ollama worker; each job's result routes back to the originating account's coordinator; one busy account cannot starve the queue; the worker survives single-account removal
**Depends on**: Phase 31
**Requirements**: WORK-01, WORK-02, WORK-03, WORK-04
**Success Criteria** (what must be TRUE):

  1. Two accounts each enqueue Stage-2 jobs; the single hub worker processes them FIFO and delivers each result to the correct account's coordinator (resolved by entry_id — supersedes the original "_on_stage2_result callback" phrasing per 32-CONTEXT.md D-01) — shipments appear under the right account's sensors, not the wrong one's
  2. When one account's coordinator is removed mid-queue, the worker continues processing remaining jobs for other accounts with no "queue is None" error and no dropped jobs for the remaining accounts
  3. A per-account enqueue cap (named constant) prevents one account from filling the shared queue — a second account can always enqueue at least one job even if the first account is at its individual cap
  4. The worker task is registered via hass.async_create_background_task — removing any single account's config entry does not cancel the task; it remains running until hub.async_shutdown() is called on last-account removal

**Plans**: 5/5 plans complete

**Wave 1**

- [x] 32-01-PLAN.md — Foundation: HUB_STAGE2_QUEUE_MAXLEN(64)/STAGE2_PER_ACCOUNT_INFLIGHT_CAP(8) + EnqueueOutcome enum + Stage2Job.entry_id (required) + suite-green test ripple [WORK-02, WORK-03]

**Wave 2** *(blocked on Wave 1)*

- [x] 32-02-PLAN.md — Hub enqueue/dedup/cap + _release_inflight + inflight_count + entry_id→coordinator registry/purge [tdd] [WORK-01, WORK-02, WORK-03, WORK-04]

**Wave 3** *(blocked on Wave 2)*

- [x] 32-03-PLAN.md — Real hub worker replacing _stub_worker: resolve/drain/dispatch + crash-isolation ladder + purge-vs-drain-race backstop [tdd] [WORK-01, WORK-02, WORK-04 + crash isolation]

**Wave 4** *(blocked on Wave 3)*

- [x] 32-04-PLAN.md — Coordinator hot-path cutover: _enqueue_stage2→hub.enqueue + delete 11 in-body discards + repoint stage2_queue_depth sensor [WORK-01, WORK-02, WORK-03]

**Wave 5** *(blocked on Wave 4)*

- [x] 32-05-PLAN.md — Retire per-entry queue/worker/lifecycle + extractor-setup split + __init__ rewire + CONF_QUEUE_MAXLEN vestigial + multi-account integration/must-NOT coverage [WORK-01, WORK-04]

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

**Plans**: 5/5 plans complete
**Wave 1**

- [x] 33-01-PLAN.md — Gmail characterization baseline + multi-shipment-digest test (D-01 regression guard)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 33-02-PLAN.md — Extract shared Shop2ParcelCoordinator._run_inline_fallback() + 2 imports + wire Gmail call site (PAR-04)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 33-03-PLAN.md — Wire IMAP Stage-1-miss site + IMAP fallback branch-matrix tests (PAR-01, PAR-03)

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 33-04-PLAN.md — Cross-parity module tests/test_coordinator_parity.py + instance-isolation test (PAR-04)

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 33-05-PLAN.md — One-time ≥90% coverage measurement + fail_under ratchet (R6 / D-02)

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

**Plans**: 6/6 plans complete

**Wave 1**

- [x] 34-01-PLAN.md — Wave-0 R-01 entity-registry re-parent characterization + Phase 32 dependency preflight + hub notification constants (D-06) [DIAG-01, DIAG-02, DIAG-03]

**Wave 2** *(blocked on Wave 1)*

- [x] 34-02-PLAN.md — Hub read accessors (stage2_queue_depth/stage2_pending) + consolidated failure-streak notification (D-05..D-08, PROH-1) [tdd] [DIAG-02, DIAG-03]

**Wave 3** *(blocked on Wave 2; 34-03 ∥ 34-04 — disjoint files)*

- [x] 34-03-PLAN.md — Global sensor classes: GlobalQuotaSensor (sensor.py) + GlobalQueueSensor (diagnostic_sensor.py), hub device, additive (D-01/D-02/D-03/D-04) [tdd] [DIAG-01, DIAG-02]
- [x] 34-04-PLAN.md — Retire per-account Stage-2 notification + wire hub worker-outcome observation (D-05) [DIAG-03]

**Wave 4** *(blocked on 34-02 + 34-03)*

- [x] 34-05-PLAN.md — R-01 re-home mechanism: hub ownership/re-home + entity-registry teardown removal + sensor registration guard + unload hook [DIAG-01, DIAG-02, LIFE-01]

**Wave 5** *(blocked on 34-03/34-04/34-05)*

- [x] 34-06-PLAN.md — LIFE-01 lifecycle suite: add→add→remove→add mixed Gmail/IMAP + remove-to-zero→re-add teardown/recreate (D-09/D-10, PROH-2) [LIFE-01]

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
| 30. Shared Dedup | v1.5 | 3/3 | Complete    | 2026-07-15 |
| 31. Shared Budget | v1.5 | 5/5 | Complete    | 2026-07-17 |
| 32. Shared Stage-2 Queue + Worker | v1.5 | 5/5 | Complete   | 2026-07-20 |
| 33. IMAP Parity | v1.5 | 5/5 | Complete    | 2026-07-20 |
| 34. Multi-Account Lifecycle Tests + Global Sensors | v1.5 | 6/6 | Complete    | 2026-07-20 |
| 35. MRG-05 Grounding Gate + Stage-1 Scoping Fix | v1.5 | 4/4 | Complete    | 2026-07-17 |

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
*Phase 35 added 2026-07-17 — MRG-05 Grounding Gate + Stage-1 Scoping Fix (independent of v1.5 hub work; see below).*
*Phase numbering is continuous across all milestones; last v1.3/v1.4 phase: 28*
