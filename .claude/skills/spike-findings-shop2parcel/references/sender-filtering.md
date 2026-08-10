# Sender/Subject Filtering — Configurable Exclusion + USPS Digest Nuance

## Requirements

- The local keyword filter (`build_keyword_matcher`/`matches_keyword_filter` in
  `email_parser.py`, shipped by quick task 260806-i5r) stays INCLUDE-biased — it must keep
  matching too much rather than too little. A sender-exclusion layer is a SEPARATE, EXCLUDE-biased
  mechanism and must never be merged into or confused with the include filter's fail-open
  contract.
- **USPS Informed Delivery (`email.informeddelivery.usps.com`) must never be excludable by
  default or by any shipped candidate list.** It sends a digest every single day regardless of
  whether any package is actually arriving — on package-free days the correct behavior is an
  empty Stage-1/Stage-2 result (`total-packages: 0` in USPS's own template), not a parser bug.
  Classifying this sender requires per-email ground truth, not a sender-level allow/deny
  judgment. Any exclusion mechanism must be structurally incapable of blocking it via a plausible
  short user entry (see "Exact match only" below).
- Sender-exclusion entries MUST be exact domain matches, never suffix/substring matches. A
  suffix match on an entry like `usps.com` would also match `email.informeddelivery.usps.com`,
  silently reintroducing the exact failure mode above.
- No YAML file, no code-baked default exclusion list — this was the user's explicit choice
  between three presented options. The exclusion list is 100% user-managed via the options flow,
  seeded from evidence the user reads off their own diagnostic sensors.

## How to Build It

**1. Enriched rejection logging (already shipped, quick task 260806-v2j)** — `coordinator.py`'s
carrier-format-gate rejection DEBUG lines already include `subject=%r sender=%r` from the
in-scope `meta`/`job.meta` dict, using lazy `%`-style formatting (no `isEnabledFor` guard needed
— the values come from data already in scope, zero extra computation). This is the raw material
users read to decide what to exclude. `%r` (not `%s`) is deliberate — escapes hostile header
content, closing a log-forging vector.

**2. Sender-exclusion matcher (shipped in quick task 260807-qw1, 2026-08-07)** — lives in
`custom_components/shop2parcel/api/email_parser.py` as `extract_sender_domain` and
`build_sender_exclusion_matcher`:
```python
def extract_sender_domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""

def build_sender_exclusion_matcher(excluded_domains: list[str]):
    normalized = {d.strip().lower().lstrip("@") for d in excluded_domains if d.strip()}
    if not normalized:
        return lambda sender: False  # fail-open: no config -> exclude nothing
    def matcher(sender: str) -> bool:
        return extract_sender_domain(sender) in normalized  # EXACT match only
    return matcher
```
Validated against 50 real live events (spike 026's corpus): 28/28 confirmed-noise events
correctly excluded, 0/22 reliable events (including all 15 USPS events) ever excluded.

**3. Options-flow UI (shipped in the same quick task 260807-qw1) — mirrors the existing
`CONF_CUSTOM_FIELDS` add/remove pattern in `options_flow.py` —
`async_step_custom_fields`/`async_step_add_custom_field`/`async_step_remove_custom_field`,
lines 374-463 — verbatim structure, under the `CONF_SENDER_EXCLUSIONS` constant now defined in
`const.py`, with the add/remove steps in `options_flow.py`)**:
```python
async def async_step_sender_exclusions(self, user_input=None):
    existing = self.config_entry.options.get(CONF_SENDER_EXCLUSIONS, [])
    menu_options = ["add_sender_exclusion"]
    if existing:
        menu_options.append("remove_sender_exclusion")
    return self.async_show_menu(step_id="sender_exclusions", menu_options=menu_options)

async def async_step_add_sender_exclusion(self, user_input=None):
    if user_input is not None:
        domain = user_input["domain"].strip().lower().lstrip("@")
        new_options = dict(self.config_entry.options)
        exclusions = list(new_options.get(CONF_SENDER_EXCLUSIONS, []))
        if domain not in exclusions:
            exclusions.append(domain)
        new_options[CONF_SENDER_EXCLUSIONS] = exclusions
        return self.async_create_entry(title="", data=new_options)
    return self.async_show_form(step_id="add_sender_exclusion",
        data_schema=vol.Schema({vol.Required("domain"): str}))

async def async_step_remove_sender_exclusion(self, user_input=None):
    existing = self.config_entry.options.get(CONF_SENDER_EXCLUSIONS, [])
    if user_input is not None:
        domain = user_input["domain"]
        new_options = dict(self.config_entry.options)
        new_options[CONF_SENDER_EXCLUSIONS] = [d for d in existing if d != domain]
        return self.async_create_entry(title="", data=new_options)
    return self.async_show_form(step_id="remove_sender_exclusion",
        data_schema=vol.Schema({vol.Required("domain"): vol.In(existing)}))
```

**4. Real corpus classification (from spike 026's live evidence, as a starting reference — NOT a
shipped default)**: senders confirmed to never yield a tracking number across 2+ real samples:
`parkslopeparents.com`, `groups.parkslopeparents.com`, `substack.com`, `google.com` (careers
alerts specifically), `github.com` (CI failure notifications), `mgs.opentable.com`. Confirmed
reliable: `email.informeddelivery.usps.com` (nuanced, see Requirements), `fedex.com`.

**5. Integration point (shipped)**: the exclusion check is wired into `gmail_coordinator.py`
(before the local keyword filter) and `imap_coordinator.py`, at the earliest point sender
metadata is known — before Stage-1 parsing — maximizing the CPU/API savings this feature is meant
to provide. One detail a future reader would otherwise get wrong: it marks an excluded message via
the in-memory `_mark_inflight` gate rather than the persisted `_mark_message_seen` cache, so
removing a domain from the exclusion list restores processing immediately (D-05).

## What to Avoid

- **Don't treat any USPS Informed Delivery rejection as evidence of a parsing bug without first
  checking the specific email's own `total-packages` count.** Two live-flagged "misses" (spike
  025) both turned out to be genuinely empty digest days, verified against USPS's own template
  markers (`<span id="total-packages">0</span>`, `<div id="no-packages-today">No packages are
  available to display.</div>`). The multi-shipment extraction mechanism (`extra_shipments`) is
  separately already validated and shipped (see `usps-digest-multi-shipment.md`) — this is not a
  contradiction, just two different senders' worth of evidence about the same integration.
- **Don't suffix/substring-match sender domains for exclusion.** This is not a style preference —
  it is the specific mechanism that would silently reintroduce the USPS exclusion risk.
- **Don't bake a default exclusion list into the code or ship a YAML config file for this
  feature.** Both were presented as options and explicitly rejected in favor of a pure
  options-flow UI list — revisit only if the user changes this decision directly, not as an
  unprompted "convenience" addition.
- **Don't conflate this exclude-biased filter's safety direction with the existing
  include-biased local keyword filter's.** `build_keyword_matcher` correctly biases toward
  matching too much (its own failure mode was silent under-matching). A sender-exclusion filter
  must bias the opposite way — excluding too little — because an over-eager exclusion silently
  and permanently drops real data with no visible error, which is strictly worse than a wasted
  Stage-1/Stage-2 pass.

## Constraints

- The `sensor.<x>_activity_log`'s `recent_events` attribute only retains a rolling window of the
  10 most recent events per state-change snapshot — any corpus built from it is a sample of
  convenience, not an exhaustive census. Document this limitation explicitly rather than
  presenting aggregate counts as complete.
- No live Gmail API fetch is required to build or validate this feature — the already-deployed
  enriched DEBUG logs plus the recorder DB's activity-log history are themselves a sufficient,
  zero-fetch corpus for both classification (spike 026) and matcher validation (spike 027).
- When a specific real email's content genuinely is needed (as in spike 025), a user pasting the
  raw `.eml` source directly into the chat is an accepted substitute for
  `GmailClient.async_get_message()` for small, targeted samples (1-3 emails) — see
  `email-parsing-diagnostics.md`'s existing convention for the broader corpus-fetch case.

## Origin

Synthesized from spikes: 025, 026, 027 (triggered by quick task 260806-v2j's enriched
carrier-format-rejection DEBUG logging, deployed live 2026-08-06)
Source files available in: `sources/025-usps-digest-ground-truth/`,
`sources/026-passed-filter-corpus-classification/`,
`sources/027-yaml-configurable-sender-exclusion/`

Shipped history: spikes 026/027's design shipped in quick task 260807-qw1 (2026-08-07).
