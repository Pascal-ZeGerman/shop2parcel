# Sender-Domain Sweep Audit — Runbook

## What this is and why it exists

`DEFAULT_GMAIL_QUERY` and `DEFAULT_IMAP_SEARCH` in
`custom_components/shop2parcel/const.py` are pure subject-keyword searches —
by construction, a keyword search can never surface the mail it is missing,
it only ever gets extended reactively one noticed-by-the-user gap at a time
(exactly what happened with quick task
`../../260827-e6t-fix-imap-subject-search-query-gap-in-sho/`). This tooling
sweeps each mailbox for **all** mail from senders already proven relevant —
domains behind shipments already captured in `persisted_shipments` — over
the last 12 months, regardless of subject, then diffs against already-seen
state. See
`../../../notes/2026-09-06-search-term-gap-finding-methodology.md` for the
full reasoning and
`../../../todos/pending/2026-09-06-sender-domain-sweep-audit-for-missed-shipment-emails.md`
for the originating todo.

## Prerequisites

1. **Sandbox / network egress (read this first).** Every step that touches a
   mailbox needs outbound network egress — IMAP TLS to `imap.ionos.de` /
   `imap.web.de`, and HTTPS to `oauth2.googleapis.com` and
   `gmail.googleapis.com`. The default agent sandbox denies this, and **a
   subagent has no path to escalate that with the human** — running the
   sweep is the session lead's job, not an executor's. Run it either:
   - interactively, as the human, in a normal terminal, or
   - as the session lead with an explicit sandbox-disabled invocation.
2. **Read access to the three storage paths.** The HA container typically
   writes `.storage/*` as `nobody:nogroup` — a `PermissionError` on the
   first run is a plausible and expected first failure; it means re-running
   as a user that can read those files (e.g. via `sudo -u` read access, or
   copying the files somewhere readable first — do not edit them in place).
3. **A Google OAuth refresh token still valid in the config entry.** If it
   has been revoked, Phase 1 (seed derivation) for the Gmail account will
   fail with a clear "refresh token is expired or revoked" message — see
   Troubleshooting.
4. **`.venv/bin/python` or any Python 3.11+.** The script imports nothing
   outside the standard library — that was a deliberate design decision so
   the orchestrator can run it under a different interpreter than the repo's
   own venv, with no package-install step, ever.

## The two-step run

**Step 1 — seeds only.** Print and eyeball the derived seed domains before
authorising the much larger full sweep:

```bash
.venv/bin/python \
  .planning/quick/260906-x62-sender-domain-sweep-audit-for-missed-shi/scripts/sweep_run.py \
  --seeds-only
```

**Step 2 — full run.** Sweeps every seed domain on all 3 accounts over the
last 12 months and writes the candidate report:

```bash
.venv/bin/python \
  .planning/quick/260906-x62-sender-domain-sweep-audit-for-missed-shi/scripts/sweep_run.py
```

Output path (default):

```
/home/pascal/Vibe-Coding/HomeAssistant/Shop2Parcel/.planning/quick/260906-x62-sender-domain-sweep-audit-for-missed-shi/candidates-report.md
```

## Flag reference

| Flag | Default | Purpose |
|------|---------|---------|
| `--self-check` | off | Offline verification mode — exercises argparse, window math, the IMAP control-char guard, and a full `render_report` round-trip with zero network calls. Prints `SELF-CHECK OK`. |
| `--seeds-only` | off | Stop after Phase 1 (seed derivation); print the derived seed domains and their provenance, then exit without sweeping. |
| `--months` | `12` | Lookback window applied on both transports (Gmail `after:`, IMAP `SINCE`). |
| `--out` | `<task-dir>/candidates-report.md` | Output path for the markdown candidate report. |
| `--extra-domain` | none (repeatable) | Add a domain to the seed list that wasn't derived from stored shipments (e.g. a sender you know is relevant but hasn't shipped anything yet). |
| `--exclude-domain` | none (repeatable) | Extend `DEFAULT_EXCLUDED_DOMAINS` (the mailbox-owner/freemail domains that would otherwise turn the sweep into a full-mailbox dump). |
| `--storage-dir` | `/home/pascal/homeassistant/config/.storage` | Directory holding `shop2parcel.<entry_id>` store files. |
| `--config-entries` | `/home/pascal/homeassistant/config/.storage/core.config_entries` | Path to HA's config entries store. |
| `--app-credentials` | `/home/pascal/homeassistant/config/.storage/application_credentials` | Path to HA's application credentials store (Google OAuth client id/secret). |
| `--entry-id` | the 3 shop2parcel entry ids (repeatable) | Restrict the sweep to specific config entries instead of all 3 connected mailboxes. |
| `--max-pages` | `20` | Gmail `messages.list()` pagination cap per domain — a truncation warning is logged (not silently dropped) if more results were available. |
| `--client-id` | none | Override the Gmail OAuth client id instead of resolving it from `--app-credentials` (or set `S2P_GOOGLE_CLIENT_ID`). |
| `--client-secret` | none | Override the Gmail OAuth client secret (or set `S2P_GOOGLE_CLIENT_SECRET`). |

## What the report looks like

`candidates-report.md` groups results per mailbox (`## <account title>`
sections), with a table of **sender / subject / date / id** columns, newest
first, plus a header stating the window, run timestamp, and the seed-domain
list with provenance (which exact sending hosts contributed to each base
domain). A totals line closes the report.

**This is a hand-review list, not a classification result.** Anything the
report surfaces that turns out to be a genuine miss becomes its own
follow-up quick task — exactly the pattern `260827-e6t` established.

## Troubleshooting

- **"Google rejected the refresh_token grant" / refresh-token-rejected.**
  The Gmail config entry's refresh token has been revoked or expired. The
  script deliberately does not retry a rejected grant. The HA integration
  itself needs re-authorising via its normal reauth flow before either the
  live integration or this sweep can reach Gmail again.
- **Missing client secret / cannot resolve OAuth client.** `resolve_google_client`
  in `sweep_lib.py` tries three match strategies against
  `application_credentials` before giving up. If none match, pass
  `--client-id` / `--client-secret` directly, or set
  `S2P_GOOGLE_CLIENT_ID` / `S2P_GOOGLE_CLIENT_SECRET` in the environment.
- **A domain sweep looks truncated.** `sweep_run.py` logs a warning naming
  the domain when Gmail's `--max-pages` cap is hit with more results still
  available (silent truncation would be a false negative for an audit).
  Raise `--max-pages` and re-run.
- **UIDVALIDITY changed since the last poll.** The diff logic
  (`sweep_lib.build_seen_keys` / `is_candidate`) already tolerates this —
  it registers the bare uid from both the `imap:uid` and `uidvalidity:uid`
  spellings, so a UIDVALIDITY bump between the last real poll and this sweep
  cannot make already-captured mail look like a new candidate.
- **How to re-run safely.** The whole tool is read-only against both the
  mailboxes and HA's `.storage` — the only write is `--out`, which defaults
  inside this task's own directory. Re-running simply overwrites the report;
  there is no state to reset first.

## Safety properties

- **Read-only on both mailboxes.** IMAP opens `INBOX` via `select(...,
  readonly=True)` (issues `EXAMINE`, never `SELECT`), header fetches use a
  `BODY.PEEK[...]` spec so nothing is ever flagged `\Seen`, and no mutating
  IMAP command (flag stores, expunge, copy, move) appears anywhere in
  `sweep_run.py`.
- **Read-only on HA `.storage`.** Every storage path
  (`--config-entries`, `--app-credentials`, `--storage-dir`) is opened for
  reading only; the sole write target anywhere in the tool is `--out`.
- **No credential ever reaches the report.** No IMAP password, OAuth
  access/refresh token, or client secret is ever printed, logged, or
  rendered — enforced by a regression assertion in `test_sweep_lib.py`, not
  just by inspection.
- **Nothing under `custom_components/` is touched.** This is a read-only
  investigation; any fix a candidate surfaces becomes its own follow-up
  quick task.
