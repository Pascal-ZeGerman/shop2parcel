"""Tests for ImapClient — covers D-05, D-06, D-08, D-09.

All tests are xfail until api/imap_client.py is implemented (Plan 09-02).
imaplib is Python stdlib — no sys.modules patching required.
"""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Inline executor helper (mirrors test_gmail_client.py pattern)
# ---------------------------------------------------------------------------


async def _inline_executor(func, *args):
    """Run sync function inline for testing (replaces hass.async_add_executor_job)."""
    return func(*args)


# ---------------------------------------------------------------------------
# Stub: ImapClient class existence and constructor signature
# ---------------------------------------------------------------------------


def test_imap_client_is_importable():
    """D-06: ImapClient must be importable from api/imap_client.py."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    assert ImapClient is not None


def test_imap_client_constructor_accepts_executor():
    """D-05: ImapClient.__init__ accepts a single Callable (executor injection)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    client = ImapClient(async_add_executor_job=_inline_executor)
    assert hasattr(client, "_executor")


# ---------------------------------------------------------------------------
# Stub: D-09 read-only contract — STORE/EXPUNGE/COPY/MOVE never called
# ---------------------------------------------------------------------------


def test_imap_client_never_calls_mutating_commands():
    """D-09: ImapClient MUST NEVER call store(), expunge(), copy() or uid(MOVE/STORE/EXPUNGE/COPY).

    This test uses a MagicMock with spec=imaplib.IMAP4_SSL so calls to undeclared
    methods raise AttributeError — only declared IMAP4_SSL methods are allowed.
    """
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])  # empty SEARCH result
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        # Call _fetch_sync directly (synchronous path) with ssl mode
        client._fetch_sync(
            "imap.example.com",
            993,
            "user@example.com",
            "password",
            "ssl",
            'SUBJECT "shipped"',
            "8-May-2026",
        )

    mock_conn.store.assert_not_called()
    mock_conn.expunge.assert_not_called()
    mock_conn.copy.assert_not_called()
    for call_args in mock_conn.uid.call_args_list:
        assert call_args[0][0].upper() not in ("MOVE", "STORE", "EXPUNGE", "COPY"), (
            f"ImapClient issued a mutating UID command: {call_args[0][0]}"
        )


# ---------------------------------------------------------------------------
# Stub: D-06 — fetch_shipping_emails return shape
# ---------------------------------------------------------------------------


async def test_fetch_shipping_emails_returns_list():
    """D-11: fetch_shipping_emails returns list[dict] — no tuple, no max_uid."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])  # empty SEARCH → no messages
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        result = await client.fetch_shipping_emails(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="password",
            tls_mode="ssl",
            search_criteria='SUBJECT "shipped"',
            since_date="8-May-2026",
        )

    assert isinstance(result, list)
    assert len(result) == 0  # no messages found


# ---------------------------------------------------------------------------
# Stub: D-08 — EXAMINE (select readonly=True) is called, not SELECT
# ---------------------------------------------------------------------------


def test_imap_client_uses_examine_not_select():
    """D-08/D-09: select() must be called with readonly=True (issues EXAMINE command)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        client._fetch_sync(
            "imap.example.com",
            993,
            "user@example.com",
            "password",
            "ssl",
            'SUBJECT "shipped"',
            "8-May-2026",
        )

    # imaplib.IMAP4.select(mailbox, readonly=True) issues EXAMINE at protocol level
    mock_conn.select.assert_called_once()
    call_kwargs = mock_conn.select.call_args
    # readonly=True can be positional (arg index 1) or keyword
    args = call_kwargs[0]
    kwargs = call_kwargs[1]
    readonly_value = kwargs.get("readonly", args[1] if len(args) > 1 else False)
    assert readonly_value is True, "select() must be called with readonly=True (issues EXAMINE)"


# ---------------------------------------------------------------------------
# Stub: D-05 — auth error raises ImapAuthError
# ---------------------------------------------------------------------------


def test_imap_login_failure_raises_imap_auth_error():
    """D-04/D-05: Login failures must raise ImapAuthError (coordinator maps to ConfigEntryAuthFailed)."""
    from custom_components.shop2parcel.api.exceptions import ImapAuthError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATE failed: invalid credentials")
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(ImapAuthError):
            client._fetch_sync(
                "imap.example.com",
                993,
                "user@example.com",
                "wrong-password",
                "ssl",
                'SUBJECT "shipped"',
                "8-May-2026",
            )


# ---------------------------------------------------------------------------
# Stub: extract_html_body_imap function
# ---------------------------------------------------------------------------


def test_extract_html_body_imap_extracts_html():
    """D-06: extract_html_body_imap(raw_bytes) returns HTML string from RFC822 bytes."""
    from custom_components.shop2parcel.api.imap_client import (
        extract_html_body_imap,  # noqa: PLC0415
    )

    # Minimal multipart/alternative message with text/html part
    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=boundary123\r\n"
        b"\r\n"
        b"--boundary123\r\n"
        b"Content-Type: text/plain\r\n\r\nPlain text\r\n"
        b"--boundary123\r\n"
        b"Content-Type: text/html\r\n\r\n<html><body>HTML body</body></html>\r\n"
        b"--boundary123--\r\n"
    )
    result = extract_html_body_imap(raw)
    assert result is not None
    assert "HTML body" in result


# ---------------------------------------------------------------------------
# Gap closure regression tests — 09-05-PLAN.md
# ---------------------------------------------------------------------------


def test_starttls_failure_does_not_leak_socket():
    """CR-02 regression: starttls() failure must NOT leave a dangling TCP socket.

    When conn.starttls() raises ssl.SSLError (TLS negotiation failed, wrong port,
    server does not support STARTTLS, etc.), conn.logout() MUST still be called
    so the underlying TCP socket is closed.

    Before the fix, connection setup was outside the try block, so a starttls()
    exception escaped before the finally clause executed.
    """
    import ssl  # noqa: PLC0415

    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4)
    mock_conn.starttls.side_effect = ssl.SSLError("handshake failed")
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(Exception):
            # Any exception escaping _fetch_sync is acceptable;
            # the key requirement is that logout() was called despite starttls() failing.
            client._fetch_sync(
                "imap.example.com",
                143,
                "user@example.com",
                "password",
                "starttls",
                'SUBJECT "shipped"',
                "8-May-2026",
            )

    mock_conn.logout.assert_called_once()


def test_imap4_ssl_constructor_failure_does_not_leak_socket():
    """CR-02 regression (SSL path): IMAP4_SSL constructor failure — conn stays None.

    When imaplib.IMAP4_SSL(host, port) raises (DNS failure, connection refused,
    certificate error), conn is None because the constructor never returned.
    The finally clause must guard with 'if conn is not None' to avoid
    AttributeError on None.logout().
    """
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    with patch("imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("connection refused")):
        client = ImapClient(_inline_executor)
        with pytest.raises(Exception):
            client._fetch_sync(
                "imap.example.com",
                993,
                "user@example.com",
                "password",
                "ssl",
                'SUBJECT "shipped"',
                "8-May-2026",
            )
    # If we get here without AttributeError on NoneType.logout(), the guard works.


async def test_since_date_search_criteria():
    """D-11: _fetch_sync passes 'SINCE {since_date} {search_criteria}' to conn.uid SEARCH."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        await client.fetch_shipping_emails(
            host="h",
            port=993,
            username="u",
            password="p",
            tls_mode="ssl",
            search_criteria='SUBJECT "shipped"',
            since_date="8-May-2026",
        )

    search_call = mock_conn.uid.call_args_list[0]
    uid_arg = search_call[0][1]
    assert uid_arg == 'SINCE 8-May-2026 SUBJECT "shipped"', (
        f"Expected SINCE date search criterion, got: {uid_arg!r}"
    )


def test_select_non_ok_raises_imap_transient_error():
    """WR-03 regression: conn.select() returning non-OK status must raise ImapTransientError.

    Before the fix, the select() return value was discarded. A SELECT/EXAMINE
    failure (mailbox not found, permission denied) was silently ignored, causing
    a confusing SEARCH failure later instead of a clear INBOX-not-found error.
    """
    from custom_components.shop2parcel.api.exceptions import ImapTransientError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("NO", [b"[NONEXISTENT] Mailbox does not exist"])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(ImapTransientError, match="Failed to select INBOX"):
            client._fetch_sync(
                "imap.example.com",
                993,
                "user@example.com",
                "password",
                "ssl",
                'SUBJECT "shipped"',
                "8-May-2026",
            )


# ---------------------------------------------------------------------------
# FETCH loop — successful SEARCH + FETCH returning messages
# ---------------------------------------------------------------------------


def _fetch_tuple(raw_bytes: bytes) -> list:
    """Build an imaplib FETCH response: [(header_bytes, raw_bytes), b')']."""
    return [(b"1 (BODY[] {%d}" % len(raw_bytes), raw_bytes), b")"]


def test_fetch_returns_messages_with_uid_and_raw():
    """Successful SEARCH + FETCH yields list of {uid:int, raw:bytes} (covers the fetch loop)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    raw_a = b"From: a@example.com\r\nSubject: shipped A\r\n\r\nbody A"
    raw_b = b"From: b@example.com\r\nSubject: shipped B\r\n\r\nbody B"

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            return ("OK", [b"101 102"])
        if command == "FETCH":
            return ("OK", _fetch_tuple(raw_a if args[0] == "101" else raw_b))
        return ("OK", [None])

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"2"])
    mock_conn.uid.side_effect = uid_side_effect
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        results = client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    assert results == [
        {"uid": 101, "raw": raw_a},
        {"uid": 102, "raw": raw_b},
    ]


def test_fetch_skips_non_integer_uid():
    """A non-integer UID in the SEARCH result is skipped (covers the ValueError branch)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    raw = b"Subject: shipped\r\n\r\nbody"

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            return ("OK", [b"101 notanumber 102"])
        if command == "FETCH":
            return ("OK", _fetch_tuple(raw))
        return ("OK", [None])

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"3"])
    mock_conn.uid.side_effect = uid_side_effect
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        results = client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    # Only the two integer UIDs are fetched; "notanumber" is skipped.
    assert [r["uid"] for r in results] == [101, 102]


def test_fetch_skips_failed_fetch_response():
    """A FETCH returning non-OK / non-tuple data is skipped (covers the FETCH-failure branch)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            return ("OK", [b"101"])
        if command == "FETCH":
            return ("NO", [b"FETCH failed"])  # non-OK → skip
        return ("OK", [None])

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.uid.side_effect = uid_side_effect
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        results = client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    assert results == []


def test_fetch_skips_non_bytes_body():
    """A FETCH tuple whose body is not bytes is skipped (covers the non-bytes guard)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    def uid_side_effect(command, *args):
        if command == "SEARCH":
            return ("OK", [b"101"])
        if command == "FETCH":
            return ("OK", [(b"1 (BODY[] {3}", "not-bytes"), b")"])  # body is str, not bytes
        return ("OK", [None])

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.uid.side_effect = uid_side_effect
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        results = client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    assert results == []


# ---------------------------------------------------------------------------
# Error classification + connection lifecycle
# ---------------------------------------------------------------------------


def test_imap4_abort_raises_transient_error():
    """imaplib.IMAP4.abort is always transient (covers _classify_imap_error abort branch)."""
    from custom_components.shop2parcel.api.exceptions import ImapTransientError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    # abort even with "invalid" in the message must classify as transient, not auth.
    mock_conn.login.side_effect = imaplib.IMAP4.abort("command invalid in state AUTH")
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(ImapTransientError):
            client._fetch_sync(
                "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
            )


def test_unclassified_error_classified_as_transient():
    """A generic (non-imaplib) error is classified as transient (covers the fallback raise)."""
    from custom_components.shop2parcel.api.exceptions import ImapTransientError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.side_effect = OSError("network blip")  # generic → transient
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(ImapTransientError):
            client._fetch_sync(
                "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
            )


async def test_fetch_shipping_emails_propagates_auth_error_unwrapped():
    """fetch_shipping_emails re-raises ImapAuthError without re-wrapping (covers async re-raise)."""
    from custom_components.shop2parcel.api.exceptions import ImapAuthError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.side_effect = imaplib.IMAP4.error("LOGIN failed: bad password")
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        with pytest.raises(ImapAuthError):
            await client.fetch_shipping_emails(
                host="h",
                port=993,
                username="u",
                password="p",
                tls_mode="ssl",
                search_criteria='SUBJECT "shipped"',
                since_date="8-May-2026",
            )


async def test_fetch_shipping_emails_wraps_generic_error_as_transient():
    """fetch_shipping_emails routes an unclassified error through _classify (covers async fallback)."""
    from custom_components.shop2parcel.api.exceptions import ImapTransientError  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    async def _boom_executor(func, *args):
        raise OSError("DNS resolution failed")

    client = ImapClient(_boom_executor)
    with pytest.raises(ImapTransientError):
        await client.fetch_shipping_emails(
            host="h",
            port=993,
            username="u",
            password="p",
            tls_mode="ssl",
            search_criteria='SUBJECT "shipped"',
            since_date="8-May-2026",
        )


def test_logout_failure_is_swallowed():
    """A logout() that raises must not propagate — the result is still returned."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])  # empty SEARCH
    mock_conn.logout.side_effect = imaplib.IMAP4.error("logout boom")

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        results = client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    assert results == []
    mock_conn.logout.assert_called_once()


# ---------------------------------------------------------------------------
# CR-01 — TLS certificate verification
# ---------------------------------------------------------------------------


def test_ssl_mode_uses_verifying_context_by_default():
    """CR-01: IMAP4_SSL must receive a verifying ssl_context (CERT_REQUIRED + hostname check).

    imaplib's stdlib fallback context is CERT_NONE with check_hostname=False —
    a MITM could terminate TLS and harvest the LOGIN credentials.
    """
    import ssl  # noqa: PLC0415

    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn) as mock_ssl_cls:
        client = ImapClient(_inline_executor)
        client._fetch_sync(
            "imap.example.com", 993, "u", "p", "ssl", 'SUBJECT "shipped"', "8-May-2026"
        )

    ctx = mock_ssl_cls.call_args.kwargs.get("ssl_context")
    assert isinstance(ctx, ssl.SSLContext), "IMAP4_SSL must be given an explicit ssl_context"
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "default context must verify certificates"
    assert ctx.check_hostname is True, "default context must check the server hostname"


def test_ssl_mode_verify_tls_false_disables_verification():
    """CR-01: verify_tls=False (explicit user opt-out) yields CERT_NONE + no hostname check."""
    import ssl  # noqa: PLC0415

    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn) as mock_ssl_cls:
        client = ImapClient(_inline_executor)
        client._fetch_sync(
            "imap.example.com",
            993,
            "u",
            "p",
            "ssl",
            'SUBJECT "shipped"',
            "8-May-2026",
            False,  # verify_tls opt-out
        )

    ctx = mock_ssl_cls.call_args.kwargs.get("ssl_context")
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_starttls_mode_uses_verifying_context_by_default():
    """CR-01: starttls() must receive the same verifying ssl_context as the SSL path."""
    import ssl  # noqa: PLC0415

    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    mock_conn = MagicMock(spec=imaplib.IMAP4)
    mock_conn.starttls.return_value = ("OK", [b"tls started"])
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        client._fetch_sync(
            "imap.example.com", 143, "u", "p", "starttls", 'SUBJECT "shipped"', "8-May-2026"
        )

    ctx = mock_conn.starttls.call_args.kwargs.get("ssl_context")
    assert isinstance(ctx, ssl.SSLContext), "starttls() must be given an explicit ssl_context"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


async def test_fetch_shipping_emails_forwards_verify_tls():
    """CR-01: fetch_shipping_emails passes verify_tls through to _fetch_sync."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415

    captured: dict = {}

    async def _capturing_executor(func, *args):
        captured["args"] = args
        return []

    client = ImapClient(_capturing_executor)
    await client.fetch_shipping_emails(
        host="h",
        port=993,
        username="u",
        password="p",
        tls_mode="ssl",
        search_criteria='SUBJECT "shipped"',
        since_date="8-May-2026",
        verify_tls=False,
    )

    assert captured["args"][-1] is False, "verify_tls must reach the executor call"


# ---------------------------------------------------------------------------
# extract_text_body_imap / extract_html_body_imap — body extraction
# ---------------------------------------------------------------------------


def test_extract_text_body_imap_multipart():
    """extract_text_body_imap returns the text/plain part of a multipart message."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_text_body_imap,
    )

    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=b1\r\n\r\n"
        b"--b1\r\nContent-Type: text/plain\r\n\r\nHello plain world\r\n"
        b"--b1\r\nContent-Type: text/html\r\n\r\n<p>Hello HTML</p>\r\n"
        b"--b1--\r\n"
    )
    result = extract_text_body_imap(raw)
    assert result is not None
    assert "Hello plain world" in result


def test_extract_text_body_imap_single_part():
    """extract_text_body_imap returns the body of a non-multipart text/plain message."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_text_body_imap,
    )

    raw = b"Content-Type: text/plain\r\n\r\nSingle part plain body"
    assert extract_text_body_imap(raw) == "Single part plain body"


def test_extract_text_body_imap_bad_charset_falls_back_to_utf8():
    """A text/plain part with an unknown charset falls back to utf-8 decoding (LookupError branch)."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_text_body_imap,
    )

    raw = b'Content-Type: text/plain; charset="definitely-not-a-charset"\r\n\r\nfallback body'
    result = extract_text_body_imap(raw)
    assert result is not None
    assert "fallback body" in result


def test_extract_text_body_imap_multipart_bad_charset_falls_back_to_utf8():
    """A multipart text/plain part with an unknown charset falls back to utf-8 (LookupError branch)."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_text_body_imap,
    )

    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=b1\r\n\r\n"
        b'--b1\r\nContent-Type: text/plain; charset="definitely-not-a-charset"\r\n\r\n'
        b"multipart fallback body\r\n"
        b"--b1--\r\n"
    )
    result = extract_text_body_imap(raw)
    assert result is not None
    assert "multipart fallback body" in result


def test_extract_text_body_imap_returns_none_when_no_text_part():
    """extract_text_body_imap returns None when there is no text/plain content."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_text_body_imap,
    )

    raw = b"Content-Type: text/html\r\n\r\n<p>only html</p>"
    assert extract_text_body_imap(raw) is None


def test_extract_html_body_imap_single_part():
    """extract_html_body_imap returns the body of a non-multipart text/html message."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_html_body_imap,
    )

    raw = b"Content-Type: text/html\r\n\r\n<html><body>Single HTML</body></html>"
    result = extract_html_body_imap(raw)
    assert result is not None
    assert "Single HTML" in result


def test_extract_html_body_imap_bad_charset_multipart_falls_back_to_utf8():
    """A multipart text/html part with an unknown charset falls back to utf-8 (LookupError branch)."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_html_body_imap,
    )

    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=b1\r\n\r\n"
        b'--b1\r\nContent-Type: text/html; charset="definitely-not-a-charset"\r\n\r\n'
        b"<html>bad charset html</html>\r\n"
        b"--b1--\r\n"
    )
    result = extract_html_body_imap(raw)
    assert result is not None
    assert "bad charset html" in result


def test_extract_html_body_imap_bad_charset_single_part_falls_back_to_utf8():
    """A non-multipart text/html message with an unknown charset falls back to utf-8."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_html_body_imap,
    )

    raw = b'Content-Type: text/html; charset="definitely-not-a-charset"\r\n\r\n<html>x</html>'
    result = extract_html_body_imap(raw)
    assert result is not None
    assert "<html>x</html>" in result


def test_extract_html_body_imap_returns_none_when_no_html_part():
    """extract_html_body_imap returns None when there is no text/html content."""
    from custom_components.shop2parcel.api.imap_client import (  # noqa: PLC0415
        extract_html_body_imap,
    )

    raw = b"Content-Type: text/plain\r\n\r\njust plain text"
    assert extract_html_body_imap(raw) is None
