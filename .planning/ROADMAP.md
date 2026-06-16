# Roadmap: Shop2Parcel — v1.3 AI-based Email Analysis

**Milestone:** v1.3
**Goal:** Add a second-stage local-LLM extractor (Ollama) so emails the template parsers miss still produce complete `ShipmentData` — with a bounded async queue smoothing slow-machine inference and loud, surfaced failures.
**Granularity:** standard
**Phase numbering:** continues from v1.2 (last shipped: Phase 14). v1.3 spans Phase 15 → Phase 22.

## Milestones

- ✅ **v1.0 MVP** — Phases 1–9 (shipped 2026-05-04) — archived at [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)
- ✅ **v1.1 Debug-Ready** — Phases 10–12 (shipped 2026-05-17) — archived at [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)
- ✅ **v1.2 Debug Switch** — Phases 13–14 (shipped 2026-06-05) — archived at [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)
- 🔄 **v1.3 AI-based Email Analysis** — Phases 15–22 (in progress)

## Phases

- [x] **Phase 15: OllamaClient Foundation** — Standalone aiohttp transport to `/api/generate` with defensive JSON parsing
- [x] **Phase 16: OllamaExtractor + Schema Composition** — Pure extractor that builds prompts + JSON-Schema from the configured field list (completed 2026-06-09)
- [x] **Phase 17: Config-Flow Expansion** — New Ollama options, `/api/tags` validate-on-save, locked-field disclosure, v1.2 backward-compat fallback (completed 2026-06-10)
- [x] **Phase 18: Queue Plumbing (transitional)** — Bounded `asyncio.Queue` + drop-newest backpressure + in-flight key dedup, wired into poll loop alongside legacy path (completed 2026-06-12)
- [x] **Phase 19: Worker Spawn + Poll Loop Flip** — Long-lived background worker via `entry.async_create_background_task`, 5 s cancel-with-suppress shutdown, poll loop becomes Ollama-free (completed 2026-06-12)
- [x] **Phase 20: Merge + Quota Guards (CRITICAL)** — LLM-authoritative merge with per-field guards, carrier-regex pre-POST validation, `MAX_STAGE2_POSTS_PER_POLL` cap, skip-dedup-on-failure (completed 2026-06-15)
- [ ] **Phase 21: Failure Surface + Diagnostics** — `_LOGGER.error` + activity-log outcomes + persistent notification with cooldown + `Stage2Sensor` + custom-field sensor attributes
- [ ] **Phase 22: README Setup + End-to-End Validation** — Docker/Portainer install, three-topology networking notes, model-pull caveat, reachability sanity-check, real-Ollama integration test

## Phase Details

### Phase 15: OllamaClient Foundation

**Goal**: A standalone, HA-free HTTP client can POST to a local Ollama `/api/generate` endpoint and return parsed JSON from a small/medium model.
**Depends on**: Nothing (foundation; mirrors `parcelapp.py` shape)
**Requirements**: OLLM-04, OLLM-05, OLLM-06
**Success Criteria** (what must be TRUE):

  1. A developer can construct an `OllamaClient` with an injected `aiohttp.ClientSession`, a base URL, a model name, and a timeout — and call `async_generate(prompt, schema)` to receive a parsed `dict` from a live Ollama server.
  2. When Ollama returns a 200 response wrapped in markdown fences (e.g. ` ```json … ``` `), the client strips the fence on a defensive retry and still produces a valid `dict`; only after the second-pass failure does it raise `OllamaSchemaError`.
  3. When Ollama returns a 200 response containing full-width digits, Cyrillic lookalikes, zero-width characters, or BOM marks inside locked-field values, the client normalizes them via NFKC + zero-width strip + `{...}` substring extraction before `json.loads`, so downstream merge sees ASCII-normalized values.
  4. Transient transport problems (timeout, connection refused, 5xx) raise `OllamaTransientError`; auth/404/malformed schema responses raise `OllamaSchemaError` — the two exception classes are distinct and downstream code can branch on them.

**Plans**: 4 plans
Plans:
**Wave 1**

- [x] 15-01-PLAN.md — Append OllamaTransientError and OllamaSchemaError to api/exceptions.py (foundation taxonomy for Phase 15)
- [x] 15-02-PLAN.md — Create api/ollama_normalize.py (NFKC + zero-width strip + {...} substring extraction; OLLM-06) and its 8-test suite

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 15-03-PLAN.md — Create api/ollama_client.py OllamaClient transport (OLLM-04/05/06 2-pass parse) and its 15-test suite

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 15-04-PLAN.md — Register live_ollama pytest marker in pyproject.toml and ship the single opt-in live smoke test (D-10..D-13)

### Phase 16: OllamaExtractor + Schema Composition

**Goal**: An `OllamaExtractor` composes an Ollama call from raw email HTML + Stage-1 `ShipmentData` + the configured field list, asks Ollama for structured JSON, and returns a typed `Stage2Result` — without touching HA, the queue, or POST plumbing.
**Depends on**: Phase 15
**Requirements**: OLLM-01, OLLM-02, OLLM-03, FLD-04
**Success Criteria** (what must be TRUE):

  1. Given a sender-matched email HTML body and a Stage-1 `ShipmentData`, `OllamaExtractor.async_extract(...)` returns a dict containing at minimum the 3 locked fields (`tracking_number`, `carrier_name`, `order_name`) plus any user-added custom fields configured for the entry.
  2. The `format` parameter sent to Ollama is a JSON-Schema built dynamically from the active field list (locked ∪ custom) — adding a custom field in options re-shapes the schema on next reload, no code change.
  3. The user can configure Ollama URL (required, no default), model (default `qwen3.5:2b`), and per-request timeout (default 60 s) via options; the extractor reads those values at construction time and applies them to every call.
  4. Empty / null Stage-2 values for locked fields are preserved as `None` (not empty string) so the downstream merge can distinguish "model declined to extract" from "model returned blank".

**Plans**: 3 plans
Plans:
**Wave 1**

- [x] 16-01-PLAN.md — Stage2Result frozen dataclass + extractors/ subpackage scaffolding + tests/extractors/ conftest

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 16-02-PLAN.md — LOCKED_OLLAMA_FIELDS const, OllamaClient.async_generate_with_metadata, and the three module-level helpers (build_schema, preprocess_html, build_prompt) + helper tests

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 16-03-PLAN.md — OllamaExtractor class (__init__ + _validate_fields + async_extract + _split_and_coerce) + class-level test coverage of every D-NN decision and OLLM-01/02/03 + FLD-04

**Research**: Done — see .planning/phases/16-ollamaextractor-schema-composition/16-RESEARCH.md (prompt template, JSON-Schema shape, BS4 preprocessing, qwen3.5:2b tag verification all canonicalized).

### Phase 17: Config-Flow Expansion

**Goal**: A user can configure Ollama URL, model, timeout, queue maxlen, and custom extraction fields in the config/options flow — and a misconfigured Ollama server is caught at save time rather than at first poll.
**Depends on**: Phase 16 (extractor's option contract is the source of truth for the schema)
**Requirements**: OLLM-01, OLLM-02, OLLM-03, FLD-01, FLD-02, CFG-01, CFG-02, CFG-03, CFG-04
**Success Criteria** (what must be TRUE):

  1. The config / options flow refuses to save an unreachable Ollama URL: on submit the integration calls `GET /api/tags`, and any timeout / connect-refused / 404 surfaces as a friendly inline form error before the entry is created or updated.
  2. The config / options flow refuses to save a model name that is not present in `/api/tags`, surfacing a friendly "run `ollama pull <model>`" inline error with the exact missing tag.
  3. The 3 locked extraction fields (`tracking_number`, `carrier_name`, `order_name`) are displayed read-only in the form with descriptive text explaining they cannot be removed; the user can still add custom fields freely.
  4. A v1.2 entry upgraded to v1.3 without an Ollama URL configured continues to operate Stage-1-only (`stage2_enabled: false`) — the integration does not crash, does not block setup, and exposes a diagnostic attribute the user can observe to detect the unconfigured state.

**Plans**: 4 plans
Plans:
**Wave 1** *(parallel)*

- [x] 17-01-PLAN.md — Phase 17 constants block in const.py + OllamaClient.async_get_tags @staticmethod (CFG-01 transport; OLLM-01/02/03 constants)
- [x] 17-02-PLAN.md — PollStats.stage2_enabled + __init__ derivation + config_flow.py options seed (CFG-04 backward-compat)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 17-03-PLAN.md — options_flow.py rewrite: menu-first init + async_step_settings with Ollama fields and /api/tags validation (OLLM-01/02/03, CFG-01/02/03)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 17-04-PLAN.md — Custom-fields CRUD (menu + add + remove) with locked-field collision + invalid-name validation (FLD-01, FLD-02)

### Phase 18: Queue Plumbing (transitional)

**Goal**: A bounded `asyncio.Queue` and in-flight key set are wired into the base coordinator so the poll loop can enqueue Stage-2 jobs without blocking — even while the legacy inline POST path still runs in parallel.
**Depends on**: Phase 17 (queue maxlen comes from options)
**Requirements**: QUE-01, QUE-03, QUE-06, QUE-07
**Success Criteria** (what must be TRUE):

  1. Each coordinator owns a bounded `asyncio.Queue` whose `maxsize` is configurable via options (default 32, range 1–256); the queue is constructed in `async_start_stage2` and torn down in `async_stop_stage2`.
  2. When the queue is full, a new enqueue triggers drop-newest backpressure: a `_LOGGER.warning` is emitted, a `stage2_dropped_backpressure` activity-log event is appended, the dropped email's tracking number is NOT written to dedup, and the next full-window poll re-enqueues it.
  3. `_async_update_data` completes in under 1 s with 50 items already on the queue — no Ollama call, no POST call, no event-loop block; the only work it does for Stage 2 is `_enqueue_stage2`.
  4. An in-memory `_stage2_enqueued_keys: set[str]` prevents the same `storage_key` from being enqueued twice across consecutive polls; entries leave the set when the worker finishes (or drops) the job.

**Plans**: TBD

### Phase 19: Worker Spawn + Poll Loop Flip

**Goal**: A single long-lived background worker per coordinator drains the queue serially, calls Ollama, then POSTs to parcelapp — replacing the legacy inline POST path entirely. Worker lifecycle is bound to the config entry, with a bounded shutdown.
**Depends on**: Phase 18
**Requirements**: QUE-02, QUE-04, QUE-05, MRG-01
**Success Criteria** (what must be TRUE):

  1. On `async_setup_entry`, exactly one worker is spawned per coordinator via `entry.async_create_background_task`, after `_async_load_store` and before `async_config_entry_first_refresh` — so the first poll's enqueues already have a draining consumer.
  2. Stage-2 Ollama extraction runs on every sender-matched email (always-on); the legacy inline POST in the per-message loop has been removed — all POSTs to parcelapp now go through the worker.
  3. On entry unload (HA shutdown, options reload, or user-triggered reload), the worker is cancelled, awaited with a 5 s timeout, and any `CancelledError` is suppressed — after three consecutive reloads, `len(asyncio.all_tasks())` shows zero leaked Stage-2 workers.
  4. The worker emits coordinator state updates via `coordinator.async_set_updated_data(...)` only — it never mutates `_pending_shipments` directly, preserving the snapshot-at-call-time semantics that protect `_async_save_store` from `RuntimeError: dictionary changed size during iteration`.

**Plans**: 2 plans
Plans:
**Wave 1**

- [x] 19-01-PLAN.md — TDD RED suite for worker lifecycle + behavior in tests/test_stage2_worker.py (QUE-02/QUE-04/QUE-05/MRG-01, D-02/D-03/D-05/D-06, Pitfalls 1/5/6)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 19-02-PLAN.md — Implement coordinator changes (sentinels, OllamaClient/OllamaExtractor construction, _async_stage2_worker, _async_process_stage2_job, async_stop_stage2 cancel-with-suppress) to turn Plan 01 GREEN

**Research**: Done — see .planning/phases/19-worker-spawn-poll-loop-flip/19-RESEARCH.md (worker leak, event-loop block, timeout cascade, store corruption all canonicalized; zero new packages).

### Phase 20: Merge + Quota Guards (CRITICAL)

**Goal**: Replace Phase 19's discard-Stage2Result stub with real merge logic and four quota-protection guards so Stage-2 LLM values safely enrich POST payloads without burning parcelapp.net quota on hallucinations or flooding — `merge_llm_authoritative` enforces per-field MRG-03 conflict guards with `str.strip().upper()` normalization and MRG-04 loose tracking-number sanity (6–40 chars, alphanum/dash/space), `MAX_STAGE2_POSTS_PER_POLL=5` caps Stage-2 POSTs per poll with a single persistent notification, and FAIL-03 routes Ollama errors to skip-POST and skip-dedup so transient failures are retriable.
**Depends on**: Phase 19 (worker is the call site for all five mitigations)
**Requirements**: MRG-02, MRG-03, MRG-04, MRG-05, FAIL-03
**Success Criteria** (what must be TRUE):

  1. A pure `merge_llm_authoritative(stage1, result)` function returns a merged `ShipmentData` and a conflicts list — LLM values overwrite Stage 1 ONLY when Stage 1 returned `None` for that field OR Stage 2 returned the same normalized value; any conflict on a locked field keeps Stage 1 and the caller emits exactly one `stage2_conflict` activity event per job (listing all conflicting fields in one payload).
  2. Before Stage-2 promotes a `tracking_number` (the Stage-1-None path), the value passes a loose sanity check: non-empty, 6–40 chars, `^[A-Za-z0-9\- ]+$`; failing values are silently discarded (no event), preventing garbled LLM outputs from consuming parcelapp quota on HTTP 400.
  3. A `MAX_STAGE2_POSTS_PER_POLL = 5` cap is enforced per poll cycle; on cap-hit, the first skipped job fires exactly one persistent HA notification (id `shop2parcel_stage2_cap_{entry_id}`), subsequent skips in the same poll are silent, and skipped emails are NOT written to dedup so the next poll re-attempts them.
  4. `OllamaTransientError` and `OllamaSchemaError` during Stage-2 extraction route to no-POST and no-`_submitted_tracking_numbers`-write, scoped to Stage-2-only failures; Stage-1-only successful POSTs continue to write to dedup as before.

**Plans**: 3 plans
Plans:
**Wave 1**

- [x] 20-01-PLAN.md — TDD: pure `merge.py` module with `merge_llm_authoritative` (MRG-03 conflict guard + MRG-04 sanity regex) + 15-test suite (D-01/D-02/D-03)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 20-02-PLAN.md — Wire merge into `_async_process_stage2_job` + extend Stage2Job with `message_id` and `meta` (D-06/D-07) + emit `stage2_conflict` activity event + FAIL-03 skip-POST and skip-dedup on Ollama errors (MRG-02, MRG-03 wiring, FAIL-03)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 20-03-PLAN.md — `MAX_STAGE2_POSTS_PER_POLL=5` constant + `stage2_cap_notification_id` helper + coordinator counters + `_reset_stage2_poll_counters` + cap gate + once-per-poll notification + dismissal at remove + temperature:0 verification (MRG-05, D-08/D-09/D-10/D-11/D-12)

**Research**: Done — see .planning/phases/20-merge-quota-guards-critical/20-RESEARCH.md (merge function shape, Stage2Job extension pitfalls, counter reset placement, persistent notification pattern all canonicalized; zero new packages).

### Phase 21: Failure Surface + Diagnostics

**Goal**: Every Stage-2 failure is loud (log + activity event), the user is notified after sustained failures without spam, and the user can observe Stage-2 queue depth + lifetime counters via a dedicated diagnostic sensor.
**Depends on**: Phase 20 (failure outcomes are emitted by the now-finalized merge / quota guard code)
**Requirements**: FAIL-01, FAIL-02, FAIL-04, FAIL-05, FLD-03, DIAG-01, DIAG-02
**Success Criteria** (what must be TRUE):

  1. Every Stage-2 failure produces a single `_LOGGER.error()` line with email subject, sender, error class, and error message — and a single `stage2_failed` outcome event is appended to the existing 50-slot activity-log ring buffer without raising `maxlen`.
  2. After `STAGE2_NOTIFY_THRESHOLD` (default 3) consecutive Stage-2 failures, a persistent HA notification fires with a stable per-entry ID `shop2parcel_stage2_failing_{entry_id}`; subsequent failures re-fire only after `STAGE2_NOTIFY_COOLDOWN_S` (1 hour) has elapsed, so notification panel never accumulates more than one banner per entry.
  3. The first Stage-2 success after a failure streak dismisses the persistent notification and resets the consecutive-failure counter to zero — recovery is silent and immediate, no manual dismissal required.
  4. A `Stage2Sensor` diagnostic entity exposes the current queue depth and lifetime counters (`enqueued_total`, `succeeded_total`, `failed_total`, `dropped_backpressure_total`, `schema_error_total`, `conflict_total`); the same counters surface in the existing diagnostics download via extended `PollStats`.
  5. User-added custom extraction fields appear as sensor attributes on each shipment sensor — they are NOT included in the parcelapp POST body, so the user can verify in HA Developer Tools that e.g. an "estimated_delivery" custom field appears as an attribute but never reaches parcelapp.net.

**Plans**: TBD

### Phase 22: README Setup + End-to-End Validation

**Goal**: A new user with a working Ollama instance can point Shop2Parcel at it, pull the recommended model, and observe a real Stage-2 extraction end-to-end — guided entirely by README content, with networking pitfalls explicitly addressed. Ollama install itself is out of scope and deferred to Ollama's own docs.
**Depends on**: Phase 21 (full feature surface available for end-to-end validation)
**Requirements**: DOC-01, DOC-02, DOC-03, DOC-04
**Success Criteria** (what must be TRUE):

  1. The README has an "AI-based email analysis (v1.3)" section explaining what Stage 2 does, when it runs (every sender-matched email, always-on after Stage 1), and the LLM-authoritative merge semantics with the per-field merge guards.
  2. The README links to Ollama's official documentation as the source of truth for install (no duplication of Docker / Portainer steps) and provides Shop2Parcel-specific model guidance: the `ollama pull qwen3.5:2b` command with the "if the tag fails, try `qwen2.5:3b` or `qwen3:1.7b`" caveat so the user can recover from a stale upstream tag.
  3. The README documents three networking topologies (HA + Ollama on same Docker host network, HA + Ollama on shared bridge, HA and Ollama on separate hosts) with example URL forms for each — and the user trying `localhost:11434` on the separate-hosts topology is explicitly warned in the same section.
  4. The README includes a reachability sanity-check command (`docker exec homeassistant wget -qO- http://<HOST>:11434/api/tags`) with an example healthy response, so the user can independently verify connectivity before opening the integration's config flow.

**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1–9 (collapsed) | v1.0 | 29/29 | Complete | 2026-05-04 |
| 10–12 (collapsed) | v1.1 | 9/9 | Complete | 2026-05-17 |
| 13, 13.1, 14 (collapsed) | v1.2 | 9/9 | Complete | 2026-06-05 |
| 15. OllamaClient Foundation | v1.3 | 4/4 | Complete    | 2026-06-06 |
| 16. OllamaExtractor + Schema Composition | v1.3 | 3/3 | Complete    | 2026-06-09 |
| 17. Config-Flow Expansion | v1.3 | 4/4 | Complete    | 2026-06-11 |
| 18. Queue Plumbing (transitional) | v1.3 | 2/2 | Complete    | 2026-06-12 |
| 19. Worker Spawn + Poll Loop Flip | v1.3 | 2/2 | Complete    | 2026-06-12 |
| 20. Merge + Quota Guards (CRITICAL) | v1.3 | 3/3 | Complete    | 2026-06-16 |
| 21. Failure Surface + Diagnostics | v1.3 | 0/0 | Not started | — |
| 22. README Setup + End-to-End Validation | v1.3 | 0/0 | Not started | — |

## Coverage

**v1.3 requirement coverage: 37 / 37 mapped ✓**

| Category | Count | Phases |
|----------|-------|--------|
| OLLM (Stage-2 client) | 6 | 15, 16 |
| FLD (extraction fields) | 4 | 16, 17, 21 |
| MRG (trigger and merge) | 5 | 19, 20 |
| QUE (async queue + worker) | 7 | 18, 19 |
| FAIL (failure surface) | 5 | 20, 21 |
| CFG (config-flow validation) | 4 | 17 |
| DIAG (diagnostics) | 2 | 21 |
| DOC (README) | 4 | 22 |

**Phases flagged NEEDS RESEARCH (run `/gsd:plan-phase --research-phase N`):**

- Phase 16 — prompt template + JSON-Schema shape + I-9 (UTF-8 / markdown-fence) mitigations
- Phase 19 — highest blast radius (C-1 worker leak, C-4 event-loop block, C-6 timeout cascade, I-11 store corruption)
- Phase 20 — owns the 5-mitigation quota-burn recipe (per-field merge guard, carrier regex, `temperature:0`, per-poll cap, scoped skip-dedup)

## Archives

- v1.0: [.planning/milestones/v1.0-ROADMAP.md](./milestones/v1.0-ROADMAP.md)
- v1.1: [.planning/milestones/v1.1-ROADMAP.md](./milestones/v1.1-ROADMAP.md)
- v1.2: [.planning/milestones/v1.2-ROADMAP.md](./milestones/v1.2-ROADMAP.md)
