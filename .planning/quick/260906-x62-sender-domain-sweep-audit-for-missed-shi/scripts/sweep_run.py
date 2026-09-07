#!/usr/bin/env python3
"""Network driver for the sender-domain sweep audit (quick-260906-x62).

Two-phase flow:
  Phase 1 (seed derivation): for each account, load the store, fetch the
    From header for each already-captured message live, derive seed domains.
    --seeds-only stops here.
  Phase 2 (sweep): for every seed domain, on every account, list all mail
    from that domain in the last N months, drop anything already known,
    collect the rest as candidates, render candidates-report.md.

Stdlib only. Imports pure helpers from the sibling sweep_lib module.

Do NOT run this against the live accounts from a sandboxed agent context —
outbound network egress (IMAP TLS, Gmail HTTPS) is blocked by design and
there is no escalation path from a subagent. --self-check is the offline
verification path; the human-run orchestrator uses the real invocation with
a sandbox bypass. See README.md for the runbook.
"""

from __future__ import annotations

import argparse
import email.utils
import imaplib
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sweep_lib as sl  # noqa: E402 — path insert must precede this import

_DEFAULT_ENTRY_IDS = [
    "01KQTV7WYHHA2YEXTDV1GSC8H7",  # Gmail
    "01M0KJVZJXNKWTFVFRNFA3E0Z6",  # IMAP imap.ionos.de
    "01M0Z2T9XYY9WMC0SVWW04XC2Y",  # IMAP imap.web.de
]

_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_IMAP_FETCH_BATCH_SIZE = 200
_HEADER_FETCH_SPEC = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"


def _log(message: str) -> None:
    """Progress output goes to stderr so stdout stays clean."""
    print(message, file=sys.stderr)


def _reject_control_chars(value: str, label: str) -> None:
    """Reject control characters before a value reaches an IMAP command.

    Mirrors api/imap_client.py:203 — imaplib appends CRLF with no
    sanitization, so an unfiltered argument would pipeline arbitrary IMAP
    commands into a session promised to be read-only.
    """
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} contains control characters: rejected before use")


def _imap_since_date(months: int) -> str:
    """Render the window boundary in IMAP SEARCH's DD-Mon-YYYY shape."""
    since = datetime.now(UTC) - timedelta(days=months * 30)
    return since.strftime("%d-%b-%Y").lstrip("0")


def _gmail_after_ts(months: int) -> int:
    """Return the unix timestamp for Gmail's after: operator."""
    since = datetime.now(UTC) - timedelta(days=months * 30)
    return int(since.timestamp())


# --------------------------------------------------------------------------
# HTTP helper with bounded retry (Gmail transport)
# --------------------------------------------------------------------------


def _http_request_with_retry(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str = "GET",
    max_attempts: int = 3,
) -> bytes:
    """Issue an HTTP request with exponential backoff on 429/5xx.

    Honours Retry-After when present. Raises with the status code and
    endpoint on any other failure — never includes the bearer token or any
    secret in the raised message.
    """
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 429 or 500 <= err.code < 600:
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"HTTP {err.code} from {urllib.parse.urlparse(url).path} "
                        f"after {attempt} attempts"
                    ) from err
                retry_after = err.headers.get("Retry-After") if err.headers else None
                delay = float(retry_after) if retry_after else (2**attempt)
                _log(f"HTTP {err.code} from {url} — retrying in {delay:.0f}s")
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {err.code} from {urllib.parse.urlparse(url).path}") from err


# --------------------------------------------------------------------------
# Gmail transport
# --------------------------------------------------------------------------


def refresh_gmail_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """Exchange a refresh_token for a fresh access_token.

    Always begins here — the stored access_token is expired by construction
    (3599s lifetime) by the time anyone runs this script. On a 400/401 from
    the token endpoint, raises with a message pointing at HA reauth — does
    not retry a rejected grant.
    """
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("ascii")
    req = urllib.request.Request(
        _TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        if err.code in (400, 401):
            raise RuntimeError(
                "Google rejected the refresh_token grant (HTTP "
                f"{err.code}) — the refresh token is expired or revoked; "
                "the HA integration needs re-authorising via its reauth flow."
            ) from err
        raise RuntimeError(f"Token refresh failed with HTTP {err.code}") from err
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Token refresh response did not contain an access_token")
    return access_token


def gmail_list_message_ids(
    access_token: str, query: str, max_pages: int, domain_for_warning: str
) -> list[str]:
    """List Gmail message ids matching query, following nextPageToken."""
    ids: list[str] = []
    page_token: str | None = None
    for _page in range(max_pages):
        params = {"q": query}
        if page_token:
            params["pageToken"] = page_token
        url = f"{_GMAIL_API_BASE}/messages?{urllib.parse.urlencode(params)}"
        raw = _http_request_with_retry(url, headers={"Authorization": f"Bearer {access_token}"})
        payload = json.loads(raw)
        ids.extend(m["id"] for m in payload.get("messages", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return ids
    _log(
        f"WARNING: Gmail list for domain {domain_for_warning!r} hit the "
        f"{max_pages}-page cap with more results available — truncation is "
        f"a false negative for an audit; consider raising --max-pages."
    )
    return ids


def gmail_get_headers(access_token: str, message_id: str) -> dict[str, str]:
    """Fetch From/Subject/Date headers for one Gmail message (metadata format)."""
    params = {
        "format": "metadata",
        "metadataHeaders": ["From", "Subject", "Date"],
    }
    url = f"{_GMAIL_API_BASE}/messages/{message_id}?{urllib.parse.urlencode(params, doseq=True)}"
    raw = _http_request_with_retry(url, headers={"Authorization": f"Bearer {access_token}"})
    payload = json.loads(raw)
    headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
    return {
        "From": headers.get("From", ""),
        "Subject": headers.get("Subject", ""),
        "Date": headers.get("Date", ""),
    }


# --------------------------------------------------------------------------
# IMAP transport
# --------------------------------------------------------------------------


def _decode_imap_header(value: str) -> str:
    """Decode an RFC 2047 encoded-word header, falling back to raw on error."""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, UnicodeDecodeError):  # fmt: skip
        return value


def imap_connect(
    host: str, port: int, username: str, password: str, tls_mode: str, verify_tls: bool
) -> tuple[imaplib.IMAP4, int | None]:
    """Open a read-only IMAP session and return (conn, uidvalidity).

    Mailbox is opened via select(readonly=True) — issues EXAMINE, never
    SELECT. Caller must log out in a finally block.
    """
    ssl_context: ssl.SSLContext | None = None
    if tls_mode in ("ssl", "starttls"):
        ssl_context = ssl.create_default_context()
        if not verify_tls:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

    if tls_mode == "ssl":
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl_context, timeout=30)
    else:
        conn = imaplib.IMAP4(host, port, timeout=30)
        if tls_mode == "starttls":
            conn.starttls(ssl_context=ssl_context)

    conn.login(username, password)
    ok, _ = conn.select("INBOX", readonly=True)  # Issues EXAMINE — read-only at protocol level
    if ok != "OK":
        raise RuntimeError("Failed to select INBOX read-only")

    uidvalidity: int | None = None
    try:
        _typ, uv_data = conn.response("UIDVALIDITY")
        if uv_data and uv_data[0] is not None:
            uidvalidity = int(uv_data[0])
    except (ValueError, TypeError):  # fmt: skip
        uidvalidity = None

    return conn, uidvalidity


def imap_search_from_domain(conn: imaplib.IMAP4, since_date: str, domain: str) -> list[str]:
    """Return matching UIDs for FROM "<domain>" SINCE <date>, UID-qualified."""
    _reject_control_chars(domain, "domain")
    _reject_control_chars(since_date, "since_date")
    query = f'SINCE {since_date} FROM "{domain}"'
    typ, data = conn.uid("SEARCH", query)
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].decode().split()


def imap_fetch_headers(conn: imaplib.IMAP4, uids: list[str]) -> dict[str, dict[str, str]]:
    """Fetch From/Subject/Date headers for a batch of UIDs via a peeking spec.

    Batches ~_IMAP_FETCH_BATCH_SIZE uids per call so nothing is downloaded
    beyond the requested header fields and no message is ever flagged
    \\Seen (BODY.PEEK, not BODY).
    """
    results: dict[str, dict[str, str]] = {}
    for start in range(0, len(uids), _IMAP_FETCH_BATCH_SIZE):
        batch = uids[start : start + _IMAP_FETCH_BATCH_SIZE]
        uid_set = ",".join(batch)
        typ, msg_data = conn.uid("FETCH", uid_set, _HEADER_FETCH_SPEC)
        if typ != "OK" or not msg_data:
            continue
        # imaplib returns a flat list where each message contributes a
        # (marker, literal) tuple; the uid isn't reliably embedded in every
        # server's response line, so we pair sequentially against the
        # requested batch order as a best-effort fallback when a per-item
        # UID cannot be parsed.
        idx = 0
        for item in msg_data:
            if not isinstance(item, tuple):
                continue
            raw_headers = item[1]
            if not isinstance(raw_headers, bytes):
                continue
            msg = email.message_from_bytes(raw_headers)
            from_hdr = _decode_imap_header(msg.get("From", ""))
            subject_hdr = _decode_imap_header(msg.get("Subject", ""))
            date_hdr = _decode_imap_header(msg.get("Date", ""))
            marker = (
                item[0].decode(errors="replace") if isinstance(item[0], bytes) else str(item[0])
            )
            uid_from_marker = None
            for token in marker.replace("(", " ").split():
                if token.isdigit() and idx < len(batch):
                    uid_from_marker = token
                    break
            uid = uid_from_marker or (batch[idx] if idx < len(batch) else str(idx))
            results[uid] = {"From": from_hdr, "Subject": subject_hdr, "Date": date_hdr}
            idx += 1
    return results


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _account_display_title(account: sl.Account) -> str:
    return account.title or account.entry_id


def run_seed_phase(
    accounts: list[sl.Account],
    args: argparse.Namespace,
) -> tuple[list[str], dict[str, list[str]]]:
    """Phase 1: fetch From headers for every stored message_id, derive seeds."""
    all_headers: list[str] = []

    for account in accounts:
        persisted, _seen_ids = sl.load_store(args.storage_dir, account.entry_id)
        title = _account_display_title(account)

        if account.kind == "gmail":
            token = account.token or {}
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                _log(f"WARNING: account {title} has no refresh_token — skipping seed fetch")
                continue
            client_id = args.client_id
            client_secret = args.client_secret
            if not client_id or not client_secret:
                client_id, client_secret = sl.resolve_google_client(
                    args.app_credentials, account.auth_implementation
                )
            access_token = refresh_gmail_access_token(client_id, client_secret, refresh_token)
            for msg_id in sl.gmail_seed_message_ids(persisted):
                try:
                    headers = gmail_get_headers(access_token, msg_id)
                except RuntimeError as err:
                    _log(f"WARNING: {title}: failed to fetch headers for {msg_id}: {err}")
                    continue
                if headers["From"]:
                    all_headers.append(headers["From"])
        else:
            uids = sl.imap_seed_uids(persisted)
            if not uids:
                continue
            conn = None
            try:
                conn, _uidvalidity = imap_connect(
                    account.imap_host,
                    account.imap_port,
                    account.imap_username,
                    account.imap_password,
                    account.imap_tls or "ssl",
                    account.imap_verify_tls,
                )
                headers_by_uid = imap_fetch_headers(conn, uids)
                for hdrs in headers_by_uid.values():
                    if hdrs["From"]:
                        all_headers.append(hdrs["From"])
            finally:
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:  # noqa: BLE001
                        pass

    domains, provenance = sl.derive_seed_domains(
        all_headers,
        extra_domains=args.extra_domain,
        excluded_domains=sl.DEFAULT_EXCLUDED_DOMAINS | set(args.exclude_domain),
    )
    return domains, provenance


def run_sweep_phase(
    accounts: list[sl.Account],
    domains: list[str],
    args: argparse.Namespace,
) -> dict[str, list[sl.Candidate]]:
    """Phase 2: sweep every seed domain on every account, diff against seen state."""
    sections: dict[str, list[sl.Candidate]] = {}

    for account in accounts:
        persisted, seen_ids = sl.load_store(args.storage_dir, account.entry_id)
        seen_keys = sl.build_seen_keys(persisted, seen_ids)
        title = _account_display_title(account)
        candidates: list[sl.Candidate] = []

        if account.kind == "gmail":
            token = account.token or {}
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                _log(f"WARNING: account {title} has no refresh_token — skipping sweep")
                sections[title] = candidates
                continue
            client_id = args.client_id
            client_secret = args.client_secret
            if not client_id or not client_secret:
                client_id, client_secret = sl.resolve_google_client(
                    args.app_credentials, account.auth_implementation
                )
            access_token = refresh_gmail_access_token(client_id, client_secret, refresh_token)
            after_ts = _gmail_after_ts(args.months)
            for domain in domains:
                query = f"from:{domain} after:{after_ts}"
                ids = gmail_list_message_ids(access_token, query, args.max_pages, domain)
                for msg_id in ids:
                    if sl.is_candidate("gmail", msg_id, None, seen_keys):
                        headers = gmail_get_headers(access_token, msg_id)
                        candidates.append(
                            sl.Candidate(
                                account_title=title,
                                sender=headers["From"],
                                subject=headers["Subject"],
                                date=headers["Date"],
                                ident=msg_id,
                            )
                        )
        else:
            since_date = _imap_since_date(args.months)
            conn = None
            try:
                conn, uidvalidity = imap_connect(
                    account.imap_host,
                    account.imap_port,
                    account.imap_username,
                    account.imap_password,
                    account.imap_tls or "ssl",
                    account.imap_verify_tls,
                )
                for domain in domains:
                    uids = imap_search_from_domain(conn, since_date, domain)
                    new_uids = [
                        uid for uid in uids if sl.is_candidate("imap", uid, uidvalidity, seen_keys)
                    ]
                    if not new_uids:
                        continue
                    headers_by_uid = imap_fetch_headers(conn, new_uids)
                    for uid, hdrs in headers_by_uid.items():
                        candidates.append(
                            sl.Candidate(
                                account_title=title,
                                sender=hdrs["From"],
                                subject=hdrs["Subject"],
                                date=hdrs["Date"],
                                ident=f"imap:{uid}",
                            )
                        )
            finally:
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:  # noqa: BLE001
                        pass

        sections[title] = candidates

    return sections


# --------------------------------------------------------------------------
# Self-check (offline)
# --------------------------------------------------------------------------


def run_self_check() -> int:
    """Exercise argument parsing, window math, the control-char guard, and a
    full render_report round-trip — all offline, no socket opened."""
    parser = build_arg_parser()
    args = parser.parse_args(["--self-check"])
    assert args.self_check is True

    since_date = _imap_since_date(12)
    assert len(since_date.split("-")) == 3, "IMAP SINCE date must be DD-Mon-YYYY shaped"

    after_ts = _gmail_after_ts(12)
    assert isinstance(after_ts, int) and after_ts > 0

    try:
        _reject_control_chars("evil\r\nDELE 1", "domain")
        raise AssertionError("control-character guard did not raise")
    except ValueError:
        pass
    _reject_control_chars("colamyhome.com", "domain")  # must not raise

    sections = {
        "fake-account": [sl.Candidate("fake-account", "s@x.com", "Subj", "2026-01-01", "imap:1")]
    }
    meta = {
        "window_months": 12,
        "run_timestamp": "2026-01-01T00:00:00Z",
        "seed_domains": {"x.com": ["s.x.com"]},
    }
    report = sl.render_report(sections, meta)
    assert "fake-account" in report
    assert "Subj" in report

    print("SELF-CHECK OK")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-entries",
        default="/home/pascal/homeassistant/config/.storage/core.config_entries",
    )
    parser.add_argument(
        "--app-credentials",
        default="/home/pascal/homeassistant/config/.storage/application_credentials",
    )
    parser.add_argument("--storage-dir", default="/home/pascal/homeassistant/config/.storage")
    parser.add_argument(
        "--entry-id", action="append", default=None, help="repeatable; defaults to all 3 accounts"
    )
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "candidates-report.md"),
    )
    parser.add_argument("--extra-domain", action="append", default=[])
    parser.add_argument("--exclude-domain", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--client-secret", default=None)
    parser.add_argument("--seeds-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.self_check:
        return run_self_check()

    entry_ids = args.entry_id or _DEFAULT_ENTRY_IDS
    accounts = sl.load_accounts(args.config_entries, entry_ids)
    if not accounts:
        _log("No matching shop2parcel accounts found — check --config-entries / --entry-id")
        return 1

    _log(f"Loaded {len(accounts)} account(s): {[a.title for a in accounts]}")

    domains, provenance = run_seed_phase(accounts, args)
    _log(f"Derived {len(domains)} seed domain(s):")
    for domain in domains:
        hosts = provenance.get(domain, [])
        _log(f"  {domain}  (from: {', '.join(hosts) if hosts else 'extra domain'})")

    if args.seeds_only:
        return 0

    sections = run_sweep_phase(accounts, domains, args)
    meta = {
        "window_months": args.months,
        "run_timestamp": datetime.now(UTC).isoformat(),
        "seed_domains": provenance,
    }
    report = sl.render_report(sections, meta)
    Path(args.out).write_text(report, encoding="utf-8")
    total = sum(len(c) for c in sections.values())
    _log(f"Wrote {total} candidate(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
