"""Tests for the Shop2Parcel exception taxonomy.

Tests are written per TDD RED phase — exceptions.py must be green after these are written.
Security note: No API tokens or credentials appear in these tests.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from custom_components.shop2parcel.api.exceptions import (
    GmailAuthError,
    GmailTransientError,
    ImapAuthError,
    ImapTransientError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)


def test_gmail_auth_error_is_exception():
    err = GmailAuthError("token expired")
    assert isinstance(err, Exception)
    assert str(err) == "token expired"


def test_gmail_transient_error_is_exception():
    err = GmailTransientError("network failure")
    assert isinstance(err, Exception)


def test_parcelapp_quota_error_reset_at_default():
    err = ParcelAppQuotaError("quota exhausted")
    assert err.reset_at is None


def test_parcelapp_quota_error_reset_at_set():
    err = ParcelAppQuotaError("quota exhausted", reset_at=1745452800)
    assert err.reset_at == 1745452800


def test_parcelapp_auth_error_is_exception():
    err = ParcelAppAuthError("bad key")
    assert isinstance(err, Exception)


def test_parcelapp_transient_error_is_exception():
    err = ParcelAppTransientError("5xx")
    assert isinstance(err, Exception)


def test_parcelapp_invalid_tracking_error_is_exception():
    err = ParcelAppInvalidTrackingError("bad number")
    assert isinstance(err, Exception)


def test_imap_auth_error_is_exception():
    err = ImapAuthError("login failed")
    assert isinstance(err, Exception)
    assert str(err) == "login failed"


def test_imap_transient_error_is_exception():
    err = ImapTransientError("connection reset")
    assert isinstance(err, Exception)
    assert str(err) == "connection reset"


def test_no_ha_imports_in_exceptions():
    exceptions_path = (
        Path(__file__).parent.parent.parent
        / "custom_components"
        / "shop2parcel"
        / "api"
        / "exceptions.py"
    )
    source = exceptions_path.read_text(encoding="utf-8")
    assert "homeassistant" not in source, "exceptions.py must not import from homeassistant.*"
