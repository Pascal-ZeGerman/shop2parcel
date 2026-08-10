"""Tests for the local keyword-matcher helpers in api/email_parser.py (D-01).

RED phase: build_keyword_matcher / matches_keyword_filter do not exist yet.

Follow-up to the gmail-query-drops-emails debug session
(.planning/debug/gmail-query-drops-emails.md): Gmail's server-side keyword
OR-chain search was removed because it silently dropped real shipment emails.
These helpers re-implement that keyword narrowing as deterministic local code,
applied by the coordinator (Task 2) to each fetched message right before the
expensive EmailParser.parse() call — never inside parse() itself, so the IMAP
path (which relies on its own deterministic IMAP SEARCH) is not double-filtered.
"""

from __future__ import annotations

import re

from custom_components.shop2parcel.api.email_parser import (
    build_keyword_matcher,
    matches_keyword_filter,
)
from custom_components.shop2parcel.const import DEFAULT_GMAIL_QUERY

# Real IZIMINI body text that the original bug silently dropped
# (see .planning/debug/gmail-query-drops-emails.md) — regression anchor.
_IZIMINI_BODY = "All items from your order 149164 have been shipped."
_IZIMINI_TRACKING_LINE = "The tracking number for these items is SBAAAAQLCQ6U4P269."


class TestBuildKeywordMatcher:
    """Tests 1-10: build_keyword_matcher(query) -> (pattern | None, dropped)."""

    def test_default_query_returns_pattern_and_no_dropped(self) -> None:
        pattern, dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert isinstance(pattern, re.Pattern)
        assert isinstance(dropped, list)
        assert dropped == []

    def test_default_pattern_matches_izimini_regression_text(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert pattern.search(_IZIMINI_BODY)
        assert pattern.search(_IZIMINI_TRACKING_LINE)

    def test_word_boundary_does_not_match_disorder_or_packaged(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert not pattern.search("This is a disorder in the system.")
        assert not pattern.search("Your items were packaged carefully.")

    def test_case_insensitive_matches_shipped_variants(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert pattern.search("SHIPPED")
        assert pattern.search("Shipped")

    def test_operator_tokens_dropped_not_matched_literally(self) -> None:
        pattern, dropped = build_keyword_matcher("from:shopify.com OR shipped")
        assert dropped == ["from:shopify.com"]
        assert pattern is not None
        assert pattern.search("has shipped")
        assert "shopify" not in pattern.pattern

    def test_negation_tokens_dropped_keeps_remaining_keyword(self) -> None:
        pattern, dropped = build_keyword_matcher("-label:spam shipped")
        assert any(tok.startswith("-") for tok in dropped)
        assert pattern is not None
        assert pattern.search("your order has shipped")

    def test_operator_only_query_fails_open(self) -> None:
        pattern, dropped = build_keyword_matcher("from:a@b.c -label:spam")
        assert pattern is None
        assert dropped != []

    def test_empty_and_whitespace_queries_fail_open_with_no_dropped(self) -> None:
        assert build_keyword_matcher("") == (None, [])
        assert build_keyword_matcher("   ") == (None, [])

    def test_or_and_and_are_separators_not_keywords(self) -> None:
        pattern, _dropped = build_keyword_matcher("tracking OR shipped AND order")
        assert pattern is not None
        assert not pattern.search("this or that")
        assert not pattern.search("bread and butter")

    def test_parentheses_and_quotes_stripped_from_tokens(self) -> None:
        pattern, dropped = build_keyword_matcher('(tracking OR "shipped")')
        assert pattern is not None
        assert dropped == []
        assert pattern.search("tracking")
        assert pattern.search("shipped")


class TestMatchesKeywordFilter:
    """Tests 11-14: matches_keyword_filter(pattern, *texts) -> bool."""

    def test_none_pattern_fails_open_regardless_of_texts(self) -> None:
        assert matches_keyword_filter(None, "irrelevant text") is True
        assert matches_keyword_filter(None) is True
        assert matches_keyword_filter(None, "") is True

    def test_matches_when_any_text_matches_subject_or_body(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        # Subject-only match.
        assert matches_keyword_filter(pattern, "Your order has shipped", "no keywords here") is True
        # Body-only match.
        assert matches_keyword_filter(pattern, "no keywords here", "Tracking info inside") is True

    def test_returns_false_when_no_text_matches(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert matches_keyword_filter(pattern, "hello world", "nothing relevant") is False

    def test_none_and_empty_strings_tolerated(self) -> None:
        pattern, _dropped = build_keyword_matcher(DEFAULT_GMAIL_QUERY)
        assert pattern is not None
        assert matches_keyword_filter(pattern, None, "", "your order has shipped") is True  # type: ignore[arg-type]
        assert matches_keyword_filter(pattern, None, "") is False  # type: ignore[arg-type]
