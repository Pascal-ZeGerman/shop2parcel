"""Offline building blocks for the sender-domain sweep audit (quick-260906-x62).

Zero network I/O, zero third-party imports — stdlib only, deliberately, so the
network driver (sweep_run.py) can be run under a different interpreter with the
sandbox disabled without any package-install dependency. See this task's
PLAN.md <interfaces> section for the storage-shape facts this module encodes.

Nothing in this module ever prints, logs, or renders a secret-bearing field
(imap_password, token contents, client_secret) — see Account.__repr__ and
render_report's secret-leak regression in test_sweep_lib.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Domains that would otherwise turn a "sweep everything from a known sender"
# audit into a full-mailbox dump: the mailbox owners themselves and their
# freemail/self-hosting domains. Exposed as a module constant so the driver
# can extend or override it from the CLI (--exclude-domain).
# --------------------------------------------------------------------------
DEFAULT_EXCLUDED_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "web.de",
        "schaefersweb.de",
        "google.com",
        "ionos.de",
    }
)

# Second-to-last-label multi-part public suffixes we collapse three labels
# for instead of two, so e.g. "mail.tracking.co.uk" -> "tracking.co.uk" and
# not the meaningless "co.uk".
_MULTI_PART_SUFFIXES: frozenset[str] = frozenset(
    {"co.uk", "org.uk", "com.au", "co.jp", "co.nz", "com.br"}
)


@dataclass(frozen=True)
class Account:
    """A shop2parcel config entry, holding transport credentials.

    __repr__ is overridden below to report only entry_id/kind — never the
    secret-bearing transport fields (imap_password, token, auth_implementation
    indirectly names a client_id but not a secret). This exists so an
    accidental f-string, log call, or unhandled-exception traceback involving
    an Account instance cannot leak credentials.
    """

    entry_id: str
    title: str
    kind: str  # "gmail" | "imap"
    imap_host: str | None = None
    imap_port: int | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    imap_tls: str | None = None
    imap_verify_tls: bool = True
    token: dict[str, Any] | None = None
    auth_implementation: str | None = None

    def __repr__(self) -> str:  # noqa: D105 — intentional secret-safe override
        return f"Account(entry_id={self.entry_id!r}, kind={self.kind!r})"


@dataclass(frozen=True)
class Candidate:
    """A single sweep hit not already accounted for in seen/persisted state."""

    account_title: str
    sender: str
    subject: str
    date: str
    ident: str


def load_json(path: str | Path) -> Any:
    """Read and parse a JSON file, read-only, with a legible error on failure.

    The orchestrator may run this as a different user than the HA container
    (which typically writes .storage as nobody:nogroup), so a permission
    error must name the path plainly rather than surfacing a bare traceback.
    """
    p = Path(path)
    try:
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as err:
        raise RuntimeError(f"File not found: {p}") from err
    except PermissionError as err:
        raise RuntimeError(
            f"Permission denied reading {p} — HA's .storage files are often "
            f"owned by a different user (e.g. nobody:nogroup); re-run as a "
            f"user with read access."
        ) from err
    except json.JSONDecodeError as err:
        raise RuntimeError(f"File is not valid JSON: {p} ({err})") from err


def load_accounts(config_entries_path: str | Path, entry_ids: Iterable[str]) -> list[Account]:
    """Filter core.config_entries to shop2parcel entries matching entry_ids."""
    wanted = set(entry_ids)
    data = load_json(config_entries_path)
    entries = data.get("data", {}).get("entries", [])
    accounts: list[Account] = []
    for entry in entries:
        if entry.get("domain") != "shop2parcel":
            continue
        entry_id = entry.get("entry_id")
        if entry_id not in wanted:
            continue
        entry_data = entry.get("data", {}) or {}
        accounts.append(
            Account(
                entry_id=entry_id,
                title=entry.get("title") or entry_id,
                kind=entry_data.get("connection_type", "gmail"),
                imap_host=entry_data.get("imap_host"),
                imap_port=entry_data.get("imap_port"),
                imap_username=entry_data.get("imap_username"),
                imap_password=entry_data.get("imap_password"),
                imap_tls=entry_data.get("imap_tls"),
                imap_verify_tls=entry_data.get("imap_verify_tls", True),
                token=entry_data.get("token"),
                auth_implementation=entry_data.get("auth_implementation"),
            )
        )
    return accounts


def _sanitize_client_id(client_id: str) -> str:
    """Replace every non-alphanumeric character in client_id with '_'."""
    return "".join(c if c.isalnum() else "_" for c in client_id)


def resolve_google_client(
    app_credentials_path: str | Path, auth_implementation: str | None
) -> tuple[str, str]:
    """Resolve (client_id, client_secret) for a Gmail account's auth_implementation.

    Tries, in order: exact `id` match, exact `auth_domain` match, then the
    normalized spelling `shop2parcel_` + sanitized(client_id). Never includes
    the secret in any raised message.
    """
    data = load_json(app_credentials_path)
    items = data.get("data", {}).get("items", [])

    if not auth_implementation:
        raise RuntimeError(
            f"No auth_implementation provided for Gmail account — cannot resolve "
            f"OAuth client from {Path(app_credentials_path)}. Pass --client-id / "
            f"--client-secret (or set S2P_GOOGLE_CLIENT_ID / "
            f"S2P_GOOGLE_CLIENT_SECRET) to override."
        )

    for item in items:
        if item.get("id") == auth_implementation:
            return item["client_id"], item["client_secret"]
    for item in items:
        if item.get("auth_domain") == auth_implementation:
            return item["client_id"], item["client_secret"]
    for item in items:
        client_id = item.get("client_id", "")
        if f"shop2parcel_{_sanitize_client_id(client_id)}" == auth_implementation:
            return item["client_id"], item["client_secret"]

    raise RuntimeError(
        f"Could not resolve OAuth client for auth_implementation="
        f"{auth_implementation!r} against {Path(app_credentials_path)}. Pass "
        f"--client-id / --client-secret (or set S2P_GOOGLE_CLIENT_ID / "
        f"S2P_GOOGLE_CLIENT_SECRET) to override."
    )


# Mirrors coordinator.py:_SHIPMENT_FIELD_TYPES — kept minimal here since this
# module only needs to read fields back out, not validate the full shape.
_REQUIRED_SHIPMENT_KEYS = ("tracking_number", "carrier_name", "order_name", "message_id")


def load_store(
    storage_dir: str | Path, entry_id: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read shop2parcel.<entry_id> and return (persisted_shipments, seen_message_ids).

    Mirrors the type guards in coordinator.py:_async_load_store — a wrong
    type never crashes the audit, it degrades to an empty container plus a
    printed warning (caller decides where warnings go; this function returns
    them via a plain print to stderr-equivalent behavior is left to callers
    that care — here we just fail open silently to keep this function pure).
    """
    path = Path(storage_dir) / f"shop2parcel.{entry_id}"
    data = load_json(path)
    stored = data.get("data", {}) or {}

    raw_shipments = stored.get("persisted_shipments", {})
    if not isinstance(raw_shipments, dict):
        raw_shipments = {}
    persisted: dict[str, dict[str, Any]] = {}
    for key, entry in raw_shipments.items():
        if isinstance(entry, dict):
            persisted[key] = entry

    raw_seen = stored.get("seen_message_ids", [])
    if not isinstance(raw_seen, list):
        raw_seen = []
    seen_ids = [mid for mid in raw_seen if isinstance(mid, str)]

    return persisted, seen_ids


def gmail_seed_message_ids(persisted: Mapping[str, dict[str, Any]]) -> list[str]:
    """Return bare Gmail message ids from a persisted_shipments dict."""
    ids: list[str] = []
    for entry in persisted.values():
        mid = entry.get("message_id")
        if isinstance(mid, str) and not mid.startswith("imap:"):
            ids.append(mid)
    return ids


def imap_seed_uids(persisted: Mapping[str, dict[str, Any]]) -> list[str]:
    """Return bare IMAP uids (stripped of the 'imap:' prefix) from persisted_shipments."""
    uids: list[str] = []
    for entry in persisted.values():
        mid = entry.get("message_id")
        if isinstance(mid, str) and mid.startswith("imap:"):
            uids.append(mid.removeprefix("imap:"))
    return uids


def extract_domain(from_header: str) -> str | None:
    """Extract the lowercased sending domain from a From header value.

    Uses email.utils.parseaddr; returns None on anything that does not yield
    an '@' (garbage, empty, or display-name-only input).
    """
    _display, addr = parseaddr(from_header or "")
    if "@" not in addr:
        return None
    domain = addr.rsplit("@", 1)[-1].strip().lower()
    return domain or None


def base_domain(host: str) -> str:
    """Collapse a hostname to its registrable base domain.

    Takes the last two labels, extended to three when the second-to-last
    label is in _MULTI_PART_SUFFIXES. Deliberately broader than the exact
    sending host: both Gmail's from: operator and IMAP's FROM key match on
    substrings of the From header, so sweeping the base domain also catches
    sibling subdomains a store rotates to.
    """
    labels = host.strip(".").split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def derive_seed_domains(
    from_headers: Iterable[str],
    extra_domains: Iterable[str] = (),
    excluded_domains: Iterable[str] = DEFAULT_EXCLUDED_DOMAINS,
) -> tuple[list[str], dict[str, list[str]]]:
    """Map From headers -> base domains, drop excluded, merge extras.

    Returns (sorted deduped domain list, provenance map of base domain ->
    sorted list of exact sending hosts that contributed to it).
    """
    excluded = set(excluded_domains)
    provenance: dict[str, set[str]] = {}
    for header in from_headers:
        host = extract_domain(header)
        if host is None:
            continue
        base = base_domain(host)
        if base in excluded:
            continue
        provenance.setdefault(base, set()).add(host)

    for extra in extra_domains:
        extra = extra.strip().lower()
        if extra and extra not in excluded:
            provenance.setdefault(extra, set())

    domains = sorted(provenance)
    provenance_sorted = {d: sorted(hosts) for d, hosts in provenance.items()}
    return domains, provenance_sorted


def build_seen_keys(persisted: Mapping[str, dict[str, Any]], seen_ids: Iterable[str]) -> set[str]:
    """Build the union "already known" key set for one account.

    Includes every seen_message_ids element, every persisted_shipments dict
    key, and every entry's message_id value. For IMAP entries this also
    registers the bare uid parsed out of both the 'imap:' and 'uidvalidity:'
    spellings, so a UIDVALIDITY change between the last poll and the sweep
    cannot make already-captured mail look like a new candidate.
    """
    keys: set[str] = set()
    keys.update(seen_ids)
    for seen in seen_ids:
        if ":" in seen and not seen.startswith("imap:"):
            # "uidvalidity:uid" spelling — also register the bare uid.
            _uidvalidity, _sep, uid = seen.partition(":")
            if uid:
                keys.add(uid)

    for key, entry in persisted.items():
        keys.add(key)
        # A multi-shipment message uses "uid_key::tracking_number" — register
        # the uid_key portion too.
        if "::" in key:
            keys.add(key.split("::", 1)[0])
        mid = entry.get("message_id")
        if isinstance(mid, str):
            keys.add(mid)
            if mid.startswith("imap:"):
                keys.add(mid.removeprefix("imap:"))
    return keys


def is_candidate(
    kind: str,
    ident: str,
    uidvalidity: int | None,
    seen_keys: set[str],
) -> bool:
    """True when ident is genuinely new (absent from every known spelling)."""
    if kind == "imap":
        spellings = {ident, f"imap:{ident}"}
        if uidvalidity is not None:
            spellings.add(f"{uidvalidity}:{ident}")
        return spellings.isdisjoint(seen_keys)
    return ident not in seen_keys


def render_report(
    sections: Mapping[str, list[Candidate]],
    meta: Mapping[str, Any],
) -> str:
    """Render the markdown candidates report.

    `meta` keys used: "window_months", "run_timestamp", "seed_domains"
    (mapping base domain -> list of contributing hosts).

    Never includes password/access_token/refresh_token/client_secret — this
    function only ever touches Candidate/meta data, which by construction
    carries no transport secrets (see Account.__repr__ and the callers of
    this function, which pass plain strings/dataclasses only).
    """
    lines: list[str] = []
    lines.append("# Sender-Domain Sweep — Candidate Report")
    lines.append("")
    lines.append(
        "This is a **candidate list for manual eyeballing, not a classification "
        "result**. Anything here that turns out to be a genuine miss should become "
        "its own follow-up quick task."
    )
    lines.append("")
    lines.append(f"- Window: last {meta.get('window_months', '?')} months")
    lines.append(f"- Run at: {meta.get('run_timestamp', '?')}")
    lines.append("- Seed domains (with provenance):")
    seed_domains: Mapping[str, list[str]] = meta.get("seed_domains", {})
    for domain in sorted(seed_domains):
        hosts = seed_domains[domain]
        hosts_str = ", ".join(hosts) if hosts else "(extra domain, no provenance)"
        lines.append(f"  - `{domain}` — {hosts_str}")
    lines.append("")

    total = 0
    for account_title in sorted(sections):
        candidates = sections[account_title]
        lines.append(f"## {account_title}")
        lines.append("")
        if not candidates:
            lines.append("No candidates.")
            lines.append("")
            continue
        lines.append("| Sender | Subject | Date | ID |")
        lines.append("|--------|---------|------|-----|")
        for cand in sorted(candidates, key=lambda c: c.date, reverse=True):
            lines.append(f"| {cand.sender} | {cand.subject} | {cand.date} | {cand.ident} |")
            total += 1
        lines.append("")

    lines.append(f"**Total candidates: {total}**")
    lines.append("")
    return "\n".join(lines)
