"""Tests for Shop2Parcel const.py — covers SCAN-03 + Phase-16 LOCKED_OLLAMA_FIELDS."""

from __future__ import annotations

from custom_components.shop2parcel.const import (
    LOCKED_OLLAMA_FIELDS,
    normalize_tracking_number,
)

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


# -------- Phase 16: LOCKED_OLLAMA_FIELDS invariants (D-06, FLD-01) ----------


def test_locked_ollama_fields_is_tuple():
    """LOCKED_OLLAMA_FIELDS is an immutable tuple (consumed by build_schema)."""
    assert isinstance(LOCKED_OLLAMA_FIELDS, tuple)


def test_locked_ollama_fields_has_exactly_three_entries():
    """FLD-01 + D-06: exactly three locked fields, no more, no less."""
    assert len(LOCKED_OLLAMA_FIELDS) == 3


def test_locked_ollama_fields_order_and_values():
    """D-06: order is (tracking_number, carrier_name, order_name); order-sensitive
    because build_schema emits required = list(LOCKED_OLLAMA_FIELDS) and JSON
    Schema ``required`` array order is observable downstream.
    """
    assert LOCKED_OLLAMA_FIELDS == ("tracking_number", "carrier_name", "order_name")


def test_locked_ollama_fields_entries_are_strings():
    """Schema builder treats them as JSON property keys — must be str."""
    for name in LOCKED_OLLAMA_FIELDS:
        assert isinstance(name, str)


# -------- Phase 17: Ollama Stage-2 options constants (OLLM-01..03, QUE-01) --


def test_phase17_constants():
    """All eleven Phase 17 constants are importable with correct values."""
    from custom_components.shop2parcel.const import (
        CONF_CUSTOM_FIELDS,
        CONF_FIELD_DESCRIPTION,
        CONF_FIELD_NAME,
        CONF_OLLAMA_MODEL,
        CONF_OLLAMA_TIMEOUT,
        CONF_OLLAMA_URL,
        CONF_QUEUE_MAXLEN,
        CONF_STAGE2_ENABLED,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_OLLAMA_TIMEOUT,
        DEFAULT_QUEUE_MAXLEN,
    )

    # String key values
    assert CONF_OLLAMA_URL == "ollama_url"
    assert CONF_OLLAMA_MODEL == "ollama_model"
    assert CONF_OLLAMA_TIMEOUT == "ollama_timeout"
    assert CONF_QUEUE_MAXLEN == "queue_maxlen"
    assert CONF_CUSTOM_FIELDS == "custom_fields"
    assert CONF_STAGE2_ENABLED == "stage2_enabled"
    assert CONF_FIELD_NAME == "field_name"
    assert CONF_FIELD_DESCRIPTION == "field_description"

    # Default values
    assert DEFAULT_OLLAMA_MODEL == "qwen3.5:2b"
    assert DEFAULT_OLLAMA_TIMEOUT == 60
    assert DEFAULT_QUEUE_MAXLEN == 32


def test_phase17_no_default_stage2_enabled():
    """D-05: CONF_STAGE2_ENABLED is a derived bool — no DEFAULT_STAGE2_ENABLED constant."""
    import custom_components.shop2parcel.const as const_mod

    assert not hasattr(const_mod, "DEFAULT_STAGE2_ENABLED")


def test_phase17_existing_constants_unaffected():
    """Regression: existing constants (DOMAIN, CONF_POLL_INTERVAL, LOCKED_OLLAMA_FIELDS) intact."""
    from custom_components.shop2parcel.const import (
        CONF_POLL_INTERVAL,
        DOMAIN,
        LOCKED_OLLAMA_FIELDS,
    )

    assert DOMAIN == "shop2parcel"
    assert CONF_POLL_INTERVAL == "poll_interval"
    assert LOCKED_OLLAMA_FIELDS == ("tracking_number", "carrier_name", "order_name")


# -------- Phase 21 Plan 02: FAIL-04 notification ID helper + threshold constants ---


def test_stage2_failing_notification_id_per_entry_format():
    """FAIL-04: stage2_failing_notification_id returns correct prefixed ID for a given entry."""
    from custom_components.shop2parcel.const import stage2_failing_notification_id

    assert stage2_failing_notification_id("abc123") == "shop2parcel_stage2_failing_abc123"


def test_stage2_failing_notification_id_distinct_from_cap():
    """FAIL-04: stage2_failing_notification_id uses a different prefix than stage2_cap_notification_id.

    Ensures notifications from FAIL-04 (consecutive failures) and MRG-05 (per-poll POST cap)
    do not overwrite each other in HA's notification panel (T-21-02-04 + P14-WR-03 pattern).
    """
    from custom_components.shop2parcel.const import (
        stage2_cap_notification_id,
        stage2_failing_notification_id,
    )

    assert stage2_failing_notification_id("abc") != stage2_cap_notification_id("abc")


def test_stage2_notify_threshold_default():
    """FAIL-04: STAGE2_NOTIFY_THRESHOLD is 3 — notification fires after 3 consecutive failures."""
    from custom_components.shop2parcel.const import STAGE2_NOTIFY_THRESHOLD

    assert STAGE2_NOTIFY_THRESHOLD == 3


def test_stage2_notify_cooldown_default():
    """FAIL-04: STAGE2_NOTIFY_COOLDOWN_S is 3600 — 1-hour cooldown between re-fires."""
    from custom_components.shop2parcel.const import STAGE2_NOTIFY_COOLDOWN_S

    assert STAGE2_NOTIFY_COOLDOWN_S == 3600
