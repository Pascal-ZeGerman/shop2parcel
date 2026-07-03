"""IMAP client — async wrapper using executor for blocking imaplib calls.

No HA imports. Caller (coordinator) passes hass.async_add_executor_job.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import ssl
from collections.abc import Callable
from typing import Any, NoReturn

from .exceptions import ImapAuthError, ImapTransientError

_LOGGER = logging.getLogger(__name__)

# WR-11: volume bounds for the per-poll fetch. Without them, a broad search
# over a busy mailbox (worse in debug mode, which forces a 365-day window)
# fetches every matching message body — attachments included — into memory in
# one executor call: hundreds of MB on a Raspberry-Pi-class HA host.
# MAX_MESSAGES_PER_POLL: newest N UIDs are kept when the SEARCH over-returns.
# MAX_MESSAGE_BYTES: messages larger than this are skipped entirely; the size
# is read via a cheap RFC822.SIZE fetch BEFORE downloading the full body.
MAX_MESSAGES_PER_POLL = 100
MAX_MESSAGE_BYTES = 5 * 1024 * 1024  # 5 MB

_RFC822_SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")

# IN-03: RFC 5530 response codes / common server phrasings that mark a LOGIN
# "NO" as a TEMPORARY server condition rather than bad credentials. A login
# failure carrying one of these markers is transient (retry next poll), NOT an
# auth failure — classifying it as ImapAuthError triggered a spurious HA
# reauth flow for conditions that clear on their own.
_TRANSIENT_LOGIN_MARKERS = (
    "[unavailable]",
    "[inuse]",
    "[limit]",
    "try later",
    "try again",
    "temporar",  # "temporary" / "temporarily"
    "rate limit",
    "rate-limited",
)


class ImapClient:
    """Wraps imaplib for async use in HA. No HA imports — executor callable injected.

    In production: pass hass.async_add_executor_job as async_add_executor_job.
    In tests: pass an async callable that runs the sync function inline.
    Opens a fresh connection per fetch_shipping_emails call (stateful IMAP
    connections must not be shared across threads — RESEARCH.md Pitfall 6).
    """

    def __init__(self, async_add_executor_job: Callable) -> None:
        self._executor = async_add_executor_job

    async def fetch_shipping_emails(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        tls_mode: str,
        search_criteria: str,
        since_date: str,
        verify_tls: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch shipping emails via IMAP using SINCE-date search.

        D-11: Returns list[dict] with keys "uid" (int), "raw" (bytes), and
        "uidvalidity" (int | None — IN-04: the mailbox UIDVALIDITY when the
        server reports it, so callers can build rebuild-safe storage keys).
        since_date: IMAP SEARCH date string in DD-Mon-YYYY format (e.g. "8-May-2026").
        Phase 10 D-11: full-window scanning uses SINCE date exclusively.
        Entire IMAP session runs in one executor call (RESEARCH.md Pitfall 6).
        CR-01: verify_tls controls server-certificate verification (default True);
        False is an explicit opt-out for self-signed local servers.
        """
        try:
            return await self._executor(
                self._fetch_sync,
                host,
                port,
                username,
                password,
                tls_mode,
                search_criteria,
                since_date,
                verify_tls,
            )
        except ImapAuthError:
            raise  # already classified — do not re-wrap
        except ImapTransientError:
            raise  # already classified — do not re-wrap
        except Exception as err:
            _classify_imap_error(err)
            raise  # unreachable, but prevents implicit None return

    def _fetch_sync(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        tls_mode: str,
        search_criteria: str,
        since_date: str,
        verify_tls: bool = True,
    ) -> list[dict[str, Any]]:
        """Synchronous IMAP session — runs in executor thread.

        D-09: Uses select(readonly) to issue EXAMINE (not SELECT) — read-only at protocol level.
        D-09: Uses PEEK fetch spec to avoid setting \\Seen flag.
        D-09: Never calls store(), expunge(), copy(), or uid(MOVE/STORE/EXPUNGE/COPY).
        D-11: Uses SINCE {since_date} search — UID-based search removed (Phase 10).
        CR-01: builds an ssl.create_default_context() (CERT_REQUIRED + hostname check)
        for both the SSL and STARTTLS paths — imaplib's stdlib fallback context does
        NOT verify certificates, letting a MITM harvest the LOGIN credentials.
        The context is built HERE (executor thread), never on the event loop:
        create_default_context() loads CA certs from disk and HA flags/blocks
        loop-blocking SSL context creation.
        """
        conn: imaplib.IMAP4 | None = None
        try:
            ssl_context: ssl.SSLContext | None = None
            if tls_mode in ("ssl", "starttls"):
                ssl_context = ssl.create_default_context()
                if not verify_tls:
                    # Explicit user opt-out (self-signed cert on a trusted local
                    # server). Order matters: check_hostname must be disabled
                    # before verify_mode can be set to CERT_NONE.
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
            if tls_mode == "ssl":
                conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl_context, timeout=30)
            else:
                conn = imaplib.IMAP4(host, port, timeout=30)
                if tls_mode == "starttls":
                    conn.starttls(ssl_context=ssl_context)
                else:
                    # IN-06: tls_mode="none" is a supported config value, but the
                    # LOGIN below sends the credentials over a plaintext socket —
                    # make the risk visible instead of failing silently.
                    _LOGGER.warning(
                        "IMAP connection to %s:%s uses no TLS — credentials are "
                        "being sent unencrypted. Switch TLS Mode to 'ssl' or "
                        "'starttls' unless this server is on a fully trusted "
                        "local network.",
                        host,
                        port,
                    )

            _LOGGER.debug("IMAP connecting to %s:%s", host, port)
            # IN-03: classify auth failures ONLY from the LOGIN command itself —
            # the one place a credential rejection can genuinely surface. The old
            # message-keyword heuristic ("login"/"password"/... anywhere in ANY
            # imaplib error) misclassified transient errors from later commands
            # (e.g. "connection dropped during login") as auth failures, causing
            # a spurious reauth flow. IMAP4.abort stays transient even here (a
            # service error — close and retry); RFC 5530 markers that signal a
            # temporary condition also stay transient.
            try:
                conn.login(username, password)
            except imaplib.IMAP4.abort:
                raise  # transient — classified by _classify_imap_error below
            except imaplib.IMAP4.error as err:
                login_msg = str(err).lower()
                if any(marker in login_msg for marker in _TRANSIENT_LOGIN_MARKERS):
                    raise ImapTransientError(str(err)) from err
                raise ImapAuthError(str(err)) from err

            ok, _ = conn.select(
                "INBOX", readonly=True
            )  # Issues EXAMINE — read-only at protocol level
            if ok != "OK":
                raise ImapTransientError(f"Failed to select INBOX: {ok}")

            # IN-04: capture UIDVALIDITY from the EXAMINE response. IMAP UIDs are
            # only stable per (mailbox, UIDVALIDITY) — after a mailbox rebuild the
            # server MUST bump UIDVALIDITY and may reuse UIDs, so the coordinator
            # qualifies its per-message storage keys with this value to prevent a
            # reused UID from colliding with a previously persisted entry.
            # Fail-open: None when the server does not report it (or the value is
            # unparsable) — callers fall back to bare-UID keys, exactly as before.
            uidvalidity: int | None = None
            try:
                _uv_typ, uv_data = conn.response("UIDVALIDITY")
                if uv_data and uv_data[0] is not None:
                    uidvalidity = int(uv_data[0])
            except (ValueError, TypeError):  # fmt: skip
                uidvalidity = None

            # WR-01: imaplib._command concatenates string args as raw bytes and
            # appends CRLF with NO sanitization — a search string containing
            # \r\n would inject arbitrary pipelined IMAP commands (e.g. STORE/
            # EXPUNGE), silently breaking the D-09 read-only guarantee. Reject
            # ALL control characters at the client boundary (defense-in-depth;
            # the options flow rejects the same class at entry time).
            if any(ord(c) < 32 or ord(c) == 127 for c in search_criteria):
                raise ImapTransientError("search_criteria contains control characters")

            uid_arg = f"SINCE {since_date} {search_criteria}"

            typ, data = conn.uid("SEARCH", uid_arg)
            if typ != "OK" or not data or not data[0]:
                return []

            uid_list = data[0].decode().split()
            _LOGGER.debug("IMAP SEARCH returned %d UIDs for folder %s", len(uid_list), "INBOX")
            # WR-11: cap the number of messages fetched per poll. IMAP UIDs
            # ascend with arrival order, so the LAST entries are the newest.
            if len(uid_list) > MAX_MESSAGES_PER_POLL:
                _LOGGER.warning(
                    "IMAP SEARCH returned %d UIDs; processing only the newest %d "
                    "this poll (narrow the search criteria or rescan window to "
                    "cover older mail)",
                    len(uid_list),
                    MAX_MESSAGES_PER_POLL,
                )
                uid_list = uid_list[-MAX_MESSAGES_PER_POLL:]
            results: list[dict[str, Any]] = []

            for uid_str in uid_list:
                try:
                    uid_int = int(uid_str)
                except ValueError:
                    _LOGGER.warning("IMAP server returned non-integer UID %r; skipping", uid_str)
                    continue
                # WR-11: check the message size via RFC822.SIZE before pulling the
                # full body — a single huge message (large attachments) must not be
                # loaded into memory at all. Fail-open: if the size cannot be read
                # or parsed, proceed with the normal fetch.
                size = _message_size(conn, uid_str)
                if size is not None and size > MAX_MESSAGE_BYTES:
                    _LOGGER.warning(
                        "IMAP message UID %s is %d bytes (> %d byte cap); skipping",
                        uid_str,
                        size,
                        MAX_MESSAGE_BYTES,
                    )
                    continue
                typ, msg_data = conn.uid("FETCH", uid_str, "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    _LOGGER.warning(
                        "IMAP FETCH failed for UID %s (server returned typ=%r); "
                        "message cannot be retried after this poll cycle",
                        uid_str,
                        typ,
                    )
                    continue
                raw_bytes = msg_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    _LOGGER.warning(
                        "IMAP FETCH returned non-bytes body for UID %s; skipping", uid_str
                    )
                    continue  # Skip malformed FETCH tuple — body must be bytes
                # IN-04: uidvalidity rides along per-message so the coordinator can
                # build (UIDVALIDITY, UID)-qualified storage keys.
                results.append({"uid": uid_int, "raw": raw_bytes, "uidvalidity": uidvalidity})

            return results
        except ImapAuthError:
            raise  # already classified — re-raise without double-wrapping
        except ImapTransientError:
            raise  # already classified — re-raise without double-wrapping
        except Exception as err:
            _classify_imap_error(err)
            raise  # unreachable, but prevents implicit None return
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception as logout_err:  # noqa: BLE001
                    _LOGGER.debug("IMAP logout failed (ignored): %s", logout_err)


def _message_size(conn: imaplib.IMAP4, uid_str: str) -> int | None:
    """Return the RFC822.SIZE of a message, or None when it cannot be determined.

    WR-11: a cheap metadata-only FETCH issued before the full BODY.PEEK[] so
    oversized messages (large attachments) are never downloaded. Returns None
    (fail-open) on a non-OK response or an unparsable size — a metadata quirk
    must not block legitimate shipment mail.
    """
    typ, size_data = conn.uid("FETCH", uid_str, "(RFC822.SIZE)")
    if typ != "OK" or not size_data:
        return None
    for item in size_data:
        raw = item[0] if isinstance(item, tuple) else item
        if isinstance(raw, bytes):
            match = _RFC822_SIZE_RE.search(raw)
            if match:
                return int(match.group(1))
    return None


def _classify_imap_error(err: Exception) -> NoReturn:
    """Translate any non-login imaplib/socket exception to ImapTransientError.

    IN-03: auth classification happens exclusively at the login() call site in
    _fetch_sync — the only command whose failure can genuinely mean bad
    credentials. Everything reaching this fallback (select/search/fetch errors,
    protocol state errors, socket failures) is transient by construction; the
    old keyword heuristic ("login"/"password" anywhere in the message) turned
    transient server errors like "connection dropped during login" into
    ImapAuthError and triggered spurious reauth flows.

    Security: never include password in exception message.
    ImapTransientError → coordinator raises UpdateFailed (D-04).
    """
    raise ImapTransientError(str(err)) from err


def extract_text_body_imap(raw_bytes: bytes) -> str | None:
    """Extract text/plain body from raw IMAP message bytes.

    Used as fallback when no HTML body is present, paralleling extract_html_body_imap.
    """
    msg = email.message_from_bytes(raw_bytes)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, TypeError):  # fmt: skip
                        return payload.decode("utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/plain":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except (LookupError, TypeError):  # fmt: skip
                    return payload.decode("utf-8", errors="replace")
    return None


def extract_html_body_imap(raw_bytes: bytes) -> str | None:
    """Extract HTML body from raw IMAP message bytes.

    Uses email.message_from_bytes + .walk() for MIME multipart handling.
    Charset fallback: part.get_content_charset() or "utf-8".
    Parallels extract_html_body() in gmail_client.py for the IMAP path.
    """
    msg = email.message_from_bytes(raw_bytes)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except LookupError:
                        return payload.decode("utf-8", errors="replace")
                    except TypeError:
                        return payload.decode("utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
                except TypeError:
                    return payload.decode("utf-8", errors="replace")
    return None
