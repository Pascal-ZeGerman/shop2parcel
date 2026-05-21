# Shop2Parcel — Retrospective

---

## Milestone: v1.1 — Debug-Ready

**Shipped:** 2026-05-17
**Phases:** 3 (10–12) | **Plans:** 9

### What Was Built

- Full-window email scanning: removed last_seen_message_id/last_imap_uid skip gates — every poll scans all emails in the configured window
- Tracking-number dedup: `Shop2ParcelStore` v2 with `OrderedDict` (LRU cap 1000) replaces message-ID dedup; persists across HA restarts
- Per-email activity log: 50-event ring buffer with full scan metadata; exposed via `ActivityLogSensor` and diagnostics download
- Comprehensive debug logging: `_LOGGER.debug()` across coordinator, parsers, all API clients — HA native debug toggle gives full visibility
- Coordinator split: monolithic 926-line `coordinator.py` → base + `GmailCoordinator` + `ImapCoordinator`
- v1.1.1 patch: href fallback for FedEx/UPS/USPS parsers, IMAP description fallback, 3 code review fixes

### What Worked

- **Audit before close**: the v1.1 audit accurately identified tech debt and confirmed requirements coverage — running it first saved ambiguity at close
- **Phase 12 as a tech-debt cleanup phase**: batching review findings + coordinator split + manifest bump into a single patch phase was clean and effective
- **Debug session tracking**: surfaced two real bugs (FedEx href, IMAP description) that were fixed before close
- **TDD rhythm**: red/green pattern in Phase 11 ring buffer kept implementation clean and verified

### What Was Inefficient

- **Three stuck message IDs**: dedup store persistence issue left unresolved — was diagnosed but no fix shipped; will carry to v1.2
- **MILESTONES.md v1.0 entry**: contained raw tool output instead of clean prose — required manual cleanup at v1.1 close
- **Debug logging visibility**: HA logs only show WARNING+ by default, so most useful diagnostic output was invisible until user enables debug mode — a debug dry-run mode (SEED-001) would have been faster to diagnose

### Patterns Established

- Debug sessions (`/gsd:debug`) are resolved or acknowledged before milestone close — not silently ignored
- Coordinator subclass pattern: one subclass per connection method (`GmailCoordinator`, `ImapCoordinator`), base class handles dedup + parcelapp POST
- Activity log architecture: deque in `PollStats`, sensor reads last N events, diagnostics serializes full buffer

### Key Lessons

- `_parse_fedex` (and carrier parsers generally) need href fallback — tracking numbers in Delivery Manager emails live in hrefs, not text
- Dedup store save errors are swallowed silently by `except Exception` in `coordinator.py:228` — need explicit error visibility or the dedup breaks invisibly across restarts
- IMAP `description` field must always have a fallback — direct carrier emails have no `order_name`

---

## Cross-Milestone Trends

| Metric | v1.0 | v1.1 |
|--------|------|------|
| Phases | 9 | 3 (+12 patch) |
| Plans | 29 | 9 |
| Tests | 156+ | 265 |
| LOC (integration) | ~6,800 | ~9,600 (incl. tests) |
| Known deferred items at close | 11 | 5 |
| Timeline | ~4 weeks | 13 days |
