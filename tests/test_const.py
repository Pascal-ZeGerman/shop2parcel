"""Tests for Shop2Parcel const.py — covers SCAN-03 + Phase-16 LOCKED_OLLAMA_FIELDS."""

from __future__ import annotations

from custom_components.shop2parcel.const import (
    LOCKED_FIELD_DESCRIPTIONS,
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


def test_locked_ollama_fields_has_exactly_four_entries():
    """FLD-01 + D-06: exactly four locked fields — tracking_number, carrier_name,
    order_name, and order_summary (LOH-SUMMARY).
    """
    assert len(LOCKED_OLLAMA_FIELDS) == 4


def test_locked_ollama_fields_order_and_values():
    """D-06: order is (tracking_number, carrier_name, order_name, order_summary);
    order-sensitive because build_schema emits required = list(LOCKED_OLLAMA_FIELDS)
    and JSON Schema ``required`` array order is observable downstream.
    """
    assert LOCKED_OLLAMA_FIELDS == (
        "tracking_number",
        "carrier_name",
        "order_name",
        "order_summary",
    )


def test_locked_field_descriptions_maps_order_summary_to_bespoke_str():
    """LOH-SUMMARY: LOCKED_FIELD_DESCRIPTIONS maps order_summary to a non-empty str."""
    assert isinstance(LOCKED_FIELD_DESCRIPTIONS, dict)
    assert "order_summary" in LOCKED_FIELD_DESCRIPTIONS
    desc = LOCKED_FIELD_DESCRIPTIONS["order_summary"]
    assert isinstance(desc, str)
    assert len(desc) > 0


def test_locked_field_descriptions_does_not_contain_other_locked_fields():
    """LOH-SUMMARY: only order_summary has a bespoke description; the other three locked
    fields are absent so they keep the None/auto-description behavior.
    """
    for name in ("tracking_number", "carrier_name", "order_name"):
        assert name not in LOCKED_FIELD_DESCRIPTIONS, (
            f"{name!r} must not appear in LOCKED_FIELD_DESCRIPTIONS"
        )


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
    assert LOCKED_OLLAMA_FIELDS == (
        "tracking_number",
        "carrier_name",
        "order_name",
        "order_summary",
    )


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


# -------- Phase 28 Plan 02 R6: DEFAULT_GMAIL_QUERY full-body (no subject: operator) -


def test_default_gmail_query_has_no_subject_operator():
    """R6: DEFAULT_GMAIL_QUERY must not contain 'subject:' — query must be full-body.

    Subject-only queries miss body-only carrier emails such as USPS Informed
    Delivery digests where the tracking number appears only in the message body.
    Removing the subject:(...) wrapper makes Gmail match against the full message.
    The strict carrier-format pre-POST gate (Plans 01/03/04) is the backstop.
    """
    from custom_components.shop2parcel.const import DEFAULT_GMAIL_QUERY

    assert "subject:" not in DEFAULT_GMAIL_QUERY


def test_default_gmail_query_contains_all_8_keywords():
    """R6: All 8 keywords must be present as tokens in DEFAULT_GMAIL_QUERY.

    Keywords: tracking, shipped, shipment, delivery, delivered, parcel, package, order.
    Each keyword is checked independently so that reordering does not break the test.
    """
    from custom_components.shop2parcel.const import DEFAULT_GMAIL_QUERY

    required_keywords = [
        "tracking",
        "shipped",
        "shipment",
        "delivery",
        "delivered",
        "parcel",
        "package",
        "order",
    ]
    for keyword in required_keywords:
        assert keyword in DEFAULT_GMAIL_QUERY, (
            f"Keyword '{keyword}' missing from DEFAULT_GMAIL_QUERY: {DEFAULT_GMAIL_QUERY!r}"
        )


def test_normalize_tracking_number_unchanged_after_r6():
    """D-03 regression: normalize_tracking_number still strip().upper() after R6 changes."""
    assert normalize_tracking_number("  9400111899223765496892  ") == "9400111899223765496892"
