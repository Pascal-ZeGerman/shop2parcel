---
phase: 12-address-tech-debt
plan: "02"
subsystem: planning-docs
tags: [requirements, documentation, tech-debt]
dependency_graph:
  requires: []
  provides: [accurate-requirements-tracking]
  affects: [.planning/REQUIREMENTS.md]
tech_stack:
  added: []
  patterns: []
key_files:
  created: []
  modified:
    - .planning/REQUIREMENTS.md
decisions:
  - "Marked all v1 requirements (CONF-01..07, FWRD-01..05, DIAG-01..12, PARSE-01..13, HACS-01..03) as [x] complete — documentation debt from phases 2-8 cleared"
  - "Marked EMAIL-01..08 traceability rows as Complete — were Pending despite shipping in v1.0 Phase 2"
  - "Left DISC-01, DISC-02 as [ ] with ✓ DONE annotation — intentional format per original spec, not open items"
  - "Used git add --force to track REQUIREMENTS.md despite .planning/ gitignore — consistent with how SUMMARY.md files are tracked"
metrics:
  duration_minutes: 13
  completed_date: "2026-05-14"
  tasks_completed: 1
  files_changed: 1
---

# Phase 12 Plan 02: Requirements Documentation Debt — Summary

**One-liner:** Marked 40 implemented v1 requirements complete in REQUIREMENTS.md — checkboxes and traceability table now reflect shipped state of phases 2–8.

## What Was Done

Updated `.planning/REQUIREMENTS.md` to accurately reflect the completion status of all v1 requirements implemented across Phases 2–8.

**Before:** Most Phase 2–8 requirements showed `[ ]` checkboxes and `Pending` traceability status despite being shipped in v1.0.

**After:** All implemented requirements show `[x]` and `Complete`. Only DISC-01 and DISC-02 retain `[ ]` (intentional — they use the `[ ] ... ✓ DONE` annotation format, not genuine open items).

### Changes Made

**Checkbox updates ([ ] → [x]):**
- CONF-01..07 (Phase 3 — HA Config & Plumbing)
- FWRD-01..05 (Phase 4 — Coordinator & Forwarding)
- DIAG-01..12 (Phase 7 — Diagnostic Tooling)
- PARSE-01..13 (Phase 8 — Parser Template Expansion)
- HACS-01..03 (Phases 1 & 6 — HACS Packaging)

**Traceability table updates (Pending → Complete):**
- EMAIL-01..08 (Phase 2)
- CONF-01..07 (Phase 3)
- FWRD-01..05 + EMAIL-05 poll interval (Phase 4)
- HACS-02..03 (Phase 6)
- DIAG-01..12 (Phase 7)
- PARSE-01..13 (Phase 8)

**Unchanged (verified):**
- EMAIL-01..08 checkboxes: already `[x]` from original authoring
- ENTT-01..06: already `[x]` and `Complete`
- SCAN-01..03, DEDUP-01..03, ACTLOG-01..05, DBG-01..05: already `[x]` and `Complete` (v1.1)
- DISC-01, DISC-02: `[ ]` with `✓ DONE` annotation retained as-is
- v2 requirements (AUTH-01, MULT-01, MULT-02, NOTF-01, NOTF-02): no checkboxes, unchanged
- Header updated to: `2026-05-14 — Phase 12: marked all implemented v1 requirements complete`
- Footer `Last updated` line updated to match

## Verification Results

```
grep -c "\[ \]" .planning/REQUIREMENTS.md  →  2  (only DISC-01 and DISC-02)
grep "Pending" .planning/REQUIREMENTS.md  →  0  (no Pending rows remain)
```

Spot checks confirmed:
- CONF-01: `[x]` + `Complete`
- FWRD-05: `[x]` + `Complete`
- DIAG-12: `[x]` + `Complete`
- PARSE-13: `[x]` + `Complete`
- HACS-03: `[x]` + `Complete`
- EMAIL-01 traceability: `Complete`

## Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Mark implemented v1 requirements complete | 19837a6 | .planning/REQUIREMENTS.md |

## Deviations from Plan

None — plan executed exactly as written.

The only process note: `.planning/REQUIREMENTS.md` was never previously committed (`.planning/` is gitignored), so `git add --force` was needed to track it. This is consistent with how `.planning/phases/*/SUMMARY.md` files are tracked (force-added through the same gitignore rule).

## Known Stubs

None — this plan modifies only documentation (checkbox states and traceability table statuses). No code stubs exist.

## Threat Flags

None — planning document only, no runtime security surface changes.

## Self-Check: PASSED

- [x] `.planning/REQUIREMENTS.md` exists and contains correct content
- [x] Commit 19837a6 exists: `git log --oneline | grep 19837a6`
- [x] `grep -c "\[ \]" .planning/REQUIREMENTS.md` returns 2 (DISC-01, DISC-02)
- [x] `grep "Pending" .planning/REQUIREMENTS.md` returns 0
