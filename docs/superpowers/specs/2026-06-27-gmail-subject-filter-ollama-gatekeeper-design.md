# Design: Subject-only Gmail filter + hybrid Ollama gatekeeper

**Date:** 2026-06-27
**Status:** Approved (design); pending implementation
**Component:** `custom_components/shop2parcel`

## Problem

The deployed integration loads and polls Gmail successfully every 5 minutes, but
**every poll returns 0 messages**, so nothing is ever extracted or forwarded to
parcelapp. Root cause: the default Gmail query is too restrictive. It anchors on a
fixed list of sender addresses and uses an awkward two-arm `OR` structure whose
Gmail operator precedence does not match intent. Confirmed empirically: a plain
subject-keyword search in the Gmail web UI returns the user's shipping mail, while
the current query returns nothing.

Current default (`const.py:20-28`):

```
(from:no-reply@shopify.com OR from:mcinfo@ups.com OR
 from:USPSInformeddelivery@email.informeddelivery.usps.com OR from:USPSPackageTracker@usps.com OR
 from:TrackingUpdates@fedex.com)
 subject:(shipped OR delivered OR tracking OR package)
 OR
 -label:spam
 subject:(tracking OR shipped OR shipment OR delivery OR parcel)
```

## Goal

1. Loosen the Gmail query to a **subject-only** filter so real shipment mail is
   actually retrieved, regardless of sender.
2. Because a broad subject filter also pulls in non-shipment mail (newsletters,
   order confirmations, marketing), add a **hybrid rejection gate** so only genuine
   shipments are forwarded.
3. Avoid the local-LLM cost of re-judging the same emails on every poll.

## Non-goals

- Re-introducing sender allow-lists.
- Changing the parcelapp POST path, quota accounting, or the 20/day limit.
- Reworking Stage-1 template/regex parsing internals.

## Background: current pipeline

(From repo exploration — file:line references.)

- **Gmail query**: `DEFAULT_GMAIL_QUERY` (`const.py:20`); user-overridable option
  `CONF_GMAIL_QUERY` (`const.py:7`), surfaced in `options_flow.py`. The `after:`
  filter is appended by `build_incremental_query(base_query, rescan_window_days)`
  (`api/gmail_client.py:93-103`) as `after:<now − rescan_window_days*86400>`.
  `DEFAULT_RESCAN_WINDOW_DAYS = 30` (`const.py:34`); the deployed entry uses 14.
- **Stage-1** (template/regex email parser) runs first. If it finds **no** tracking
  number, the email is dropped and **never reaches Ollama**.
- **Stage-2 (Ollama)** runs only *after* Stage-1 found a tracking number, to refine
  / add fields. Prompt in `extractors/ollama_extractor.py:164-228`; client in
  `api/ollama_client.py`. The prompt already instructs Ollama to return `null` for
  absent fields.
- **Merge** `merge_llm_authoritative()` (`merge.py:45-129`): Stage-1 wins for
  `tracking_number`; Stage-2 values validated by
  `_SANITY_RE = ^[A-Za-z0-9\- ]{6,40}$` (`merge.py:42`).
- **No skip-POST gate**: `_async_process_stage2_job` (`coordinator.py:1129-1328`)
  always POSTs; it assumes Stage-1 succeeded.
- **Volume controls**: `MAX_STAGE2_POSTS_PER_POLL = 5` (`const.py:93`);
  `DEFAULT_QUEUE_MAXLEN = 32` (`const.py:161`). Gmail listing paginates through all
  pages (no hard message cap) — `api/gmail_client.py:47-80`.
- **Dedup today**: by tracking number (`submitted_tracking_numbers` in
  `.storage/shop2parcel.<entry_id>`).

## Design

### 1. Gmail query — subject-only default

Replace `DEFAULT_GMAIL_QUERY` (`const.py:20-28`) with:

```
subject:(tracking OR shipped OR shipment OR delivery OR delivered OR parcel OR package OR order)
```

- No `from:` anchors; no `-label:spam` (Gmail already excludes Spam and Trash from
  normal search — `-label:spam` is a no-op).
- `build_incremental_query` continues to append `after:<now − rescan_window_days>`
  unchanged.
- This changes the **default** only. Existing entries keep their stored
  `gmail_query` option; the new default applies to fresh installs and to anyone who
  clears the field. Update the options-flow help text to describe the new default.

### 2. Seen-message-ID cache (new dedup layer)

- New persisted field `seen_message_ids` in `.storage/shop2parcel.<entry_id>`:
  an **insertion-ordered** collection of Gmail message IDs.
- Holds **both** outcomes: IDs that were successfully forwarded **and** IDs that the
  Ollama fallback rejected as non-shipment. Once an ID is in the cache it is never
  processed again.
- **Bounded to 10,000**; on overflow, evict the oldest IDs first (FIFO).
- **Poll start**: filter the Gmail result list to IDs **not** present in the cache;
  only those are processed. Every ID actually processed this poll (any outcome) is
  added to the cache.
- The existing `submitted_tracking_numbers` set is **retained** — it serves a
  different purpose (preventing two *different* emails about the *same* shipment from
  double-posting). Message-ID dedup ≠ shipment dedup; both are required.

### 3. Hybrid extraction flow (per new, unseen email)

1. **Stage-1** template/regex parser runs.
   - Valid tracking number found → existing path: Ollama Stage-2 refine → merge
     (Stage-1 authoritative) → POST.
2. **Stage-1 finds nothing** → **Ollama fallback gatekeeper**:
   - Send the email to Ollama and build a `ShipmentData` from its output (Ollama is
     authoritative here — there is no Stage-1 result to win).
   - Validate `tracking_number` with the existing `_SANITY_RE`.
   - Valid → POST. Null / invalid → **reject** (no POST). Either way the message ID
     is added to the seen-cache.
3. **New skip-POST gate** (closes the existing gap): if, after extraction/merge,
   `tracking_number is None`, skip the POST and emit a `stage2_no_data` skip reason
   instead of assuming Stage-1 succeeded. This protects both the fallback path and
   any future caller of the merge path.

### 4. Volume guards

- `MAX_STAGE2_POSTS_PER_POLL = 5` — unchanged (POST/quota cap).
- **New** per-poll cap on Ollama **fallback extractions**: **10** per poll. This is a
  safety valve for a large first-poll backlog from the 14-day window. Emails not
  reached this poll are simply **not** added to the seen-cache, so the next poll
  picks them up. Once the backlog drains, steady-state polls see only a few genuinely
  new emails and the cap is effectively never hit.
- Add a new constant, e.g. `MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL = 10`.

## Data flow (new)

```
Gmail poll
  └─ list messages matching subject filter + after:<window>
       └─ drop IDs already in seen_message_ids
            └─ for each remaining (unseen) message, up to fallback/POST caps:
                 ├─ Stage-1 parse
                 │    ├─ tracking found ─→ Ollama refine ─→ merge ─→ POST ─→ add ID to seen
                 │    └─ no tracking ────→ Ollama fallback gatekeeper
                 │                           ├─ valid tracking ─→ POST ─→ add ID to seen
                 │                           └─ null/invalid ──→ reject (no POST) ─→ add ID to seen
                 └─ (skip-POST gate: tracking_number is None ⇒ no POST, reason=stage2_no_data)
```

## Error handling

- Ollama unreachable / timeout during a fallback extraction: treat as **transient**
  — do **not** add the ID to the seen-cache (so it is retried next poll), do not POST.
  Log at debug/warning consistent with existing Ollama error handling.
- Cache persistence failure: non-fatal; processing continues (matches existing
  debounced-save behavior).
- parcelapp 429 / quota: unchanged — existing backoff and `MAX_STAGE2_POSTS_PER_POLL`
  apply.

## Testing

- `tests/api/test_gmail_client.py` — new default query shape; `after:` still appended.
- `tests/test_options_flow.py` — default wiring / help text updated.
- New: **seen-ID cache** — add, skip-if-seen, FIFO eviction at 10,000, persistence
  round-trip via the store.
- New: **Ollama fallback gatekeeper** — Stage-1 miss → Ollama valid tracking → POST +
  ID cached; Stage-1 miss → Ollama null/invalid → rejected, no POST, ID cached.
- New: **skip-POST gate** — `tracking_number is None` after merge ⇒ no POST,
  `stage2_no_data` reason.
- New: **per-poll fallback cap (10)** — 25 unseen Stage-1-misses ⇒ exactly 10 Ollama
  fallback calls this poll; remainder not cached and picked up next poll.
- New: **transient Ollama failure** — ID not added to seen-cache; retried next poll.

## Constants summary

| Constant | Value | Status |
|---|---|---|
| `DEFAULT_GMAIL_QUERY` | `subject:(tracking OR shipped OR shipment OR delivery OR delivered OR parcel OR package OR order)` | changed |
| `SEEN_MESSAGE_IDS_MAXLEN` (name TBD in impl) | 10000 | new |
| `MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL` (name TBD in impl) | 10 | new |
| `MAX_STAGE2_POSTS_PER_POLL` | 5 | unchanged |
| `DEFAULT_QUEUE_MAXLEN` | 32 | unchanged |

## Open questions

None blocking. Exact new constant identifiers are an implementation detail.
