"""Cross-coordinator parity tests: Gmail and IMAP agree on _run_inline_fallback outcomes.

Phase 33 Plan 04 (PAR-04, D-03): a dedicated parametrized module that feeds the
same email scenario through both ``GmailCoordinator`` and ``ImapCoordinator`` and
asserts identical ``_enqueue_stage2`` decisions at the call boundary across the
full branch matrix: first-refresh, cap-reached, budget-exhausted, carrier-reject,
dedup-hit, enqueue-success, extractor-None, and multi-shipment-digest.

Both coordinators are already wired to the shared ``Shop2ParcelCoordinator.
_run_inline_fallback()`` gatekeeper (Plans 33-02, 33-03), so these tests are
green on write — they are the encoded parity audit (PAR-04: "the audit is
encoded as tests").

Assertions target ONLY the ``_enqueue_stage2`` / ``_mark_message_seen`` call
boundary (Q2=A) — never ``Stage2Job`` internals or queue state, since Phase 32's
hub-queue rewire may change internals independently of this phase. No assertion
equates ``email_date`` between the two coordinators: Gmail always has a real
epoch, IMAP passes the synthetic ``0`` (Pitfall 2) — that divergence is
intentional and pre-existing, not something parity should paper over.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.shop2parcel.const import (
    MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL,
)
from custom_components.shop2parcel.extractors.types import Stage2Result
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator
from custom_components.shop2parcel.imap_coordinator import ImapCoordinator

# Don't Hand-Roll (RESEARCH.md): reuse the existing stage2-enabled entry fixtures
# rather than duplicating entry shapes. Importing a @pytest.fixture-decorated
# function into this module's namespace registers it as a fixture usable here
# too (pytest discovers fixtures via module attributes, not just conftest.py).
from tests.test_gmail_coordinator import mock_stage2_entry  # noqa: F401
from tests.test_imap_coordinator import mock_imap_stage2_entry  # noqa: F401

# (coordinator_cls, entry_fixture_name, scan-event prefix, email_date)
# Gmail always carries a real epoch; IMAP passes the synthetic 0 (Pitfall 2) —
# this divergence is intentional and must never be asserted as a parity failure.
COORDINATOR_PARAMS = [
    pytest.param(GmailCoordinator, "mock_stage2_entry", "gmail:", 1700000000, id="gmail"),
    pytest.param(ImapCoordinator, "mock_imap_stage2_entry", "imap:", 0, id="imap"),
]


def _valid_stage2_result(tn: str = "1Z999AA10123456784") -> Stage2Result:
    """Build a Stage2Result carrying a valid, not-yet-submitted carrier-format TN."""
    return Stage2Result(
        locked={
            "tracking_number": tn,
            "carrier_name": "UPS",
            "order_name": "#parity-1001",
        },
        custom={},
        passes_used=1,
        latency_ms=10.0,
    )


async def _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture):
    """Construct + load a coordinator past its first refresh, ready for a direct
    ``_run_inline_fallback`` call (mirrors the direct-call harness added in
    Plan 33-03's IMAP branch-matrix tests, applied identically to both classes).
    """
    entry = request.getfixturevalue(entry_fixture)
    entry.add_to_hass(hass)
    coord = coordinator_cls(hass, entry)
    await coord._async_load_store()
    coord._diagnostics.stage2_enabled = True
    coord._first_refresh_done = True
    coord._reset_stage2_poll_counters()
    return coord


# ---------------------------------------------------------------------------
# Branch-matrix parity tests (PAR-04 / R4) — one test per condition, each
# parametrized over both coordinator classes so a divergence between Gmail and
# IMAP fails the SAME test id for both, making the parity gap immediately visible.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_first_refresh_skip_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """E1: on the first refresh, neither coordinator runs inline extraction and
    both leave the message un-marked/re-inspectable (bootstrap-window guard)."""
    entry = request.getfixturevalue(entry_fixture)
    entry.add_to_hass(hass)
    coord = coordinator_cls(hass, entry)
    await coord._async_load_store()
    coord._diagnostics.stage2_enabled = True
    # Deliberately NOT setting _first_refresh_done — defaults False.
    assert coord._first_refresh_done is False
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result())
    coord._extractor = mock_extractor
    coord._reset_stage2_poll_counters()

    msg_key = "parity-first-refresh"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_extractor.async_extract.assert_not_awaited()
    mock_enqueue.assert_not_called()
    assert msg_key not in coord._seen_message_ids
    assert msg_key not in coord._inflight_message_ids


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_cap_reached_defers_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """Boundary (R2/R5): with the per-poll cap already at MAX, both coordinators
    skip extraction and leave the message un-marked (retried next poll)."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result())
    coord._extractor = mock_extractor
    coord._stage2_fallback_extractions_this_poll = MAX_STAGE2_FALLBACK_EXTRACTIONS_PER_POLL

    msg_key = "parity-cap-reached"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_extractor.async_extract.assert_not_awaited()
    mock_enqueue.assert_not_called()
    assert msg_key not in coord._seen_message_ids
    assert msg_key not in coord._inflight_message_ids


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_budget_exhausted_defers_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """Boundary (R2/R5): with the wall-clock budget already past its deadline,
    both coordinators defer extraction and leave the message un-marked."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result())
    coord._extractor = mock_extractor
    coord._stage2_fallback_inline_deadline = 0.0  # always-past deadline

    msg_key = "parity-budget-exhausted"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_extractor.async_extract.assert_not_awaited()
    mock_enqueue.assert_not_called()
    assert msg_key not in coord._seen_message_ids
    assert msg_key not in coord._inflight_message_ids


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_carrier_reject_no_enqueue_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """R1/R3: a fallback result whose tracking number fails validate_carrier_format
    is rejected identically by both coordinators — no enqueue, message marked seen
    (terminal — the gatekeeper does not re-run Ollama on a non-shipment email)."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(
        return_value=_valid_stage2_result(tn="ORDER-12345")  # fails no_carrier_match
    )
    coord._extractor = mock_extractor

    msg_key = "parity-carrier-reject"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_enqueue.assert_not_called()
    assert msg_key in coord._seen_message_ids, (
        "A carrier-format-rejected fallback result must be marked seen (terminal) "
        "identically for both coordinators"
    )


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_dedup_hit_no_enqueue_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """R4/R5: a tracking number already present in the shared hub's dedup set is
    rejected identically by both coordinators — no re-enqueue, message marked seen."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    tn = "1Z999AA10123456784"
    assert coord._hub is not None
    coord._hub.check_and_mark(tn)  # pre-seed dedup via the shared hub (D-05 unaffected)

    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result(tn=tn))
    coord._extractor = mock_extractor

    msg_key = "parity-dedup-hit"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_enqueue.assert_not_called()
    assert msg_key in coord._seen_message_ids, (
        "An already-submitted tracking number must be marked seen (terminal) "
        "identically for both coordinators"
    )


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_enqueue_success_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """R2: a valid, not-yet-submitted carrier-format TN is enqueued identically by
    both coordinators, with the message left un-marked/re-inspectable (convergence
    via the hub dedup re-fetch) but pinned in the in-flight set."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    tn = "1Z999AA10123456784"
    mock_extractor = AsyncMock()
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result(tn=tn))
    coord._extractor = mock_extractor

    msg_key = "parity-enqueue-success"
    with patch.object(coordinator_cls, "_enqueue_stage2", return_value=True) as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args.args[0] == tn, (
        f"Expected _enqueue_stage2 called with the extracted TN, "
        f"got args={mock_enqueue.call_args.args}"
    )
    assert msg_key in coord._inflight_message_ids
    assert msg_key not in coord._seen_message_ids


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_extractor_none_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """R1: when _extractor is None (transiently unavailable), both coordinators
    leave the message un-marked/re-inspectable (identical extractor-unavailable
    branch) — no enqueue, no seen-mark."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    coord._extractor = None

    msg_key = "parity-extractor-none"
    with patch.object(coordinator_cls, "_enqueue_stage2") as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html="<html>shipping body</html>",
            meta={"subject": "s", "from": "f"},
            email_date=email_date,
            candidate_tokens=[],
            debug_mode=False,
        )

    mock_enqueue.assert_not_called()
    assert msg_key not in coord._seen_message_ids
    assert msg_key not in coord._inflight_message_ids


@pytest.mark.parametrize("coordinator_cls,entry_fixture,prefix,email_date", COORDINATOR_PARAMS)
async def test_multi_shipment_digest_single_enqueue_identical(
    hass, request, coordinator_cls, entry_fixture, prefix, email_date
):
    """Assumption A2 (RESEARCH.md Pitfall 6): the inline fallback path is
    structurally single-shipment — Stage2Result carries exactly one
    tracking_number, unlike the Stage-1 HIT path's extra_shipments list. A digest
    email whose body contains MULTIPLE tracking-number-shaped strings, fully
    missed by Stage-1, still yields exactly ONE _enqueue_stage2 call once the
    fallback extracts a single TN — asserted identically for both coordinators."""
    coord = await _build_ready_coordinator(hass, request, coordinator_cls, entry_fixture)
    tn = "1Z999AA10123456784"
    digest_html = (
        "<html><body><p>Package 1: 1Z999AA10123456784</p>"
        "<p>Package 2: 9400111899223344556677</p>"
        "<p>Package 3: 1Z888BB20987654321</p></body></html>"
    )
    mock_extractor = AsyncMock()
    # The extractor returns exactly ONE locked tracking_number regardless of how
    # many TN-shaped strings appear in the digest body (Stage2Result is
    # structurally single-shipment) — the fallback path never iterates multiple
    # shipments from a single Ollama call (Assumption A2).
    mock_extractor.async_extract = AsyncMock(return_value=_valid_stage2_result(tn=tn))
    coord._extractor = mock_extractor

    msg_key = "parity-multi-shipment-digest"
    with patch.object(coordinator_cls, "_enqueue_stage2", return_value=True) as mock_enqueue:
        await coord._run_inline_fallback(
            msg_key=msg_key,
            prefix=prefix,
            html=digest_html,
            meta={"subject": "Your 3 packages have shipped", "from": "f"},
            email_date=email_date,
            candidate_tokens=["1Z999AA10123456784", "9400111899223344556677"],
            debug_mode=False,
        )

    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args.args[0] == tn


# ---------------------------------------------------------------------------
# Instance-isolation parity test (E2/D-05) — Task 2
# ---------------------------------------------------------------------------


async def test_instance_isolation_no_state_bleed(hass, request):
    """E2/D-05/P4: a GmailCoordinator and an ImapCoordinator constructed in the
    same test, driven with the SAME string key, never share prefetch/in-flight/
    seen state. Instance isolation (not key-namespacing code) is what satisfies
    this — each coordinator's __init__ creates its OWN dict objects, so equal
    string keys structurally cannot collide across instances."""
    gmail_entry = request.getfixturevalue("mock_stage2_entry")
    gmail_entry.add_to_hass(hass)
    imap_entry = request.getfixturevalue("mock_imap_stage2_entry")
    imap_entry.add_to_hass(hass)

    gmail_coord = GmailCoordinator(hass, gmail_entry)
    imap_coord = ImapCoordinator(hass, imap_entry)
    await gmail_coord._async_load_store()
    await imap_coord._async_load_store()

    shared_key = "COLLIDING-KEY-123"

    # Drive both coordinators into an in-flight + seen + prefetch-cache state for
    # the SAME string key.
    gmail_coord._mark_inflight(shared_key)
    gmail_coord._mark_message_seen(shared_key)
    gmail_coord._fallback_prefetch_cache[shared_key] = _valid_stage2_result()

    imap_coord._mark_inflight(shared_key)
    imap_coord._mark_message_seen(shared_key)
    imap_coord._fallback_prefetch_cache[shared_key] = _valid_stage2_result(
        tn="9400111899223344556677"
    )

    # (a) The four shared-state maps are genuinely distinct dict objects per
    # coordinator instance — not just equal-by-value, but `is not` at the object
    # level (D-05: instance isolation, no shared hub map for these four dicts).
    assert gmail_coord._seen_message_ids is not imap_coord._seen_message_ids
    assert gmail_coord._inflight_message_ids is not imap_coord._inflight_message_ids
    assert gmail_coord._fallback_prefetch_cache is not imap_coord._fallback_prefetch_cache
    assert (
        gmail_coord._stage2_inline_schema_failures
        is not imap_coord._stage2_inline_schema_failures
    )

    # (b) Each coordinator's maps contain only its own entry for the shared key —
    # writing to one instance's dict never mutates the other's, even though both
    # entries are keyed by the identical string.
    assert shared_key in gmail_coord._seen_message_ids
    assert shared_key in imap_coord._seen_message_ids
    assert len(gmail_coord._seen_message_ids) == 1
    assert len(imap_coord._seen_message_ids) == 1

    assert shared_key in gmail_coord._inflight_message_ids
    assert shared_key in imap_coord._inflight_message_ids
    assert len(gmail_coord._inflight_message_ids) == 1
    assert len(imap_coord._inflight_message_ids) == 1

    assert gmail_coord._fallback_prefetch_cache[shared_key].locked["tracking_number"] == (
        "1Z999AA10123456784"
    )
    assert imap_coord._fallback_prefetch_cache[shared_key].locked["tracking_number"] == (
        "9400111899223344556677"
    )
