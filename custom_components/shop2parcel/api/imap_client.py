"""IMAP client — async wrapper using executor for blocking imaplib calls.

No HA imports. Caller (coordinator) passes hass.async_add_executor_job.
"""

from __future__ import annotations

import email
import imaplib
import logging
from collections.abc import Callable
from typing import Any, NoReturn

from .exceptions import ImapAuthError, ImapTransientError

_LOGGER = logging.getLogger(__name__)


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
        since_uid: int | None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Fetch shipping emails via IMAP, returning (messages, max_uid).

        D-06: Returns list[dict] with keys "uid" (int) and "raw" (bytes).
        D-08: Passes since_uid to _fetch_sync for UID-based incremental polling.
        Entire IMAP session runs in one executor call (RESEARCH.md Pitfall 6).
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
                since_uid,
            )
        except (ImapAuthError, ImapTransientError):
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
        since_uid: int | None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Synchronous IMAP session — runs in executor thread.

        D-09: Uses select(readonly) to issue EXAMINE (not SELECT) — read-only at protocol level.
        D-09: Uses PEEK fetch spec to avoid setting \\Seen flag.
        D-09: Never calls store(), expunge(), copy(), or uid(MOVE/STORE/EXPUNGE/COPY).
        """
        conn: imaplib.IMAP4 | None = None
        try:
            if tls_mode == "ssl":
                conn = imaplib.IMAP4_SSL(host, port, timeout=30)
            else:
                conn = imaplib.IMAP4(host, port, timeout=30)
                if tls_mode == "starttls":
                    conn.starttls()

            conn.login(username, password)

            ok, _ = conn.select(
                "INBOX", readonly=True
            )  # Issues EXAMINE — read-only at protocol level
            if ok != "OK":
                raise ImapTransientError(f"Failed to select INBOX: {ok}")

            if since_uid is not None:
                uid_arg = f"{since_uid + 1}:* {search_criteria}"
            else:
                uid_arg = search_criteria

            typ, data = conn.uid("SEARCH", uid_arg)
            if typ != "OK" or not data or not data[0]:
                return [], None

            uid_list = data[0].decode().split()
            results: list[dict[str, Any]] = []
            max_uid: int | None = None

            for uid_str in uid_list:
                try:
                    uid_int = int(uid_str)
                except ValueError:
                    _LOGGER.warning("IMAP server returned non-integer UID %r; skipping", uid_str)
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
                results.append({"uid": uid_int, "raw": raw_bytes})
                if max_uid is None or uid_int > max_uid:
                    max_uid = uid_int

            return results, max_uid
        except (ImapAuthError, ImapTransientError):
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


def _classify_imap_error(err: Exception) -> NoReturn:
    """Translate imaplib exceptions to ImapAuthError / ImapTransientError.

    Security: never include password in exception message.
    ImapAuthError → coordinator raises ConfigEntryAuthFailed (D-04).
    ImapTransientError → coordinator raises UpdateFailed (D-04).

    IMAP4.abort is a subclass of IMAP4.error but semantically a service error
    (close and retry) — always transient, even if the message contains "invalid".
    Note: "invalid" is intentionally excluded from auth keywords because IMAP
    protocol state errors like "command invalid in state AUTH" contain that word
    but are transient, not auth failures.
    """
    if isinstance(err, imaplib.IMAP4.abort):
        # IMAP4.abort is semantically a service error (close and retry) — always transient.
        raise ImapTransientError(str(err)) from err
    if isinstance(err, imaplib.IMAP4.error):
        msg = str(err).lower()
        if any(kw in msg for kw in ("login", "auth", "credential", "username", "password")):
            raise ImapAuthError(str(err)) from err
    raise ImapTransientError(str(err)) from err


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
                    except (LookupError, TypeError):
                        return payload.decode("utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except (LookupError, TypeError):
                    return payload.decode("utf-8", errors="replace")
    return None
