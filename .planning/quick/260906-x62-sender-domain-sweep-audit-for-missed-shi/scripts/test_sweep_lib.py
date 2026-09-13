#!/usr/bin/env python3
"""Standalone offline regression runner for sweep_lib.py.

Not a pytest module (deliberately) — this file lives under .planning/, which
is outside [tool.pytest.ini_options] testpaths, so the normal suite never
collects it. Plain executable script: bare asserts, run directly.

Usage: .venv/bin/python test_sweep_lib.py
Exits 0 and prints "SELF-TEST OK" when every check passes; makes zero network
calls anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sweep_lib as sl  # noqa: E402 — path insert must precede this import


def test_extract_domain() -> None:
    assert sl.extract_domain("COLAMY <noreply@orders.colamyhome.com>") == "orders.colamyhome.com"
    assert sl.extract_domain("") is None
    assert sl.extract_domain("Just A Display Name") is None
    assert sl.extract_domain("garbage no at sign") is None
    assert sl.extract_domain("Mixed CASE <Noreply@Example.COM>") == "example.com"


def test_base_domain() -> None:
    assert sl.base_domain("orders.colamyhome.com") == "colamyhome.com"
    assert sl.base_domain("mail.tracking.co.uk") == "tracking.co.uk"
    assert sl.base_domain("colamyhome.com") == "colamyhome.com"
    assert sl.base_domain("bounce.17track.net") == "17track.net"


def test_derive_seed_domains() -> None:
    headers = [
        "COLAMY <noreply@orders.colamyhome.com>",
        "17TRACK <no-reply@bounce.17track.net>",
        "Me <pascal@gmail.com>",  # excluded (freemail/self)
        "USPS <informeddelivery@email.informeddelivery.usps.com>",
        "garbage no at sign",
    ]
    domains, provenance = sl.derive_seed_domains(
        headers,
        extra_domains=["extra-carrier.example"],
        excluded_domains=sl.DEFAULT_EXCLUDED_DOMAINS,
    )
    assert domains == sorted(domains)
    assert "gmail.com" not in domains
    # codeql[py/incomplete-url-substring-sanitization]: list-membership assertion
    # against `derive_seed_domains`' own output, not a URL/security boundary check.
    assert "colamyhome.com" in domains  # lgtm[py/incomplete-url-substring-sanitization]
    assert "17track.net" in domains  # lgtm[py/incomplete-url-substring-sanitization]
    assert "usps.com" in domains  # lgtm[py/incomplete-url-substring-sanitization]
    assert "extra-carrier.example" in domains
    assert provenance["colamyhome.com"] == ["orders.colamyhome.com"]
    # deduped: two headers mapping to the same base domain collapse to one entry
    assert len(domains) == len(set(domains))


def test_build_seen_keys_imap_three_spellings() -> None:
    # The single most likely false-positive source: seen_message_ids uses the
    # uidvalidity:uid spelling while persisted_shipments uses imap:uid.
    persisted = {
        "imap:1001": {
            "tracking_number": "TN1",
            "carrier_name": "usps",
            "order_name": "Order A",
            "message_id": "imap:1001",
            "email_date": 1,
        },
        "imap:1002::TN2": {
            "tracking_number": "TN2",
            "carrier_name": "ups",
            "order_name": "Order B",
            "message_id": "imap:1002",
            "email_date": 2,
        },
    }
    seen_ids = ["500:1000"]  # uidvalidity:uid spelling
    keys = sl.build_seen_keys(persisted, seen_ids)

    assert "500:1000" in keys
    assert "1000" in keys  # bare uid parsed from uidvalidity:uid
    assert "imap:1001" in keys
    assert "1001" in keys  # bare uid also registered via the message_id field
    # And persisted_shipments key "imap:1002::TN2" registers the uid_key prefix.
    assert "imap:1002" in keys
    assert "imap:1002::TN2" in keys
    assert "1002" in keys


def test_is_candidate_imap() -> None:
    persisted = {
        "imap:1001": {
            "tracking_number": "TN1",
            "carrier_name": "usps",
            "order_name": "Order A",
            "message_id": "imap:1001",
            "email_date": 1,
        },
    }
    seen_ids = ["500:1000"]
    keys = sl.build_seen_keys(persisted, seen_ids)

    # Already known via imap: spelling.
    assert sl.is_candidate("imap", "1001", uidvalidity=500, seen_keys=keys) is False
    # Already known via uidvalidity:uid spelling.
    assert sl.is_candidate("imap", "1000", uidvalidity=500, seen_keys=keys) is False
    # A UIDVALIDITY change since last poll: bare uid still registered.
    assert sl.is_candidate("imap", "1000", uidvalidity=999, seen_keys=keys) is False
    # Genuinely new uid.
    assert sl.is_candidate("imap", "9999", uidvalidity=500, seen_keys=keys) is True


def test_is_candidate_gmail() -> None:
    persisted = {
        "abc123": {
            "tracking_number": "TN1",
            "carrier_name": "usps",
            "order_name": "Order A",
            "message_id": "abc123",
            "email_date": 1,
        }
    }
    keys = sl.build_seen_keys(persisted, [])
    assert sl.is_candidate("gmail", "abc123", uidvalidity=None, seen_keys=keys) is False
    assert sl.is_candidate("gmail", "zzz999", uidvalidity=None, seen_keys=keys) is True


def test_render_report_secret_leak_regression() -> None:
    fake_password = "SUPER-SECRET-PASSWORD-1234"  # noqa: S105 — obviously fake literal
    fake_token = "ya29.FAKE-OAUTH-ACCESS-TOKEN-ABCDEF"  # noqa: S105 — obviously fake

    account = sl.Account(
        entry_id="entry1",
        title="fake-imap-account",
        kind="imap",
        imap_password=fake_password,
        token={"access_token": fake_token},
    )
    # Prove __repr__ itself is secret-safe.
    assert fake_password not in repr(account)
    assert fake_token not in repr(account)

    sections = {
        "fake-imap-account": [
            sl.Candidate(
                account_title="fake-imap-account",
                sender="noreply@example.com",
                subject="Your order has shipped",
                date="2026-08-01",
                ident="imap:42",
            )
        ]
    }
    meta = {
        "window_months": 12,
        "run_timestamp": "2026-09-06T00:00:00Z",
        "seed_domains": {"example.com": ["noreply.example.com"]},
    }
    report = sl.render_report(sections, meta)
    assert fake_password not in report
    assert fake_token not in report
    # codeql[py/incomplete-url-substring-sanitization]: presence-in-rendered-report
    # assertion, not a URL/security boundary check.
    assert "example.com" in report  # lgtm[py/incomplete-url-substring-sanitization]
    assert "Your order has shipped" in report


def test_render_report_grouping_and_header() -> None:
    sections = {
        "Account A": [
            sl.Candidate("Account A", "s1@x.com", "Subj 1", "2026-01-01", "id1"),
            sl.Candidate("Account A", "s2@x.com", "Subj 2", "2026-02-01", "id2"),
        ],
        "Account B": [],
    }
    meta = {
        "window_months": 12,
        "run_timestamp": "2026-09-06T00:00:00Z",
        "seed_domains": {"x.com": ["s.x.com"]},
    }
    report = sl.render_report(sections, meta)
    assert "## Account A" in report
    assert "## Account B" in report
    assert "No candidates." in report
    assert "Sender" in report and "Subject" in report and "Date" in report
    assert "last 12 months" in report
    # codeql[py/incomplete-url-substring-sanitization]: presence-in-rendered-report
    # assertion, not a URL/security boundary check.
    assert "x.com" in report  # lgtm[py/incomplete-url-substring-sanitization]
    assert "Total candidates: 2" in report
    # Newest-first ordering within a section.
    idx1 = report.index("Subj 1")
    idx2 = report.index("Subj 2")
    assert idx2 < idx1  # Subj 2 (2026-02-01) sorts before Subj 1 (2026-01-01)
    # Running numbers: a continuous "#" column so entries can be referenced by
    # a single number regardless of which account section they fall in.
    assert "| # | Sender | Subject | Date | ID |" in report
    assert "| 1 | s2@x.com | Subj 2 | 2026-02-01 | id2 |" in report
    assert "| 2 | s1@x.com | Subj 1 | 2026-01-01 | id1 |" in report


def test_resolve_google_client(tmp_path) -> None:
    creds_path = tmp_path / "application_credentials"
    creds_path.write_text(
        """
        {
          "data": {
            "items": [
              {
                "id": "cred-by-id",
                "domain": "shop2parcel",
                "client_id": "12345.apps.googleusercontent.com",
                "client_secret": "FAKE-SECRET-1",
                "auth_domain": "cred-auth-domain"
              }
            ]
          }
        }
        """
    )

    # Match by exact id.
    client_id, secret = sl.resolve_google_client(creds_path, "cred-by-id")
    assert client_id == "12345.apps.googleusercontent.com"
    assert secret == "FAKE-SECRET-1"

    # Match by exact auth_domain.
    client_id, secret = sl.resolve_google_client(creds_path, "cred-auth-domain")
    assert client_id == "12345.apps.googleusercontent.com"

    # Match by normalized shop2parcel_ + sanitized(client_id) spelling.
    sanitized = "shop2parcel_12345_apps_googleusercontent_com"
    client_id, secret = sl.resolve_google_client(creds_path, sanitized)
    assert client_id == "12345.apps.googleusercontent.com"

    # No match: raises, names the file and the missing auth_implementation.
    try:
        sl.resolve_google_client(creds_path, "nonexistent-implementation")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as err:
        msg = str(err)
        assert "nonexistent-implementation" in msg
        assert str(creds_path) in msg
        assert "FAKE-SECRET-1" not in msg  # never leak the secret in the error


def test_load_store_type_guards(tmp_path) -> None:
    storage_dir = tmp_path
    entry_id = "entry-x"
    store_path = storage_dir / f"shop2parcel.{entry_id}"
    store_path.write_text(
        """
        {
          "data": {
            "persisted_shipments": "not-a-dict",
            "seen_message_ids": "not-a-list"
          }
        }
        """
    )
    persisted, seen_ids = sl.load_store(storage_dir, entry_id)
    assert persisted == {}
    assert seen_ids == []


def test_gmail_and_imap_seed_extraction() -> None:
    # Real coordinator.py:2483 stores ShipmentData.message_id verbatim — bare Gmail
    # ids and bare IMAP uids, never an "imap:" prefix (that prefix is only used in
    # imap_coordinator.py's _emit_scan_event payload, a separate event-bus concern).
    # Each account's own store contains only its own kind, disambiguated by the
    # caller (account.kind) before either function is called — no prefix filtering
    # needed or correct here. Regression for the real bug this caught: both
    # functions used to filter on a startswith("imap:") check that never matched
    # real data, silently returning [] for every IMAP account.
    gmail_persisted = {"abc123": {"message_id": "abc123"}}
    imap_persisted = {"1:1001": {"message_id": "1001"}}
    assert sl.gmail_seed_message_ids(gmail_persisted) == ["abc123"]
    assert sl.imap_seed_uids(imap_persisted) == ["1001"]


def test_parse_uid_from_fetch_marker() -> None:
    # Regression for a real bug caught against a live mailbox: naively
    # grabbing "the first digit token" from a UID FETCH marker returns the
    # sequence number (which always comes first), not the requested UID.
    # Every fetched result was silently mislabeled with a small ~17000-range
    # sequence number instead of the true ~1646143xxx UID — corrupting the
    # candidate-diff logic downstream, not just cosmetic.
    marker = "17417 (UID 1646143839 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {123}"
    assert sl.parse_uid_from_fetch_marker(marker) == "1646143839"

    # Simpler/older server response shape, no BODY[...] fields spelled out.
    marker2 = "42 (UID 1001)"
    assert sl.parse_uid_from_fetch_marker(marker2) == "1001"

    # No "UID" token present at all — caller falls back to positional pairing.
    assert sl.parse_uid_from_fetch_marker("garbage response with no uid") is None


def main() -> int:
    tests = [
        test_extract_domain,
        test_base_domain,
        test_derive_seed_domains,
        test_build_seen_keys_imap_three_spellings,
        test_is_candidate_imap,
        test_is_candidate_gmail,
        test_render_report_secret_leak_regression,
        test_render_report_grouping_and_header,
        test_gmail_and_imap_seed_extraction,
        test_parse_uid_from_fetch_marker,
    ]
    import tempfile

    for test in tests:
        test()
        print(f"  ok: {test.__name__}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_resolve_google_client(tmp_path)
        print("  ok: test_resolve_google_client", file=sys.stderr)
        test_load_store_type_guards(tmp_path)
        print("  ok: test_load_store_type_guards", file=sys.stderr)

    print("SELF-TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
