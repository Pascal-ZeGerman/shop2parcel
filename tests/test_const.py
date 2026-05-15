"""Tests for Shop2Parcel const.py — covers SCAN-03."""

from __future__ import annotations

from custom_components.shop2parcel.const import normalize_tracking_number

# -------- SCAN-03: normalize_tracking_number canonicalizes strings ----------


def test_normalize_strips_leading_whitespace():
    """SCAN-03: Leading whitespace is removed."""
    assert normalize_tracking_number("  ABC123") == "ABC123"


def test_normalize_strips_trailing_whitespace():
    """SCAN-03: Trailing whitespace is removed."""
    assert normalize_tracking_number("ABC123  ") == "ABC123"


def test_normalize_strips_both_ends():
    """SCAN-03: Whitespace stripped from both ends simultaneously."""
    assert normalize_tracking_number("  abc123  ") == "ABC123"


def test_normalize_uppercases_lowercase():
    """SCAN-03: Lowercase letters are uppercased."""
    assert normalize_tracking_number("abc123") == "ABC123"


def test_normalize_uppercases_mixed_case():
    """SCAN-03: Mixed-case input is fully uppercased."""
    assert normalize_tracking_number("AbC123xYz") == "ABC123XYZ"


def test_normalize_already_canonical_is_idempotent():
    """SCAN-03: Already-normalized input is returned unchanged."""
    assert normalize_tracking_number("1Z999AA10123456784") == "1Z999AA10123456784"


def test_normalize_tabs_and_newlines_stripped():
    """SCAN-03: Tab and newline whitespace is also stripped (not just spaces)."""
    assert normalize_tracking_number("\tabc123\n") == "ABC123"


def test_normalize_empty_string_returns_empty():
    """SCAN-03: Empty string input returns empty string without error."""
    assert normalize_tracking_number("") == ""


def test_normalize_whitespace_only_returns_empty():
    """SCAN-03: Whitespace-only input collapses to empty string."""
    assert normalize_tracking_number("   ") == ""
