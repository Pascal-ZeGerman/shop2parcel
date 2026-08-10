"""Tests for the sender-exclusion matcher helpers in api/email_parser.py.

RED phase: extract_sender_domain / build_sender_exclusion_matcher do not exist yet.

EXCLUDE-biased sibling of build_keyword_matcher/matches_keyword_filter
(tests/api/test_email_parser_keyword_filter.py) — deliberately the opposite
safety direction (see docstrings in api/email_parser.py: that filter biases
toward matching too much, this one biases toward excluding too little).
Validated against spike 027's 50-event real corpus: 28/28 confirmed-noise
events correctly excluded, 0/22 reliable events (including all 15 USPS
events) ever excluded.
"""

from __future__ import annotations

from custom_components.shop2parcel.api.email_parser import (
    build_sender_exclusion_matcher,
    extract_sender_domain,
)


class TestExtractSenderDomain:
    """extract_sender_domain(sender) -> str."""

    def test_bare_address(self) -> None:
        assert extract_sender_domain("no-reply@shopify.com") == "shopify.com"

    def test_display_name_address(self) -> None:
        sender = '"USPS Informed Delivery" <USPSInformeddelivery@email.informeddelivery.usps.com>'
        assert extract_sender_domain(sender) == "email.informeddelivery.usps.com"

    def test_lowercased(self) -> None:
        assert extract_sender_domain("No-Reply@SHOPIFY.COM") == "shopify.com"

    def test_no_at_sign_returns_empty(self) -> None:
        assert extract_sender_domain("plain text with no at sign") == ""

    def test_empty_string_returns_empty(self) -> None:
        assert extract_sender_domain("") == ""

    def test_none_returns_empty(self) -> None:
        assert extract_sender_domain(None) == ""  # type: ignore[arg-type]


class TestBuildSenderExclusionMatcher:
    """build_sender_exclusion_matcher(excluded_domains) -> Callable[[str], bool]."""

    def test_empty_list_fails_open(self) -> None:
        matcher = build_sender_exclusion_matcher([])
        assert matcher("digest@substack.com") is False
        assert matcher("anything@example.com") is False

    def test_blank_entries_dropped_fails_open(self) -> None:
        matcher = build_sender_exclusion_matcher(["  ", ""])
        assert matcher("digest@substack.com") is False

    def test_exact_domain_excluded(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com"])
        assert matcher("digest@substack.com") is True

    def test_unrelated_domain_not_excluded(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com"])
        assert matcher("a@notsubstack.com") is False

    def test_domain_suffix_is_not_matched_as_substring(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com"])
        assert matcher("a@substack.com.evil.net") is False

    def test_read_side_normalisation_strip_lower_leading_at(self) -> None:
        matcher = build_sender_exclusion_matcher(["  @SubStack.COM  "])
        assert matcher("digest@substack.com") is True

    def test_usps_informed_delivery_guard_parent_domain(self) -> None:
        """MANDATORY guard: 'usps.com' must never exclude the informed-delivery subdomain."""
        matcher = build_sender_exclusion_matcher(["usps.com"])
        assert matcher("USPSInformeddelivery@email.informeddelivery.usps.com") is False

    def test_usps_informed_delivery_guard_intermediate_subdomain(self) -> None:
        """MANDATORY guard: an intermediate subdomain entry also must not match."""
        matcher = build_sender_exclusion_matcher(["informeddelivery.usps.com"])
        assert matcher("USPSInformeddelivery@email.informeddelivery.usps.com") is False

    def test_usps_informed_delivery_full_literal_domain_excludes(self) -> None:
        """Only the exact, full literal domain excludes USPS Informed Delivery."""
        matcher = build_sender_exclusion_matcher(["email.informeddelivery.usps.com"])
        assert matcher("USPSInformeddelivery@email.informeddelivery.usps.com") is True

    def test_multiple_domains_excluded_independently(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com", "github.com"])
        assert matcher("a@substack.com") is True
        assert matcher("b@github.com") is True
        assert matcher("c@example.com") is False

    def test_tolerates_empty_sender_without_raising(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com"])
        assert matcher("") is False

    def test_tolerates_no_at_sender_without_raising(self) -> None:
        matcher = build_sender_exclusion_matcher(["substack.com"])
        assert matcher("plain text with no at sign") is False
