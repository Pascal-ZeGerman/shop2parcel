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


# ---------------------------------------------------------------------------
# IN-07 (api-layer review): per-service base classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("concrete_name", "base_name"),
    [
        ("GmailAuthError", "GmailError"),
        ("GmailTransientError", "GmailError"),
        ("ParcelAppAuthError", "ParcelAppError"),
        ("ParcelAppQuotaError", "ParcelAppError"),
        ("ParcelAppTransientError", "ParcelAppError"),
        ("ParcelAppInvalidTrackingError", "ParcelAppError"),
        ("ParcelAppAlreadyAddedError", "ParcelAppError"),
        ("ImapAuthError", "ImapError"),
        ("ImapTransientError", "ImapError"),
        ("OllamaTransientError", "OllamaError"),
        ("OllamaSchemaError", "OllamaError"),
    ],
)
def test_concrete_exception_derives_from_service_base(concrete_name, base_name):
    """IN-07: each concrete exception subclasses its per-service base (and Exception)."""
    from custom_components.shop2parcel.api import exceptions as exc_mod  # noqa: PLC0415

    concrete = getattr(exc_mod, concrete_name)
    base = getattr(exc_mod, base_name)
    assert inspect.isclass(concrete)
    assert issubclass(concrete, base)
    assert issubclass(base, Exception)


def test_already_added_is_not_invalid_tracking_subclass():
    """D-01 (preserved by IN-07): AlreadyAdded is NOT a subclass of InvalidTracking."""
    from custom_components.shop2parcel.api.exceptions import (  # noqa: PLC0415
        ParcelAppAlreadyAddedError,
    )

    assert not issubclass(ParcelAppAlreadyAddedError, ParcelAppInvalidTrackingError)
