# Roadmap: Shop2Parcel

## Milestones

- ✅ **v1.0 MVP** — Phases 1–9 (shipped 2026-05-04)
- ✅ **v1.1 Debug-Ready** — Phases 10–12 (shipped 2026-05-17)
- 📋 **v1.2 Debug Switch** — Phases 13–14 (in progress)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1–9) — SHIPPED 2026-05-04</summary>

- [x] Phase 1: Foundation & Discovery (2/2 plans) — completed 2026-04-27
- [x] Phase 2: API Clients (3/3 plans) — completed 2026-04-27
- [x] Phase 3: HA Config & Plumbing (4/4 plans) — completed 2026-04-27
- [x] Phase 4: Coordinator & Forwarding (3/3 plans) — completed 2026-04-27
- [x] Phase 5: Sensor Entities (2/2 plans) — completed 2026-04-27
- [x] Phase 6: Testing & HACS Packaging (4/4 plans) — completed 2026-04-27
- [x] Phase 7: Diagnostic Tooling (3/3 plans) — completed 2026-05-01
- [x] Phase 8: Parser Template Expansion (3/3 plans) — completed 2026-05-02
- [x] Phase 9: IMAP Support & Multi-Account (5/5 plans) — completed 2026-05-03

Full phase details: [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)

</details>

<details>
<summary>✅ v1.1 Debug-Ready (Phases 10–12) — SHIPPED 2026-05-17</summary>

- [x] Phase 10: Full-Window Scanning & Tracking Dedup (3/3 plans) — completed 2026-05-11
- [x] Phase 11: Activity Log & Debug Logging (3/3 plans) — completed 2026-05-12
- [x] Phase 12: Address Tech Debt / v1.1.1 patch (3/3 plans) — completed 2026-05-14

Full phase details: [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)

</details>

### 📋 v1.2 Debug Switch (Phases 13–14)

- [ ] **Phase 13: Dedup Store Persistence Fix** — Eliminate 400-looping tracking numbers on HA restart
- [ ] **Phase 14: Debug/Dry-Run Mode** — Add configurable debug toggle that suppresses all side effects and emits verbose diagnostics

## Phase Details

### Phase 13: Dedup Store Persistence Fix
**Goal**: The dedup store reliably prevents re-submission of previously-rejected tracking numbers across HA restarts
**Depends on**: Nothing (production bug fix, no new capability dependencies)
**Requirements**: DEDUP-01, DEDUP-02, DEDUP-03
**Success Criteria** (what must be TRUE):
  1. A tracking number that received a 400 "already added" response is NOT written to the dedup store, so it does not persist and loop on the next HA restart
  2. When parcelapp.net returns a 400 "already added" response, the coordinator treats it as an idempotent success (tracking number marked submitted, no retry)
  3. When HA starts, the developer can see "loaded N tracking numbers" at DEBUG level in the HA logs, confirming the store persistence path without inspecting raw storage files
  4. When a store save completes, the developer can see "saved N tracking numbers" at DEBUG level, confirming that write paths are observable
**Plans:** 2 plans
Plans:
- [ ] 13-01-PLAN.md — Add ParcelAppAlreadyAddedError exception class and branch parcelapp.py 400 handling (DEDUP-01)
- [ ] 13-02-PLAN.md — Wire already-added handler into both coordinators, add DEBUG store load/save logs, test coverage (DEDUP-01, DEDUP-02, DEDUP-03)

### Phase 14: Debug/Dry-Run Mode
**Goal**: Users can enable a debug/dry-run toggle in the integration options that suppresses all parcelapp.net side effects and emits full per-email diagnostic output
**Depends on**: Phase 13 (dedup fix must ship before debug mode, which bypasses dedup and would otherwise mask the persistence bug)
**Requirements**: DBG-01, DBG-02, DBG-03, DBG-04, DBG-05, DBG-06
**Success Criteria** (what must be TRUE):
  1. User can enable or disable debug mode via the integration options flow in the HA UI (works for both Gmail and IMAP account types)
  2. When debug mode is on, the scan window is automatically 365 days regardless of the user's configured rescan window
  3. When debug mode is on, no POST is sent to parcelapp.net (dry-run — existing parcelapp data is untouched)
  4. When debug mode is on, the dedup store is neither read nor written — every email is evaluated fresh on every poll
  5. When debug mode is on, each email's subject, sender, parse result, tracking candidates, and final outcome appear in the HA log at INFO level
  6. While debug mode is active, a Home Assistant persistent notification appears on every poll cycle, ensuring users are aware the integration is not submitting real data
**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation & Discovery | v1.0 | 2/2 | Complete | 2026-04-27 |
| 2. API Clients | v1.0 | 3/3 | Complete | 2026-04-27 |
| 3. HA Config & Plumbing | v1.0 | 4/4 | Complete | 2026-04-27 |
| 4. Coordinator & Forwarding | v1.0 | 3/3 | Complete | 2026-04-27 |
| 5. Sensor Entities | v1.0 | 2/2 | Complete | 2026-04-27 |
| 6. Testing & HACS Packaging | v1.0 | 4/4 | Complete | 2026-04-27 |
| 7. Diagnostic Tooling | v1.0 | 3/3 | Complete | 2026-05-01 |
| 8. Parser Template Expansion | v1.0 | 3/3 | Complete | 2026-05-02 |
| 9. IMAP Support & Multi-Account | v1.0 | 5/5 | Complete | 2026-05-03 |
| 10. Full-Window Scanning & Tracking Dedup | v1.1 | 3/3 | Complete | 2026-05-11 |
| 11. Activity Log & Debug Logging | v1.1 | 3/3 | Complete | 2026-05-12 |
| 12. Address Tech Debt | v1.1 | 3/3 | Complete | 2026-05-14 |
| 13. Dedup Store Persistence Fix | v1.2 | 0/2 | Not started | - |
| 14. Debug/Dry-Run Mode | v1.2 | 0/? | Not started | - |
