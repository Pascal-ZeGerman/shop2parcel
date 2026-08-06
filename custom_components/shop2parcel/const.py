"""Constants for the Shop2Parcel integration."""

import re
from datetime import timedelta
from enum import Enum

DOMAIN = "shop2parcel"

# Phase 4: coordinator + options flow constants
CONF_POLL_INTERVAL = "poll_interval"  # minutes (int)
CONF_GMAIL_QUERY = "gmail_query"  # Gmail search query string
DEFAULT_POLL_INTERVAL = 30  # 30 minutes (CONTEXT.md D-08)
# Phase 28 Plan 02 (R6): full-body keyword match — no Gmail operator prefix.
# Phase 27 used a header-only operator to avoid broad-scan noise; Phase 28
# widens to the full message body so that body-only carrier emails (e.g. USPS
# Informed Delivery digests whose headers lack every keyword but whose body
# carries the 9400… number) are now fetched.  The strict carrier-format
# pre-POST gate introduced in Plans 01/03/04 is the backstop that makes the
# wider net safe — non-shipment mail that slips through the query is rejected
# before burning a parcelapp quota slot.  Gmail already excludes Spam/Trash,
# so no -label:spam guard is needed.
# User can override the stored per-entry value at any time (CONF_GMAIL_QUERY) —
# see the quick-260806-i5r note below for how that value is consumed today.
#
# Residual FedEx risk (T-N3K-02): the FedEx carrier pattern in _TRACKING_PATTERNS
# matches ANY bare 12-, 15-, or 20-digit number.  An email body containing an
# order/invoice/phone number of those lengths that Stage-1 matches as FedEx will
# pass the carrier-format gate and burn a parcelapp quota slot.  The wider query
# above increases the email volume exposed to this risk.  Tightening the FedEx
# pattern to a known-prefix anchor is deferred to a future phase.
#
# quick-260806-i5r (gmail-query-drops-emails follow-up, D-01): this string is no
# longer sent to the Gmail List API at all — gmail_coordinator.py always passes
# an empty base query there (Gmail's server-side OR-chain search silently
# dropped real shipment emails). DEFAULT_GMAIL_QUERY is now consumed exclusively
# by api/email_parser.py's build_keyword_matcher(), which compiles it into a
# LOCAL post-fetch narrowing filter applied to each message's subject/body
# before the expensive EmailParser.parse() call. The options-flow field that
# used to expose this value for editing was removed (D-03); a stored per-entry
# override still works and still feeds the local filter (D-04), it just has no
# form to change it from.
DEFAULT_GMAIL_QUERY = (
    "tracking OR shipped OR shipment OR delivery OR delivered OR parcel OR package OR order"
)

# QF-02: Gmail-only rescan window option. Controls the minimum lookback period
# used in build_incremental_query. Allows users to widen the after: filter
# without wiping forwarded_ids (which would cause duplicate ParcelApp POSTs).
CONF_RESCAN_WINDOW_DAYS = "rescan_window_days"  # int, days; Gmail-only
DEFAULT_RESCAN_WINDOW_DAYS = 30
MIN_RESCAN_WINDOW_DAYS = 7
MAX_RESCAN_WINDOW_DAYS = 365

# Gmail-side per-poll message volume cap (D-02, quick-260806-i5r): the
# server-side keyword narrowing removed by gmail-query-drops-emails used to
# bound how many messages a poll fetched in practice; now a broad
# date-only after: window over a busy mailbox decides how many
# messages.get() full-body fetches a single poll performs — unbounded on a
# Raspberry-Pi-class host, and worse in debug mode which forces a 365-day
# window (MAX_RESCAN_WINDOW_DAYS above). Mirrors api/imap_client.py's own
# same-purpose MAX_MESSAGES_PER_POLL, deliberately named differently so the
# two never get confused across modules — that constant stays untouched, in
# its own module. Gmail messages.list() returns results newest-first
# (opposite of IMAP's ascending UIDs), so the coordinator keeps the FRONT
# slice, not the tail.
MAX_GMAIL_MESSAGES_PER_POLL: int = 100

# Phase 9: IMAP connection + multi-account constants
CONF_CONNECTION_TYPE = "connection_type"  # str: "gmail" | "imap"
CONNECTION_TYPE_GMAIL = "gmail"
CONNECTION_TYPE_IMAP = "imap"
CONF_IMAP_HOST = "imap_host"  # str
CONF_IMAP_PORT = "imap_port"  # int
CONF_IMAP_USERNAME = "imap_username"  # str
CONF_IMAP_PASSWORD = "imap_password"  # str (encrypted in entry.data)
CONF_IMAP_TLS = "imap_tls"  # str: "ssl" | "starttls" | "none"
# CR-01: server-certificate verification toggle for the SSL/STARTTLS paths.
# Default True (verify). False is an explicit opt-out for self-signed
# certificates on trusted local servers — never disable silently.
CONF_IMAP_VERIFY_TLS = "imap_verify_tls"  # bool
DEFAULT_IMAP_VERIFY_TLS = True
CONF_IMAP_SEARCH = "imap_search"  # str: IMAP SEARCH criteria
DEFAULT_IMAP_SEARCH = (
    'OR OR OR SUBJECT "shipped" SUBJECT "tracking" SUBJECT "delivery" SUBJECT "shipment"'
)

# Parcel API key (stored in config entry data, shared between config_flow and coordinator)
CONF_API_KEY = "api_key"

# PR4-C2: opt-in gate for Tier 2 broad-scan. OFF by default to prevent
# false-positive forwards to ParcelApp consuming the 20/day quota.
CONF_ENABLE_BROAD_SCAN = "enable_broad_scan"
DEFAULT_ENABLE_BROAD_SCAN = False

# Phase 10 (D-09): LRU cap for submitted_tracking_numbers OrderedDict.
MAX_SUBMITTED_TRACKING_NUMBERS = 1000

# Phase 14 (DBG-01): debug/dry-run mode toggle; stored in entry.options.
CONF_DEBUG_MODE = "debug_mode"

# Phase 14 (WR-03): per-entry notification ID prefix.
# Using a per-entry suffix prevents notification collision when multiple
# Shop2Parcel config entries exist (e.g., one Gmail + one IMAP account).
# The helper below builds the full notification_id for a given entry_id.
DEBUG_MODE_NOTIFICATION_ID_PREFIX = "shop2parcel_debug_mode"


def debug_mode_notification_id(entry_id: str) -> str:
    """Return the persistent-notification ID scoped to a single config entry.

    Using a per-entry suffix prevents notification collision when multiple
    Shop2Parcel config entries coexist (P14-WR-03).  The prefix alone
    (``shop2parcel_debug_mode``) was the pre-fix value — callers must now
    use this helper so HA's notification store keeps each entry's banner
    separate.
    """
    return f"{DEBUG_MODE_NOTIFICATION_ID_PREFIX}_{entry_id}"


# Phase 20 MRG-05 (CONTEXT.md D-08): per-poll Stage-2 POST cap.
# Caps Stage-2 POSTs at 5 per poll cycle — 25% of parcelapp's 20-POST daily
# quota. 4 polls/day at the cap equals the daily limit (5 × 4 = 20); any
# additional activity in the same day would exceed the quota. The counter
# window is "per counter-reset", not a strict per-poll-cycle guarantee — jobs
# queued by one poll but drained by the next may count against the next poll's
# quota. See WR-01 in phase 20 REVIEW.md for full analysis.
# stage2_cap_notification_id mirrors debug_mode_notification_id pattern.
MAX_STAGE2_POSTS_PER_POLL: int = 5
# Phase 31 (D-02): shared per-poll Stage-2 POST window used by the hub's
# _poll_window_unsub timer (31-03) to reset the shared _stage2_posts_this_poll
# counter. Matches DEFAULT_POLL_INTERVAL's 30-minute wall-clock cadence.
HUB_STAGE2_POLL_WINDOW = timedelta(minutes=30)
# Phase 27 volume guards (Constants summary, Design §1):
# SEEN_MESSAGE_IDS_MAXLEN: FIFO bound for the seen-message-ID cache introduced
#   in Plan 02 (coordinator.py). Oldest ID evicted when the cache reaches this
#   size. 10 000 covers years of typical shipment-mail volume.
SEEN_MESSAGE_IDS_MAXLEN: int = 10_000
# MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL: per-poll cap on Ollama fallback
#   extractions (gmail_coordinator.py, Plan 03). Composes with
#   MAX_STAGE2_POSTS_PER_POLL (POST cap); the extraction cap fires earlier to
#   limit Ollama load on a large first-poll backlog.
MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL: int = 10
# Per-poll cumulative wall-clock budget (seconds) for inline Stage-1-miss Ollama
# fallback extraction. Checked BETWEEN extractions (a single in-flight call can still
# run up to ollama_timeout); 60 s is comfortably below HA's 300 s bootstrap stage-2
# global timeout and caps steady-state inline Ollama time on every poll.
MAX_STAGE2_FALLBACK_INLINE_SECONDS: float = 60.0
STAGE2_CAP_NOTIFICATION_ID_PREFIX = "shop2parcel_stage2_cap"

# Phase 32 (D-12, WORK-03): shared hub Stage-2 queue/worker bounds. Both are
# fixed named constants — NOT user-configurable (contrast the vestigial
# per-account CONF_QUEUE_MAXLEN/DEFAULT_QUEUE_MAXLEN below, which predates the
# shared hub queue and is retired once the per-entry queue/worker are cut over).
# HUB_STAGE2_QUEUE_MAXLEN: global asyncio.Queue maxsize on the hub — the
#   drop-newest bound across ALL accounts sharing the single worker.
HUB_STAGE2_QUEUE_MAXLEN: int = 64
# STAGE2_PER_ACCOUNT_INFLIGHT_CAP: max jobs enqueued-but-not-yet-completed for
#   a single entry_id at any time, independent of the global bound above.
STAGE2_PER_ACCOUNT_INFLIGHT_CAP: int = 8


class EnqueueOutcome(Enum):
    """Three-way distinguishable result of hub.enqueue() (D-02, Phase 32 WORK-03).

    Lives in const.py (not hub.py or coordinator.py) so both modules can import
    it with no circular import — hub.py already imports coordinator at module
    scope, and coordinator imports hub only under TYPE_CHECKING.

    ENQUEUED: job accepted onto the shared queue; coordinator bumps
        stage2_enqueued_total.
    DROPPED_BACKPRESSURE: rejected by either bound (global queue-full or the
        per-account in-flight cap); coordinator emits stage2_dropped_backpressure
        and bumps stage2_dropped_backpressure_total.
    SKIPPED_DUP: a job for this tracking number is already in flight (this or
        another entry); silent no-op — no event, no counter bump.
    """

    ENQUEUED = "enqueued"
    DROPPED_BACKPRESSURE = "dropped_backpressure"
    SKIPPED_DUP = "skipped_dup"


def stage2_cap_notification_id(entry_id: str) -> str:
    """Return the persistent-notification ID for Stage-2 cap-hit events.

    Mirrors debug_mode_notification_id pattern (P14-WR-03). Using a per-entry
    suffix prevents notification collision when multiple Shop2Parcel config
    entries coexist. Fired at most once per poll cycle (D-08) so the user
    sees a single banner rather than per-job spam.
    """
    return f"{STAGE2_CAP_NOTIFICATION_ID_PREFIX}_{entry_id}"


# Phase 21 Plan 02 (FAIL-04): consecutive-failure threshold notification constants.
# After STAGE2_NOTIFY_THRESHOLD consecutive Ollama failures the user sees a persistent
# notification banner.  Re-fires are gated by STAGE2_NOTIFY_COOLDOWN_S (1 hour) to
# avoid notification spam under sustained outages (T-21-02-01 DoS mitigation).
# stage2_failing_notification_id mirrors stage2_cap_notification_id pattern (P14-WR-03).
STAGE2_NOTIFY_THRESHOLD: int = 3
STAGE2_NOTIFY_COOLDOWN_S: int = 3600
STAGE2_FAILING_NOTIFICATION_ID_PREFIX = "shop2parcel_stage2_failing"

# Poison-message quarantine: after STAGE2_MSG_QUARANTINE_THRESHOLD consecutive
# extraction failures on the SAME Gmail message, the worker stops releasing it
# for re-fetch and leaves it in the in-memory in-flight skip set for the rest of
# the session — breaking the observed infinite retry loop where one pathological
# email re-failed every poll cycle for hours. Session-scoped only (never
# persisted) so a transient Ollama outage self-heals on restart instead of
# permanently poisoning legitimate shipment emails.
STAGE2_MSG_QUARANTINE_THRESHOLD: int = 5


def stage2_failing_notification_id(entry_id: str) -> str:
    """Return the persistent-notification ID for Stage-2 consecutive-failure events.

    Mirrors stage2_cap_notification_id pattern (P14-WR-03). Using a per-entry
    suffix prevents notification collision when multiple Shop2Parcel config
    entries coexist (T-21-02-04). Fired at most once per STAGE2_NOTIFY_COOLDOWN_S
    window (FAIL-04 threshold notification; 1-hour cooldown via STAGE2_NOTIFY_COOLDOWN_S).
    """
    return f"{STAGE2_FAILING_NOTIFICATION_ID_PREFIX}_{entry_id}"


# Phase 34 (D-06): hub-scoped consolidated Stage-2 failure notification —
# ONE notification for the whole HA instance instead of one per account.
# HUB_STAGE2_NOTIFY_THRESHOLD is deliberately HIGHER than the per-account
# STAGE2_NOTIFY_THRESHOLD=3 above: the hub aggregates the affected-account
# count across up to ~10 accounts sharing the queue+worker, so the streak
# crosses the threshold faster than any single account would, and the
# all-recovered dismiss (D-07) makes a low threshold sticky/noisy. Unlike
# stage2_failing_notification_id() above, HUB_STAGE2_FAILING_NOTIFICATION_ID
# is a FIXED string, not a per-entry helper — there is exactly ONE hub-scoped
# notification with no entry_id parameter (D-05/D-08).
#
# 34-REVIEW-FIX (CR-02): this value is a CAP on the effective threshold, not
# a fixed floor. hub.record_stage2_worker_failure() scales the effective
# threshold DOWN to the number of currently-attached accounts
# (min(HUB_STAGE2_NOTIFY_THRESHOLD, max(1, len(self._coordinators)))) so the
# project's stated primary use case — a "personal HA ecosystem" with one or
# a handful of accounts (CLAUDE.md) — can still trip the notification once
# every attached account is failing, instead of never reaching a fixed
# 5-distinct-account bar. Larger fleets still keep this value as their
# ceiling, so a single flaky account among ~10 never fires it alone.
HUB_STAGE2_NOTIFY_THRESHOLD: int = 5
HUB_STAGE2_FAILING_NOTIFICATION_ID: str = "shop2parcel_hub_stage2_failing"


def normalize_tracking_number(tracking_number: str) -> str:
    """Normalize a tracking number to the single canonical dedup form.

    WR-01: mirrors validate_carrier_format's clean form (api/email_parser.py) —
    strips internal space/dash separators and uppercases, so EVERY dedup write
    site produces the identical key regardless of email formatting. Two divergent
    normalizations (strip().upper() here vs the separator-free gate-clean form)
    previously split the dedup key space: a separator-containing tracking number
    was recorded under one key by the drain and looked up under another by the
    next poll, defeating dedup and re-running Ollama + re-POSTing.
    The separator strip uses a bounded char class (no unbounded quantifier —
    ASVS V5), same as validate_carrier_format.
    """
    return re.sub(r"[ -]", "", (tracking_number or "").strip()).upper()


# Phase 16: Stage-2 LLM extraction (locked field set — owned by extractor,
# surfaced by Phase 17 options flow). The tuple is consumed by:
#   * extractors/ollama_extractor.py.build_schema (required keys)
#   * extractors/ollama_extractor.py._validate_fields (collision check, Plan 03)
#   * Phase 20 merge_llm_authoritative (locked-vs-custom routing)
# Order is observable downstream — JSON Schema ``required`` array semantics
# depend on declared order for some validators (D-06).
# LOH-SUMMARY: order_summary is the 4th locked field. Its prompt/schema description
# is bespoke (composition-licensed) and comes from LOCKED_FIELD_DESCRIPTIONS below;
# the other three fields keep the None/auto-description behavior.
LOCKED_OLLAMA_FIELDS: tuple[str, str, str, str] = (
    "tracking_number",
    "carrier_name",
    "order_name",
    "order_summary",
)

# LOH-SUMMARY: Bespoke descriptions for locked fields that require composition
# instructions rather than the verbatim-extract auto-description. Only fields
# listed here carry a custom description; all others resolve to None and use
# _auto_description(name) in build_schema / build_prompt.
LOCKED_FIELD_DESCRIPTIONS: dict[str, str] = {
    "order_summary": (
        "A short human-readable summary combining the merchant/store name and the "
        'ordered item(s), e.g. "Target — Coffee maker". '
        "Exception to the verbatim-extraction rule: you MAY compose this string "
        "by combining information from the email rather than copying it verbatim. "
        'Use "<merchant> — <item(s)>" when both are known; use whichever '
        "is present if only one is derivable. "
        "Return null if neither the merchant nor the ordered contents are derivable "
        "from the email. "
        "Do NOT copy tracking numbers or order numbers into this field."
    ),
}

# Phase 17: Ollama Stage-2 configuration constants.
# CONF_OLLAMA_URL: user-supplied Ollama server base URL (required for Stage 2;
#   empty/absent → Stage-1-only path; no default — hardcoding localhost is unsafe
#   because Pi-on-Pi co-located Docker is only one of several network topologies).
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b"
CONF_OLLAMA_TIMEOUT = "ollama_timeout"
DEFAULT_OLLAMA_TIMEOUT = 60  # seconds
# Phase 32 (WORK-01, orchestrator decision 2): retained but INERT as of Phase 32 —
# the per-account Stage-2 queue this option used to size is retired; the Stage-2
# queue is now hub-global and bounded by HUB_STAGE2_QUEUE_MAXLEN (64) above.
# Left in the options flow (options_flow.py) to avoid a user-facing migration; a
# user who changes it will see no effect.
CONF_QUEUE_MAXLEN = "queue_maxlen"
DEFAULT_QUEUE_MAXLEN = 32
CONF_CUSTOM_FIELDS = "custom_fields"  # list[dict]: {"name": str, "description": str | None}
# CONF_STAGE2_ENABLED: derived boolean; set in async_setup_entry from CONF_OLLAMA_URL presence.
# Never exposed as a user-editable form field (D-05, T-17-02-03).
CONF_STAGE2_ENABLED = "stage2_enabled"
CONF_FIELD_NAME = "field_name"  # str, used in add/remove options steps
CONF_FIELD_DESCRIPTION = "field_description"  # str | None

# Phase 26 Plan 03 (P26-ENT-03): ParcelApp daily POST quota limit.
# Advisory only until a real 429 is seen (the coordinator then tracks actual quota).
# ParcelAppQuotaSensor uses this constant to estimate remaining quota.
PARCELAPP_DAILY_LIMIT: int = 20
