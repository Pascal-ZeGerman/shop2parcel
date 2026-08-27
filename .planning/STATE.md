---
gsd_state_version: 1.0
milestone: none
milestone_name: none — v1.5 closed, v1.6 not yet defined
current_phase: 36
current_phase_name: DHL Carrier Support + USPS Digest Sender Extraction
status: idle
stopped_at: "Completed 36-02-PLAN.md; milestone v1.5 archived; manifest bumped to 1.6.0-rc1 (tag pushed, PR #45 merged)"
last_updated: "2026-08-27T14:29:16.087Z"
last_activity: 2026-08-27
last_activity_desc: quick task 260827-e6t widened DEFAULT_IMAP_SEARCH from 4 to 7 SUBJECT terms (added delivered/order/confirmed) with a correct RFC 3501 6-OR prefix tree, fixing a confirmed production miss on a real 17TRACK/COLAMY delivery-notification email
progress:
  total_phases: 13
  completed_phases: 13
  total_plans: 51
  completed_plans: 51
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-28)

**Core value:** Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.
**Current focus:** None active. v1.5 shipped and archived; Phases 35/36 (independent, spike-driven) also shipped; v1.6.0-rc1 tagged and merged to main. Next: `/gsd-new-milestone` to define v1.6 scope.

## Current Position

Phase: 36 — DHL Carrier Support + USPS Digest Sender Extraction (complete, verified passed 8/8)
Plan: All plans complete
Status: No active phase — awaiting next milestone definition
Last activity: 2026-08-08 — quick task 260808-074 refreshed the spike-findings-shop2parcel skill docs (DHL/USPS-digest/sender-exclusion now shipped, not pending; 260807-tpu production-bug history recorded)

```
[████████████████████] 68/68 plans (100%)
```

## Accumulated Context

### Roadmap Evolution

- Phase 23 added (v1.4): Decouple Stage-2 LLM extraction from parcelapp POST + honor debug-mode POST suppression. Completed 2026-06-24.
- Phase 26 added (v1.4, completed 2026-06-24): Operational Health Sensor Rework — coordinator-owned health counters replacing one-way per-shipment sensors; additive store keys; Gmail+IMAP parity.
- Phase 27 added (v1.4, completed 2026-06-30): Subject-Only Gmail Filter + Hybrid Ollama Gatekeeper — subject-line pre-filter, inline Ollama fallback with volume guards, first-refresh skip, wall-clock budget.
- Phase 28 added (v1.4, completed 2026-06-30): Carrier Format Pre-POST Validation + Full-Body Gmail Scan — validate_carrier_format() gate, DHL branch removal, full-body fallback, CarrierFormatRejectionsSensor (14th diagnostic).
- v1.5 Phases 29–34 added (2026-07-03): Hub skeleton + foundational safety, shared dedup, shared budget, shared queue+worker, IMAP parity, lifecycle tests + global sensors.
- Phase numbering is continuous: last v1.3/v1.4 phase was 28; v1.5 starts at 29.
- Phase 35 added (2026-07-17): MRG-05 Grounding Gate + Stage-1 Scoping Fix — from spike findings (spikes 006-013), independent of the v1.5 hub work. Adds the validated Stage-2 order_name/order_summary grounding gate (merge.py) plus conservative Stage-1 tracking-number scoping broadening (email_parser.py). Not part of the original v1.5 requirement set.

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Gmail API over IMAP — IMAP being shut down for OAuth by Google; Gmail API is the supported path
- Dual-strategy email parsing — Shopify standard HTML template extraction first, keyword/regex fallback for non-standard emails
- Configurable Gmail search query — user can customize the email search filter in options flow (default: from:no-reply@shopify.com subject:shipped)
- Polling over webhooks — no public HA endpoint required
- HACS-compatible repo structure — enables easy install and future sharing
- GmailClient accepts async_add_executor_job as Callable injection — no HA import in api/ module
- Tests mock googleapiclient via sys.modules at module load time — runs without network/PyPI access
- ParcelAppQuotaError carries optional reset_at field per D-02 for smarter coordinator backoff
- EmailParser._parse_html_template scans only <p> elements — Shopify standard template uses prose in <p>, no CSS class on tracking data
- normalize_carrier falls back to pholder not none — pholder is a valid parcelapp code returning HTTP 200, preventing quota waste on unknown carriers
- ShipmentData.carrier_name stores raw Shopify string — Phase 4 coordinator calls normalize_carrier() before POSTing to parcelapp
- aiohttp/aioresponses symlinked from sibling VW-CarNet venv — PyPI network-blocked in Raspberry Pi dev environment
- ParcelAppClient session injection pattern — Phase 4 coordinator injects shared HA session, client never creates its own
- v0.1.0 released (D-10 + D-11) — first HACS-installable tag, all 4 CI workflows green, GitHub Release at https://github.com/Pascal-ZeGerman/shop2parcel/releases/tag/v0.1.0
- v1.1 scanning architecture: full-window scan always on (no message-ID/UID gate), dedup shifts to persisted tracking-number set in HA Store
- Phase 13.1: STORAGE_VERSION bumped to 3; v2->v3 migration preserves submitted_tracking_numbers + quota_exhausted_until, seeds persisted_shipments as {}
- Phase 13.1: _SHIPMENT_FIELD_TYPES module-level constant used for per-entry type validation in _async_load_store (T-13.1-04 ASVS V5)
- Phase 13.1: _pending_shipments assigned before _async_save_store() so debounced lambda captures updated state
- v1.3 Phase 15: OllamaClient mirrors `parcelapp.py` shape — session injection, no HA imports, custom exception taxonomy (`OllamaTransientError`, `OllamaSchemaError`)
- v1.3 Phase 15-03: OllamaClient 2-pass parse pipeline shipped (Pass 1 = normalize + json.loads; Pass 2 = fence-strip + normalize + json.loads on Pass 1 JSONDecodeError only). Missing-`{` from normalize is a hard fail (no Pass 2 retry). NFKC preserves Cyrillic A — Phase 20 carrier-regex pre-POST validation will catch any homoglyph slips on real tracking numbers.
- v1.3 architecture lock: in-memory queue only (HA-restart-lossy by design); full-window rescan re-discovers un-dedup'd emails — no STORAGE_VERSION bump
- v1.3 architecture lock: single long-lived worker per coordinator; multi-worker rejected (Ollama serializes per-model + ParcelApp 20/day quota)
- Phase 23 Wave 2: Quota guard moved post-extraction (LD-03) — Ollama always runs regardless of parcelapp quota; merged_shipment persisted to _pending_posts for drain
- Phase 23 Wave 2: Debug dry-run branch added after extractor, before quota-defer — no POST, no writes in debug mode (LD-02/DBG-03)
- Phase 23 Wave 2: ParcelAppQuotaError handler extended to persist merged_shipment to _pending_posts (Pitfall 2 fix — no item loss on 429 during POST)
- v1.3 architecture lock: drop-newest backpressure on QueueFull (drop-oldest rejected — wastes head-of-queue work, breaks FIFO activity-log ordering)
- v1.3 quota-burn mitigation set is INSEPARABLE: per-field merge guards + carrier-regex pre-POST validation + `temperature:0` + `MAX_STAGE2_POSTS_PER_POLL` cap + scoped skip-dedup. All five land in Phase 20.
- v1.3 Phase 15-04: `live_ollama` pytest marker registered in `pyproject.toml`; single opt-in smoke test `tests/api/test_ollama_client_live.py` gated by `OLLAMA_URL` env var (D-10/D-11/D-12/D-13). CI silently skips. Phase 15 complete: 384 tests passing, 1 skipped (the live smoke).
- Phase 20-01: merge_llm_authoritative returns tuple[ShipmentData, list[dict]] (Option A) — merge.py stays HA-free per D-02; coordinator emits stage2_conflict event using returned conflicts list
- Phase 20-01: type: ignore[arg-type] on dataclasses.replace(**overrides) — mypy cannot resolve dict[str, str | None] spread against ShipmentData typed replace() overload; runtime values always valid
- [Phase ?]: Phase 22 D-12/D-13/D-14/D-15: tests/test_stage2_e2e_live.py ships with live_ollama marker, OLLAMA_URL gate, and OllamaExtractor construction — fulfills Phase 15 D-13 deferral
- v1.5 architecture: Shop2ParcelHub singleton in hass.data[DOMAIN]["__shared__"]; asyncio.Lock (_init_lock) created synchronously before first await in async_setup_entry; hub reference-counted via attach(coordinator)/detach(coordinator); worker registered via hass.async_create_background_task (not entry-scoped)
- v1.5: try_consume() is synchronous (no await) — lock-free by design; the single shared worker serializes all POSTs sequentially
- v1.5: SHARED_STORAGE_VERSION=1 in shop2parcel.__shared__ store; independent version chain from per-entry STORAGE_VERSION=3
- v1.5: Migration strategy — used_today=0 on migration day; max() across all quota_exhausted_until; union-merge submitted_tracking_numbers capped at 1000
- v1.5: Stage2Job gains entry_id + callback fields for per-account result routing; hub worker calls job.callback(result) after each extraction
- [Phase ?]: Phase 29-01: explicit hub.detach(coordinator) in async_unload_entry (not via entry.async_on_unload) — sidesteps the async_on_unload/async_unload_platforms ordering assumption
- [Phase ?]: Phase 29-01: hub.async_shutdown() never touches hass.data — async_unload_entry owns deleting hass.data[DOMAIN]['__shared__'] only after refcount reaches 0 (D-04/D-06)
- [Phase ?]: Phase 29-01: SHARED_STORAGE_VERSION=1 lives only in hub.py — separate version chain from coordinator.py's STORAGE_VERSION=3
- [Phase ?]: Phase 29-01: hub worker stub tested via hub._queue.put_nowait() directly (D-02) — no coordinator wiring or test-only helper added
- [Phase ?]: PAR-02: ImapCoordinator now sets _first_refresh_done=True on success path only, mirroring GmailCoordinator (gmail_coordinator.py:200)
- [Phase ?]: Combined Task 1+Task 2 TDD cycles in 30-01 since seed_from_list reuses check_and_mark's shared FIFO trim helper
- [Phase ?]: Hub check_and_mark/is_submitted perform zero normalization on tn -- verbatim store/compare (SPEC Prohibition 1, hub side); caller-canonical contract enforced in Plan 30-03
- [Phase 30]: async_setup calls self.async_load() unconditionally after version-handling -- mocked store's async_load invoked twice per setup in tests, left as-is (no test asserts call count)
- [Phase 30]: async_shutdown delegates to self.async_save() so version+submitted_tracking_numbers write path cannot drift between explicit save and shutdown flush
- [Phase 30-03]: Deferred hub migration persist (hub.async_save + per-entry save) to the end of _async_load_store, after all state hydration completes, to avoid wiping persisted_shipments/pending_posts/counters with __init__ defaults (Rule 1 fix).
- [Phase 30-03]: tests/conftest.py autouse fixture monkeypatches Shop2ParcelCoordinator.__init__ to auto-attach a shared, I/O-free per-hass test hub, avoiding ~250 individual coordinator-construction call-site edits while leaving hub._refcount and real async_setup_entry wiring untouched.
- [Phase ?]: 35-01: 4-tuple return from merge_llm_authoritative_with_grounding (not the spike blueprint's 3-tuple) keeps grounding rejections in their own list, separate from MRG-04 carrier-format gate_rejections
- [Phase 35]: 35-02: LABEL_TAGS=[p,td] stays unchanged; TRACKING_TAGS=[p,td,div,span] is the only broadened scope — order_name/carrier_name never scan div/span, closing the footer-boilerplate false-positive risk
- [Phase 35]: 35-02: Div-recovered tracking numbers now return via html_template strategy before Tier-1 regex fallback runs, so order_name/carrier_name are lost when they lived outside p/td — deliberate, validated tradeoff per RESEARCH.md Pitfall 4
- [Phase ?]: Phase 35-03: MRG-05 gate wired into coordinator.py's production merge call site — order_name/order_summary now gated via preprocess_html(job.html_body) recomputed prose + merge_llm_authoritative_with_grounding; grounding rejections routed to a new isolated PollStats.grounding_rejected_total counter (RESEARCH.md Pitfall 3)
- [Phase ?]: 35-04: gmail_coordinator.py inline Stage-1-miss fallback (Pattern 3, no Stage-1 ShipmentData) gates order_name/order_summary via direct validate_grounding() calls at the fb_shipment construction site -- not the merge_llm_authoritative_with_grounding wrapper, which requires a real Stage-1 value
- [Phase ?]: 35-04: both real order_name/order_summary promotion sites (worker merge path from 35-03 + inline gmail fallback from 35-04) are now grounding-gated on body-only prose -- SC-1/SC-2 hold everywhere order fields can be promoted
- [Phase ?]: hub.py hoists _next_midnight_utc/_today_utc_str verbatim from coordinator.py (D-05); coordinator copies stay until 31-04 removes them
- [Phase ?]: hub._maybe_reset_used_today() is in-memory-only (no persist) in 31-01 — D-07-gated end-of-poll save deferred to 31-04/31-05
- [Phase ?]: HUB_STAGE2_POLL_WINDOW = timedelta(minutes=30) placed in const.py so 31-03 can pass it directly to async_track_time_interval
- [Phase ?]: async_save persists the private _used_today backing attribute (not the used_today rollover property) so a save call has zero read-triggered side effects
- [Phase ?]: quota_exhausted_until's corrupt-value load guard excludes bool (isinstance(int) and not isinstance(bool)), stricter than coordinator.py's existing isinstance(int) check it mirrors
- [Phase ?]: seed_quota_from_account() never touches used_today (migration day starts conservatively at 0, QUOTA-05/R5) while quota_exhausted_until merges via max()
- [Phase ?]: 31-03: hub timers arm unconditionally in async_setup (no coordinator-style enable-flag gate) — the hub always goes through async_setup, so no bare-construction test path needs gating
- [Phase ?]: 31-03: test_midnight_tick_resets_used_today drives hub._on_midnight() directly rather than via async_fire_time_changed, mirroring test_coordinator.py's precedent — _maybe_reset_used_today reads the real wall-clock UTC date, which a simulated HA time-fire does not advance
- [Phase ?]: 31-04: Deferred _next_midnight_utc removal to 31-05 -- gmail/imap coordinators still import it for their own untouched 429 branches
- [Phase ?]: 31-04: Added TEMPORARY coordinator.py compatibility shim (_quota_exhausted_until property + no-op _arm_quota_expiry_timer) redirecting gmail/imap inline forward-path reads/writes onto the shared hub -- reduced test blast radius from 121 to 2 failures without touching gmail_coordinator.py/imap_coordinator.py; removed by 31-05
- [Phase ?]: 31-05: left the 31-04 compatibility shim (_quota_exhausted_until property + no-op _arm_quota_expiry_timer) in coordinator.py in place -- 40 references remain across 4 test files this plan does not own (test_coordinator.py, test_store_migration.py, test_stage2_worker.py, test_binary_sensor.py); removing it is now a separately-scoped future cleanup
- [Phase ?]: 31-05: both per-account stale-quota-clear blocks in gmail/imap coordinators removed entirely -- the shared hub's own always-armed quota-expiry timer (31-03) is now the sole owner of clearing quota_exhausted_until
- [Phase ?]: EnqueueOutcome lives in const.py (not hub.py/coordinator.py) to avoid the hub<->coordinator circular import (D-02)
- [Phase ?]: Stage2Job.entry_id inserted before defaulted fields (prefetched_result/raw_msg_id) per D-11 — frozen/slots dataclass constraint
- [Phase ?]: Combined Task 1+Task 2 GREEN commit in 32-02 (attach/detach edits sit textually between the enqueue-path additions in hub.py) -- mirrors 30-01 precedent
- [Phase ?]: Global-bound test in 32-02 fills the queue across 8 distinct entry_ids x 8 jobs (not one account) to prove the global bound independently of the per-account cap
- [Phase ?]: 32-03: coord resolved once before the per-job try (not re-resolved per except branch); skip path uses if coord is not None guards inside try/except so the hub in-flight release + task_done stay in a single finally with no mypy type: ignore needed
- [Phase ?]: 32-04: RESEARCH.md's 11-site discard census was stale (actual 12) after intervening Phase 32 plans shifted line numbers -- verified via grep + AST-scoped body check instead of trusting stale line numbers
- [Phase ?]: 32-04: fixed 5 test files beyond declared files_modified (test_stage2_queue.py, test_stage2_worker.py, test_diagnostic_sensor.py) to keep the full suite green -- Wave 5 (32-05) assumes a green suite as its starting state
- [Phase 32]: Phase 32-05: async_start_stage2 split into async_setup_stage2_extractor (extractor-build only); async_stop_stage2/_async_stage2_worker/_log_stage2_worker_crash deleted entirely (D-04)
- [Phase 32]: Phase 32-05: CONF_QUEUE_MAXLEN retained but documented inert (superseded by fixed HUB_STAGE2_QUEUE_MAXLEN=64) — no user-facing migration
- [Phase 32]: Phase 32-05: multi-account no-leak/teardown/cap-fairness/no-dedup-bypass proven via REAL two-account async_setup_entry integration tests driving the live shared hub worker (test_multi_account.py), not synthetic bare-MagicMock coordinators
- [Phase ?]: Digest test uses one mocked Stage2Result per Assumption A2 -- inline fallback path is structurally single-shipment
- [Phase ?]: Phase 33-02: _run_inline_fallback() inserted between _enqueue_stage2 and _async_drain_pending_posts in coordinator.py (coordinator.py:1232-1593) -- new method's enqueue decision routes through _enqueue_stage2
- [Phase ?]: Phase 33-02: assert self._hub is not None added at the top of _run_inline_fallback (Rule 3 auto-fix) -- mirrors the existing pattern at other _hub call sites since the standalone method has no enclosing-function assert to inherit narrowing from
- [Phase 33]: Only the full-poll integration test proved genuine RED for 33-03 — direct-call tests against coord._run_inline_fallback() exercise the already-existing 33-02 shared method and pass immediately
- [Phase 33]: 33-03: schema-quarantine test loop mirrors the real poll's pre-loop seen/in-flight skip gate explicitly since _run_inline_fallback does not re-check that condition itself
- [Phase 33]: 33-04: multi-shipment-digest parity test feeds a body with 3 TN-shaped strings but mocks the extractor to return exactly ONE Stage2Result (Assumption A2) -- fallback path is structurally single-shipment, not multi-shipment iteration
- [Phase 33]: 33-04: instance-isolation test proves E2/D-05 structurally -- Gmail and Imap coordinators built with an identical string key hold distinct _seen_message_ids/_inflight_message_ids/_fallback_prefetch_cache/_stage2_inline_schema_failures dict objects, no key-namespacing code needed
- [Phase 33-05]: fail_under ratcheted 85 -> 90 against measured 95.52% global coverage; combined _run_inline_fallback + both call sites measured 100.0% (78/78 lines, D-02/R6)
- [Phase ?]: [Phase 34-01] test_phase32_hub_inflight_and_worker_present adapted to assert hub.enqueue() surface (not literal hub._stub_worker) — Phase 32-05 already deleted the stub attribute entirely (D-04) — Plan text anticipated this fallback explicitly
- [Phase ?]: [Phase 34-01] R-01 characterization confirms HA EntityRegistry re-parents config_entry_id on cross-entry re-registration for the pinned HA version (Assumption A1 validated) — De-risks 34-05 re-home mechanism before it is built
- [Phase 34]: 34-02: record_stage2_worker_success/detach() dismiss the consolidated hub notification unconditionally whenever the failing set empties (not gated on _hub_notification_active) -- relies on HA's async_dismiss being a safe no-op on an unknown/already-dismissed ID, same assumption already proven at coordinator.py:900-904
- [Phase 34]: 34-02: hub.stage2_queue_depth/stage2_pending are the sole public read surface onto Phase 32's _inflight/_queue -- the 34-03+ global queue sensor never touches hub privates directly
- [Phase 34]: 34-05: maybe_rehome_global_sensors picks survivors[0] as the new owner — SPEC only requires exactly-one-instance, not a specific tie-break rule
- [Phase 34]: 34-05: hub.async_shutdown() resolves both global entities' entity_ids via entity_registry.async_get_entity_id('sensor', DOMAIN, unique_id) reading the classes' _unique_id_suffix (lazy-imported) rather than hardcoding literal unique_id strings
- [Phase quick-260827-e6t]: DEFAULT_IMAP_SEARCH widened from 4 to 7 SUBJECT terms (added delivered/order/confirmed) with a correct RFC 3501 6-OR prefix tree — Real 17TRACK/COLAMY delivery-notification subject used delivered (past participle) which the present-tense-only delivery term did not match; hand-wrapped the 148-char literal as two implicitly concatenated single-quoted fragments to satisfy ruff's 100-column limit

### Roadmap Evolution

- Phase 7 added: Diagnostic Tooling — HA sensors with rich attributes for email scan/parse/tracking-number stats
- Phase 8 added: Parser Template Expansion — UPS/USPS/FedEx/Amazon/DHL template matchers (porting from Mail & Packages addon)
- Phase 9 added: IMAP Support & Multi-Account — IMAP connection method + multiple accounts per HA instance
- Phase 10 added (v1.1): Full-Window Scanning & Tracking Dedup — remove last_seen_message_id/last_imap_uid gates, add persisted tracking-number dedup
- Phase 11 added (v1.1): Activity Log & Debug Logging — per-email scan event ring buffer + comprehensive DEBUG-level logging
- Phase 12 added: Address tech debt
- Phase 13.1 inserted after Phase 13: Sensor Restore on Restart — coordinator.data not persisted means all sensors unavailable after restart (HA log audit finding) (URGENT)
- v1.3 Phases 15–22 added: OllamaClient foundation → extractor → config-flow → queue → worker → merge+quota guards → failure surface+diagnostics → README+e2e. Phases 16, 19, 20 flagged NEEDS RESEARCH.
- v1.5 Phases 29–34 added (2026-07-03): Hub Skeleton → Shared Dedup → Shared Budget → Shared Queue+Worker → IMAP Parity → Lifecycle Tests + Global Sensors

### Pending Todos

| File | Title | Area |
|------|-------|------|
| [2026-06-02-add-forwarded-email-sender-configuration.md](./todos/pending/2026-06-02-add-forwarded-email-sender-configuration.md) | Add forwarded email sender configuration | api |
| [2026-08-01-fix-gmail-ollama-fallback-missing-poison-message-quarantine.md](./todos/pending/2026-08-01-fix-gmail-ollama-fallback-missing-poison-message-quarantine.md) | Fix Gmail Ollama fallback missing poison-message quarantine | api |

### Blockers/Concerns

None — Phase 33 (IMAP Parity) complete and verified (4/4 must-haves). Ready to plan Phase 34 (Multi-Account Lifecycle Tests + Global Sensors), the last phase of v1.5.

### Quick Tasks Completed

| # | Description | Date | Commit | Status | Directory |
|---|-------------|------|--------|--------|-----------|
| 260424-d83 | Gmail API pivot — update REQUIREMENTS.md, ROADMAP.md, STATE.md, delete stale Shop app API docs | 2026-04-24 | n/a (gitignored) | [260424-d83-gmail-email-pivot](./quick/260424-d83-gmail-email-pivot/) |
| 260501-n8g | Fix Phase 07 code review findings: WR-01 coordinator diagnostics, WR-02 dead code, WR-03 regex, IN-01 docstring | 2026-05-01 | 502167f | [260501-n8g-fix-phase-07-code-review-findings-wr-01-](./quick/260501-n8g-fix-phase-07-code-review-findings-wr-01-/) |
| 260504-dgz | Commit simplify fixes and archive 4 stale todos | 2026-05-04 | 366388f | [260504-dgz-commit-simplify-fixes-and-archive-4-stal](./quick/260504-dgz-commit-simplify-fixes-and-archive-4-stal/) |
| 260504-k1b | Fix all PR #2 review findings — 5 critical bugs, 7 important issues, 3 missing tests, 7 doc fixes, 4 suggestions | 2026-05-04 | ef0d402 | [260504-k1b-fix-pr-review-findings](./quick/260504-k1b-fix-pr-review-findings/) |
| 260504-mxy | Fix 3 real CodeQL findings — unused _LOGGER in diagnostic_sensor.py, unused imports in test_multi_account.py | 2026-05-04 | 1eb2314 | [260504-mxy-fix-3-codeql-findings-remove-unused-logg](./quick/260504-mxy-fix-3-codeql-findings-remove-unused-logg/) |
| 260504-ci1 | Fix 3 CI failures — stale setup-python SHA, missing hacs/action tag, invalid homeassistant key in manifest.json | 2026-05-04 | 636fa41 | n/a |
| 260504-uik | Add diagnostics.py — HA quality-scale diagnostics platform with PollStats + last 10 shipments, credential redaction | 2026-05-05 | 3bd29b5 | [260504-uik-add-diagnostics-py-to-shop2parcel-integr](./quick/260504-uik-add-diagnostics-py-to-shop2parcel-integr/) |
| 260505-wl8 | Fix all critical and important issues from PR4 review (C1 order regex, C2 broad-scan gate, C3 ValueError crash, I1 html.escape, I2 carrier path, I3 debug logging, I4 carrier regex) | 2026-05-06 | 3fc8a47 | [260505-wl8-fix-all-critical-and-important-issues-fr](./quick/260505-wl8-fix-all-critical-and-important-issues-fr/) |
| 260506-dhd | Fix DEFAULT_GMAIL_QUERY label:inbox (archived emails excluded) and add rescan_window_days option (Gmail-only, 7-365d) with min(stored_ts, now-window) semantics for non-destructive lookback widening | 2026-05-06 | ac6d01a | [260506-dhd-fix-gmail-query-label-inbox-and-add-resc](./quick/260506-dhd-fix-gmail-query-label-inbox-and-add-resc/) |
| 260513-p01 | Write missing summary stubs for 01-01-PLAN.md and 06-03-PLAN.md to close the v1.0 archive artifact gap | 2026-05-13 | n/a (docs only) | [260513-p01-write-missing-summary-stubs-for-01-01-pl](./quick/260513-p01-write-missing-summary-stubs-for-01-01-pl/) |
| 260523-g8u | Reconcile ROADMAP and STATE for Phase 14 (4/4 complete 2026-05-20); audit WR-01..WR-04 — all already shipped on origin/main | 2026-05-23 | n/a (docs only) | [260523-g8u-reconcile-roadmap-and-state-for-phase-14](./quick/260523-g8u-reconcile-roadmap-and-state-for-phase-14/) |
| 260601-x94 | Fix wrong USPS Informed Delivery sender address in DEFAULT_GMAIL_QUERY | 2026-06-02 | cfb3567 | [260601-x94-fix-wrong-usps-informed-delivery-sender-](./quick/260601-x94-fix-wrong-usps-informed-delivery-sender-/) |
| 260618-iro | Fix 10 issues from PR 20 code review: I1 (cap notification dismiss), I2 (remove_entry missing dismiss), I3 (drain loop task_done), I4 (cancel-mid-success store flush), I5 (wrong error message in _split_and_coerce), I6 (MRG-04 tracking_number=None invariant), I7 (cap counter includes AlreadyAdded), S1 (BeautifulSoup error handling), S2 (snapshot-before-set_updated_data), S3 (empty URL UX note) | 2026-06-18 | d1b2a09 | [260618-iro-fix-10-issues-from-pr-20-code-review-i1-](./quick/260618-iro-fix-10-issues-from-pr-20-code-review-i1-/) |
| 260623-d9s | Add description attribute to all 7 diagnostic sensors so users can read what each sensor measures in the HA entity detail panel | 2026-06-23 | a9296fa | [260623-d9s-add-description-attrs-to-diagnostic-sensors](./quick/260623-d9s-add-description-attrs-to-diagnostic-sensors/) |
| 260623-f2k | Add 3 LLM performance diagnostic sensors: OllamaLatencySensor (avg/last/min/max ms), OllamaParseQualitySensor (fence-strip retry count + rate), Stage2ConsecutiveFailuresSensor (failure streak) | 2026-06-23 | 34385e0 | [260623-f2k-add-llm-performance-diagnostic-sensors](./quick/260623-f2k-add-llm-performance-diagnostic-sensors/) |
| 260701-l5r | Fix success-field gap in parcelapp async_add_delivery — honor documented API response contract by asserting `success is True` on 2xx and routing `success:false` to the ParcelAppAlreadyAddedError/ParcelAppInvalidTrackingError taxonomy | 2026-07-01 | a7f9382 | [260701-l5r-fix-the-success-field-gap-in-parcelapp-a](./quick/260701-l5r-fix-the-success-field-gap-in-parcelapp-a/) |
| 260701-loh | parcelapp delivery `description` = order-email summary (merchant + contents, e.g. "Target — Coffee maker") via new locked Ollama field `order_summary` with bespoke composition prompt; precedence `order_summary or order_name or tracking_number` at all 4 POST sites; Stage-1/no-LLM fallback preserved; CR-01 fix restores order_summary across HA restart. --full: research + plan-check + code-review + verify (8/8 passed) | 2026-07-01 | eb58360 | [260701-loh-make-parcelapp-delivery-description-a-sh](./quick/260701-loh-make-parcelapp-delivery-description-a-sh/) |
| 260703-mac | Fix startup LLM timeout: Gmail Ollama fallback gatekeeper ran inline LLM extraction during `async_config_entry_first_refresh` (inside HA's 300s bootstrap stage-2 window), so a Stage-1-miss backlog cancelled setup and downed the integration. Fix: (1) skip inline fallback on the first refresh via in-memory `_first_refresh_done` flag (skipped misses left un-marked → re-inspected next poll); (2) per-poll wall-clock budget `MAX_STAGE2_FALLBACK_INLINE_SECONDS=60.0` as defense-in-depth. Gmail-only; 10/poll cap unchanged. --full: research + plan-check + code-review (2 warnings fixed) + verify (5/5 passed). Verified. | 2026-07-03 | 9e7d8bc | [260703-mac-fix-startup-llm-problem-defer-inline-oll](./quick/260703-mac-fix-startup-llm-problem-defer-inline-oll/) |
| 260720-mt8 | Fix reauth confirmation screen showing raw untranslated placeholder `[%key:common::config_flow::abort::reauth_successful%]` instead of "Re-authentication was successful". Root cause: custom/HACS integrations serve `translations/en.json` to the frontend at runtime, but HA core only resolves `[%key:...%]` common-string references at its own build time — never for custom integrations — so the placeholder rendered verbatim. Resolved all 6 unresolved `config.abort` references (reauth_successful + 5 OAuth aborts) to literal English text in both `strings.json` and `translations/en.json` (kept byte-identical); added 2 regression tests. TDD RED→GREEN; 17/17 tests pass. | 2026-07-20 | c7fb96f (shared w/ concurrent 34-02 commit — verified byte-identical diff, see 260720-mt8-SUMMARY.md) | [260720-mt8-i-got-the-following-text-when-i-reauthed](./quick/260720-mt8-i-got-the-following-text-when-i-reauthed/) |
| 260806-i5r | Client-side follow-up for `gmail-query-drops-emails`: re-implemented keyword narrowing as local code (`build_keyword_matcher`/`matches_keyword_filter` in `api/email_parser.py`, applied after Gmail message fetch and before `EmailParser.parse()`, fail-open on operator-only/blank queries); added Gmail-side `MAX_GMAIL_MESSAGES_PER_POLL=100` per-poll cap (newest-first front slice, applied after seen/in-flight filter, new `last_poll_emails_capped` diagnostics counter); removed `gmail_query` from the options UI entirely while stored per-entry values keep driving the local filter untouched (no migration, D-04). --full: research + plan-check + code-review + verify. Code review found the new per-poll cap only bounded `messages.get()` fetches, not the underlying `messages.list()` pagination (still unbounded on a busy mailbox / debug mode's 365-day window) — fixed in a follow-up round: bounded pagination to `_MAX_LIST_PAGES=5` (~500 raw IDs/poll) in `api/gmail_client.py`, corrected an `EmailsScannedSensor` description that falsely claimed IMAP now filters locally too (IMAP unchanged, still server-side via `CONF_IMAP_SEARCH`), refreshed a stale `DEFAULT_GMAIL_QUERY` comment. Fixed 56 pre-existing test failures + 13 further schema-validation call sites as fallout of the UI removal. TDD RED→GREEN ×4; full suite 1154 passed, 2 pre-existing live-service skips; ruff/mypy clean. Verified. Redeployment to live HA and closing the debug session remain outstanding. | 2026-08-06 | 7bb5202 (+ 2b639a7, 73e3f1a, 0df2280, 68b20fd, ace338c, 8e8e59a, 36f29fd, 380eb08) | [260806-i5r-client-side-follow-up-for-gmail-query-fi](./quick/260806-i5r-client-side-follow-up-for-gmail-query-fi/) |
| 260806-v2j | Enhance carrier-format-gate rejection DEBUG lines in `coordinator.py` (LOG-01..05): 3 of 4 rejection sites (`_run_inline_fallback`, worker pre-POST re-gate, MRG-04 promotion loop) now log `subject=%r sender=%r` sourced from the already-in-scope `meta`/`job.meta` dict — same source `_emit_scan_event` uses, zero-cost read. The 4th (Stage-2 drain re-gate) has no email metadata in scope (`_pending_posts` stores `ShipmentData` only, no re-fetch performed) so it logs `storage_key=%s` instead, documented inline. `record_carrier_format_rejection()` signature/call sites/sensor behavior unchanged. 4 new caplog regression tests pin both DEBUG presence and no-PII-above-DEBUG absence. Full suite 1170 passed, 2 pre-existing live-service skips; ruff/mypy clean. `gmail_coordinator.py`/`imap_coordinator.py` deliberately untouched (out of scope by task name) — recorded as an optional symmetric follow-up. | 2026-08-06 | 44d18d3, 6db4894 | [260806-v2j-enhance-carrier-format-gate-rejection-de](./quick/260806-v2j-enhance-carrier-format-gate-rejection-de/) |
| 260807-qw1 | Implement user-configurable sender-exclusion filter (spike 027): `extract_sender_domain`/`build_sender_exclusion_matcher` (exact-domain-match only, fail-open on empty config) in `api/email_parser.py`; options-flow add/remove UI (`CONF_SENDER_EXCLUSIONS`) mirroring the existing custom-fields pattern; wired into Gmail (before local keyword filter) and IMAP coordinators at the earliest point sender metadata is known, using `_mark_inflight` (not `_mark_message_seen`) so a config change takes effect immediately (D-05). USPS Informed Delivery structurally guaranteed non-excludable (exact match only). --validate: plan-checked + independently verified, 13/13 must-haves. Full suite 1238 passed, 2 pre-existing live-service skips; ruff/mypy clean. | 2026-08-07 | f55c1ac, 34b0f13, 50d7108 | Verified | [260807-qw1-implement-user-configurable-sender-exclu](./quick/260807-qw1-implement-user-configurable-sender-exclu/) |
| 260807-usps-dhl-check | Investigated spike round-4 candidates (USPS digest structural sender extraction, DHL carrier support) requested as "recent spike work" — found both already shipped in Phase 36 (2026-07-24); `spike-findings-shop2parcel` skill docs are stale on this point (still read "validated, not yet implemented"). No code change; superseded by 260807-tpu below, which the DHL finding led directly into. | 2026-08-07 | n/a (investigation only) | n/a | n/a |
| 260807-tpu | Made the shared carrier-format gate carrier-aware (`validate_carrier_format(value, carrier_name=None)`, additive OR-widening, DHL bare-digit shape reuses `_dhl_looks_like_tracking`, never a switch) and wired it into MRG-04 (`stage1.carrier_name`, option a — never the LLM's own carrier claim, anti-circularity proven by test) plus all four production pre-POST gates (worker: `job.shipment.carrier_name`; drain: pending shipment's own carrier, bounded-residual-risk documented inline; Gmail/IMAP inline: pure Stage-1 `shipment.carrier_name`). Investigation found DHL was dead-ended at every production POST path since Phase 36 shipped — this un-blocks DHL end-to-end. `_TRACKING_PATTERNS` unchanged (R5 hole stays closed). TDD RED→GREEN ×3 tasks; full suite 1238 passed, 2 pre-existing live-service skips; ruff/mypy clean. --validate: plan-checked + independently verified, 9/9 must-haves. | 2026-08-07 | eab0c9a, 1117867, 93fd9cc | Verified | [260807-tpu-make-mrg-04-s-carrier-format-gate-carrie](./quick/260807-tpu-make-mrg-04-s-carrier-format-gate-carrie/) |
| 260808-074 | Refreshed the `spike-findings-shop2parcel` skill (docs-only, 3 tasks): `references/us-carrier-coverage.md` now states DHL shipped in Phase 36 (2026-07-24), replaces the open local-vs-shared-validator choice with the resolved 260807-tpu reversal history, and adds a What-to-Avoid entry recording the two-week production dead-end incident; `references/usps-digest-multi-shipment.md` and `references/sender-filtering.md` reframe `_extract_usps_shippers` (Phase 36) and the sender-exclusion matcher/UI/wiring (260807-qw1) as live production code; `SKILL.md` frontmatter description, a new sixth `<context>` paragraph ("Implementation round, NOT a spike round"), 4 `<requirements>` bullets, and 3 `<findings_index>` cells updated to shipped status — Processed Spikes list (26 entries) left byte-identical, no spike number invented for quick-task work. All plan verification gates passed; zero changes under `custom_components/`/`tests/`. | 2026-08-08 | 38cded0, e9f7879, e793ccb | Verified | [260808-074-refresh-spike-findings-shop2parcel-skill](./quick/260808-074-refresh-spike-findings-shop2parcel-skill/) |
| 260827-e6t | Fixed a confirmed production gap: `DEFAULT_IMAP_SEARCH` widened from 4 to 7 SUBJECT terms (added `delivered`, `order`, `confirmed` to the existing `shipped`/`tracking`/`delivery`/`shipment`), closing the real 17TRACK/COLAMY delivery-notification miss where a subject ending "...has been delivered." was silently skipped because `delivered` (past participle) was not covered by `delivery`. Rebuilt as a correct RFC 3501 6-OR left-nested prefix tree (N-1 rule for 7 keys); hand-wrapped the 148-char literal as two 74-char implicitly concatenated single-quoted fragments to stay under ruff's 100-column limit. 3 new regression tests pin term coverage, exact OR/SUBJECT/total token counts, and a recursive-descent prefix-OR-tree parse-validity check. README.md and docs/CONFIGURATION.md synced to the same byte-identical string. TDD RED→GREEN; full suite 1242 passed, 2 pre-existing live-service skips; ruff clean (scoped to project files). | 2026-08-27 | 1e63ab7, c864d8a, 7dddcd8 | [260827-e6t-fix-imap-subject-search-query-gap-in-sho](./quick/260827-e6t-fix-imap-subject-search-query-gap-in-sho/) |

## Performance Metrics

**Velocity:**

- Total plans completed: 84
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 02 | 3 | - | - |
| 04 | 3 | - | - |
| 05 | 2 | - | - |
| 06 | 4 | - | - |
| 07 | 3 | - | - |
| 08 | 3 | - | - |
| 9 | 5 | - | - |
| 10 | 3 | - | - |
| 11 | 3 | - | - |
| 12 | 3 | - | - |
| 15 | 4 | - | - |
| 16 | 3 | - | - |
| 17 | 4 | - | - |
| 18 | 2 | - | - |
| 19 | 2 | - | - |
| 20 | 3 | - | - |
| 22 | 2 | - | - |
| 21 | 3 | - | - |
| 23 | 4 | - | - |
| 29 | 2 | - | - |
| 30 | 3 | - | - |
| 35 | 4 | - | - |
| 31 | 5 | - | - |
| 33 | 5 | - | - |
| 34 | 6 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 02-api-clients P01 | 9 | 2 tasks | 9 files |
| Phase 02-api-clients P02 | 4 | 2 tasks | 5 files |
| Phase 02-api-clients P03 | 9 | 1 tasks | 2 files |
| Phase 05-sensor-entities P01 | 10 | 3 tasks | 4 files |
| Phase 15-ollamaclient-foundation P03 | 7 | 3 tasks | 2 files |
| Phase 15-ollamaclient-foundation P04 | 4 | 3 tasks | 2 files |
| Phase 22 P01 | 15 | 2 tasks | 1 files |
| Phase 22 P02 | 2 | 1 tasks | 1 files |
| Phase 21 P01 | 25 | 2 tasks | 7 files |
| Phase 21 P02 | 26 | 2 tasks | 4 files |
| Phase 21 P03 | 8m | 2 tasks | 5 files |
| Phase 29 P01 | 25min | 3 tasks | 3 files |
| Phase 29 P02 | 15min | 2 tasks | 2 files |
| Phase 30 P01 | 10min | 2 tasks | 2 files |
| Phase 30 P02 | 15min | 2 tasks | 2 files |
| Phase 30 P03 | 45min | 3 tasks | 14 files |
| Phase 35 P01 | 20min | 2 tasks | 2 files |
| Phase 35 P02 | 20min | 2 tasks | 2 files |
| Phase 35 P03 | 20min | 2 tasks | 2 files |
| Phase 35 P04 | 15min | 2 tasks | 2 files |
| Phase 31 P01 | 15min | 2 tasks | 3 files |
| Phase 31 P02 | 5min | 2 tasks | 2 files |
| Phase 31 P03 | 15min | 2 tasks | 2 files |
| Phase 31 P04 | 35min | 3 tasks | 7 files |
| Phase 31 P05 | 25min | 2 tasks | 6 files |
| Phase 32 P01 | 15min | 3 tasks | 7 files |
| Phase 32 P02 | 25min | 2 tasks | 2 files |
| Phase 32 P03 | 25min | 2 tasks | 2 files |
| Phase 32 P04 | 35min | 3 tasks | 6 files |
| Phase 32 P05 | 50min | 3 tasks | 14 files |
| Phase 33 P01 | 10min | 2 tasks | 1 files |
| Phase 33 P02 | 25min | 2 tasks | 2 files |
| Phase 33 P03 | 20min | 2 tasks | 2 files |
| Phase 33 P04 | 20min | 2 tasks | 1 files |
| Phase 33-imap-parity P05 | 8min | 2 tasks | 1 files |
| Phase 34 P01 | 10min | 2 tasks | 2 files |
| Phase 34 P02 | 20min | 2 tasks | 2 files |
| Phase 34 P05 | 30min | 3 tasks | 4 files |
| Phase 34 P06 | 35min | 2 tasks | 2 files |

## Deferred Items

Items acknowledged and deferred at milestone close on 2026-05-04:

| Category | Item | Status |
|----------|------|--------|
| uat_gap | Phase 02: 02-HUMAN-UAT.md — 2 pending scenarios | partial |
| uat_gap | Phase 03: 03-HUMAN-UAT.md — 3 pending scenarios | partial |
| uat_gap | Phase 04: 04-HUMAN-UAT.md — 3 pending scenarios | partial |
| uat_gap | Phase 05: 05-HUMAN-UAT.md — 2 pending scenarios | partial |
| uat_gap | Phase 07: 07-HUMAN-UAT.md — 1 pending scenario | partial |
| verification_gap | Phase 02: 02-VERIFICATION.md | human_needed |
| verification_gap | Phase 03: 03-VERIFICATION.md | human_needed |
| verification_gap | Phase 04: 04-VERIFICATION.md | human_needed |
| verification_gap | Phase 05: 05-VERIFICATION.md | human_needed |
| verification_gap | Phase 06: 06-VERIFICATION.md | human_needed |
| quick_task | 260424-001-shop-app-api-docs | missing summary |

All UAT and verification gaps require live HA instance testing (hardware-dependent). Deferred — not blocking milestone close.

Items acknowledged and deferred at v1.1 milestone close on 2026-05-17:

| Category | Item | Status |
|----------|------|--------|
| debug_session | no-new-numbers-email-search-2026-05-17 — 3 stuck message IDs loop with "already added" 400s on every HA restart; dedup store persistence | root_cause_identified |
| tech_debt | CONF_RESCAN_WINDOW_DAYS comment reads "Gmail-only" but IMAP also uses it | minor |
| tech_debt | ACTLOG-02 test gap: no_html_body outcome has no ACTLOG assertion | minor |
| tech_debt | DBG-01..04 have no automated caplog test coverage | minor |
| tech_debt | ParcelAppTransientError/InvalidTrackingError paths don't emit scan events | minor |

Items acknowledged and deferred at v1.2 milestone close on 2026-06-05:

| Category | Item | Status |
|----------|------|--------|
| debug_session | no-new-numbers-email-search-2026-05-17 — RESOLVED by Phase 13 (already-added 400s now treated as idempotent success; dedup store no longer writes rejected TNs) | resolved |
| debug_session | oauth-token-rejected-after-update | root_cause_identified |
| todo | add-forwarded-email-sender-configuration — future capability, not blocking v1.2 | deferred |
| seed | SEED-001-debug-dry-run-mode — SHIPPED in Phase 14 (v1.2); seed can be closed | resolved |
| seed | SEED-002-activity-log-human-readable-poll-summary — future feature | dormant |
| quick_task | 17 quick tasks with missing summary files — administrative debt; work is completed | missing_summary |

Note: UAT gaps (phases 02-07) and verification gaps (phases 02-06) are continuing deferrals from v1.0/v1.1 (hardware-dependent live HA testing).

Items acknowledged and deferred at v1.5 milestone close on 2026-07-28:

| Category | Item | Status |
|----------|------|--------|
| debug_session | knowledge-base — false positive, not a debug session (GSD debugger's resolved-session index, no status frontmatter) | not_applicable |
| debug_session | only-two-emails-sent-to-llm — primary question RESOLVED (correct dedup behavior); surfaced a real latent bug (below) | resolved_with_followup |
| tech_debt | Gmail Ollama fallback path (gmail_coordinator.py:454-472) doesn't quarantine permanently-failing messages like the main worker path does (STAGE2_MSG_QUARANTINE_THRESHOLD) — a poison Stage-1-miss email can flood logs and consume MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL every cycle during an Ollama outage. Fix direction: mirror worker quarantine or mark seen/inflight on repeated fallback failure. | open_followup |
| debug_session | oauth-token-rejected-after-update | root_cause_identified |
| debug_session | stage2-queue-consumer-stall — RESOLVED 2026-06-22 (quota gate moved before extractor, stage2_quota_skipped_total added, 676 tests passing); status flag never flipped from awaiting_human_verify | resolved |
| uat_gap | Phase 22: 22-UAT.md — 1 pending scenario | partial |
| verification_gap | Phase 18: 18-VERIFICATION.md | human_needed |
| verification_gap | Phase 26: 26-VERIFICATION.md | human_needed |
| verification_gap | Phase 32: 32-VERIFICATION.md — WR-01 reload-race window open human-decision item, harm bounded by is_submitted() idempotency guard | human_needed |
| todo | add-forwarded-email-sender-configuration — future capability, not blocking v1.5 | deferred |
| seed | SEED-002-activity-log-human-readable-poll-summary — future feature | dormant |
| quick_task | 16 quick tasks with missing summary files — administrative debt; work is completed | missing_summary |

Note: UAT/verification gaps continue the hardware-dependent live HA testing deferral pattern from v1.0-v1.2. The Gmail Ollama fallback quarantine gap is genuinely open work, not administrative debt — worth a follow-up quick task or phase.

## Session Continuity

**Resume file:** None

Last session: 2026-08-27T14:27:59.679Z
Stopped at: Completed quick task 260827-e6t — widened `DEFAULT_IMAP_SEARCH` from 4 to 7 SUBJECT terms (added `delivered`, `order`, `confirmed`), rebuilt as a correct RFC 3501 6-OR left-nested prefix tree, closing a confirmed production gap where a real 17TRACK/COLAMY delivery-notification email ("...has been delivered.") was silently skipped because `delivered` wasn't covered by `delivery`. README.md and docs/CONFIGURATION.md synced. TDD RED→GREEN; full suite 1242 passed; ruff clean.
Next action: `/gsd-new-milestone` to define v1.6 scope, or continue with ad-hoc spike-driven phases (Phase 37+) if no new milestone is started yet. Outstanding: redeploy 260806-i5r, 260807-qw1, and 260807-tpu to the live HA instance; close .planning/debug/gmail-query-drops-emails.md once verified live. Optional follow-up: symmetric subject/sender enrichment for the sibling rejection log sites in gmail_coordinator.py:807 and imap_coordinator.py:532.

## Operator Next Steps

- `/gsd-new-milestone` — start v1.6: requirements gathering → roadmap creation
- Alternatively: `git tag -d v1.6.0-rc1 && git tag v1.6.0 origin/main && git push` once rc1 is confirmed stable, to cut the final v1.6.0 release
- Follow-up worth tracking: Gmail Ollama fallback path missing poison-message quarantine (see Deferred Items above) — candidate for a v1.6 phase or standalone quick task
- Full sequence: 29 ✓ → 30 ✓ → 31 ✓ → 32 ✓ → 33 ✓ → 34 ✓ (v1.5 complete) → 35 ✓ → 36 ✓ (independent phases complete)
