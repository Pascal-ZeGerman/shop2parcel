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

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
def test_imap_client_is_importable():
    """D-06: ImapClient must be importable from api/imap_client.py."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415
    assert ImapClient is not None


@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
def test_imap_client_constructor_accepts_executor():
    """D-05: ImapClient.__init__ accepts a single Callable (executor injection)."""
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415
    client = ImapClient(async_add_executor_job=_inline_executor)
    assert hasattr(client, "_executor")


# ---------------------------------------------------------------------------
# Stub: D-09 read-only contract — STORE/EXPUNGE/COPY/MOVE never called
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
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
            "imap.example.com", 993, "user@example.com", "password",
            "ssl", 'SUBJECT "shipped"', None
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

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
async def test_fetch_shipping_emails_returns_tuple():
    """D-06: fetch_shipping_emails returns (list[dict], int|None) tuple."""
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
            since_uid=None,
        )

    assert isinstance(result, tuple)
    assert len(result) == 2
    emails, max_uid = result
    assert isinstance(emails, list)
    assert max_uid is None  # no messages found


# ---------------------------------------------------------------------------
# Stub: D-08 — EXAMINE (select readonly=True) is called, not SELECT
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
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
            "imap.example.com", 993, "user@example.com", "password",
            "ssl", 'SUBJECT "shipped"', None
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

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
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
                "imap.example.com", 993, "user@example.com", "wrong-password",
                "ssl", 'SUBJECT "shipped"', None
            )


# ---------------------------------------------------------------------------
# Stub: extract_html_body_imap function
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=False, reason="ImapClient not yet implemented — Plan 09-02")
def test_extract_html_body_imap_extracts_html():
    """D-06: extract_html_body_imap(raw_bytes) returns HTML string from RFC822 bytes."""
    from custom_components.shop2parcel.api.imap_client import extract_html_body_imap  # noqa: PLC0415

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
