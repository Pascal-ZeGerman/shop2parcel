"""Phase 18: Stage-2 queue plumbing tests — QUE-01, QUE-03, QUE-06, QUE-07."""

from __future__ import annotations

import asyncio
import logging
import pathlib
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ParseResult, ShipmentData
from custom_components.shop2parcel.const import (
    CONF_OLLAMA_URL,
    CONF_QUEUE_MAXLEN,
    DEFAULT_QUEUE_MAXLEN,
    DOMAIN,
    STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
)
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator, Stage2Job
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator


def _make_shipment(message_id: str = "msg1") -> ShipmentData:
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1700000000,
    )


def _make_parse_result(
    shipment: ShipmentData | None,
    *,
    skip_reason: str | None = None,
    strategy_used: str | None = "html_template",
    keyword_hits: dict[str, bool] | None = None,
) -> ParseResult:
    if keyword_hits is None:
        keyword_hits = {"tracking_regex": False, "order_regex": False, "carrier_regex": False}
    return ParseResult(
        shipment=shipment,
        skip_reason=skip_reason if shipment is None else None,
        strategy_used=strategy_used if shipment is not None else None,
        keyword_hits=keyword_hits,
    )


@pytest.fixture
def mock_stage2_config_entry() -> MockConfigEntry:
    """MockConfigEntry with stage2_enabled=True (has ollama_url in options)."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        options={
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
        },
        unique_id="user@gmail.com",
    )


def _patch_coord_deps():
    """Return a context manager stack that suppresses all external I/O for coordinator tests."""
    return (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore"),
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Stage2Job is a frozen dataclass (QUE-01 partial)
# ---------------------------------------------------------------------------


def test_stage2job_is_frozen():
    """Stage2Job must be a frozen dataclass — assigning any field raises FrozenInstanceError."""
    shipment = _make_shipment()
    job = Stage2Job(
        storage_key="1Z999AA10123456784",
        normalized_tn="1Z999AA10123456784",
        shipment=shipment,
        html_body="<html/>",
        message_id="test-msg-id",
        meta={"subject": "test", "from": "test@example.com"},
        entry_id="test_entry",
    )
    with pytest.raises(FrozenInstanceError):
        job.storage_key = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Phase 32 (D-03, WORK-03): test_queue_maxsize_matches_config and
# test_queue_maxsize_clamped are retired — both asserted on the retired
# per-entry _stage2_queue.maxsize, sized from the now-vestigial
# CONF_QUEUE_MAXLEN option (const.py). async_setup_stage2_extractor no longer
# constructs any queue; the shared hub queue is sized by the fixed
# HUB_STAGE2_QUEUE_MAXLEN constant instead (test_hub.py coverage).
# ---------------------------------------------------------------------------


async def test_setup_stage2_extractor_survives_corrupt_custom_fields(
    hass, mock_stage2_config_entry, caplog
):
    """IN-05 (coordinators): corrupt custom_fields options must not abort
    async_setup_stage2_extractor (entry setup) — malformed entries are skipped
    with a WARNING, valid entries survive.

    Phase 32 cutover: the CONF_QUEUE_MAXLEN corrupt-value/clamp scenario this
    test used to also cover is retired along with the per-entry queue
    construction it guarded (D-03) — only the extractor-build half's IN-05
    resilience survives to test here.
    """
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        hass.config_entries.async_update_entry(
            mock_stage2_config_entry,
            options={
                CONF_OLLAMA_URL: "http://localhost:11434",
                "custom_fields": [
                    {"description": "missing name key"},
                    "not-a-dict",
                    {"name": "valid_field", "description": "ok"},
                ],
            },
        )
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        with caplog.at_level(logging.WARNING):
            await coord.async_setup_stage2_extractor()

        # Extractor constructed; the valid custom field survived, junk was skipped.
        assert coord._extractor is not None
        field_names = [name for name, _desc in coord._extractor._fields]
        assert "valid_field" in field_names
        assert "malformed custom field entry" in caplog.text


# ---------------------------------------------------------------------------
# Phase 32 (D-04): test_stop_stage2_clears_state is retired — async_stop_stage2
# no longer exists, and the per-entry _stage2_queue/_stage2_enqueued_keys it
# used to clear are gone. The tail behavior it also proved ("_enqueue_stage2
# still works via the hub, independent of any per-entry lifecycle") no longer
# needs a "stop" precondition to demonstrate — it is simply how _enqueue_stage2
# always works now (covered by test_in_flight_dedup_prevents_double_enqueue
# and test_drop_newest_backpressure below).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 5: in-flight dedup prevents double-enqueue (QUE-06)
# ---------------------------------------------------------------------------


async def test_in_flight_dedup_prevents_double_enqueue(hass, mock_stage2_config_entry):
    """Calling _enqueue_stage2 twice with the same normalized_tn enqueues exactly one item.

    Phase 32 cutover: _enqueue_stage2 now delegates dedup entirely to the shared hub's
    global in-flight set (hub.enqueue) — assert on the hub's per-account in-flight count
    instead of the retired per-entry _stage2_queue/_stage2_enqueued_keys attributes.
    The hub's own dedup-gate unit coverage lives in test_hub.py
    (test_enqueue_duplicate_tn_returns_skipped_dup); this test locks the coordinator-side
    call contract (_enqueue_stage2 return value on dup).
    """
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_setup_stage2_extractor()

        shipment = _make_shipment()
        meta = {"subject": "Shipped", "from": "noreply@shopify.com", "date": "", "snippet": ""}
        normalized_tn = "1Z1"

        first = coord._enqueue_stage2(
            normalized_tn,
            storage_key=normalized_tn,
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:1",
            meta=meta,
        )
        # Second call with the same normalized_tn — must be silently skipped (SKIPPED_DUP).
        second = coord._enqueue_stage2(
            normalized_tn,
            storage_key=normalized_tn,
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:1",
            meta=meta,
        )

        assert first is True
        assert second is False
        assert coord._hub.inflight_count(coord.config_entry.entry_id) == 1


# ---------------------------------------------------------------------------
# Test 6: drop-newest backpressure on per-account cap exhaustion (WORK-03)
# ---------------------------------------------------------------------------


async def test_drop_newest_backpressure(hass, mock_stage2_config_entry, caplog):
    """On per-account in-flight cap exhaustion: event emitted, dropped TN NOT in dedup.

    Phase 32 cutover: backpressure is now enforced by the shared hub (global bound
    HUB_STAGE2_QUEUE_MAXLEN=64 + per-account cap STAGE2_PER_ACCOUNT_INFLIGHT_CAP=8),
    not the retired per-entry CONF_QUEUE_MAXLEN-sized queue. The hub's own gate-order
    and cap-boundary unit coverage lives in test_hub.py; this test locks the
    coordinator-side drop-emission contract (_emit_scan_event + diagnostics counter
    on DROPPED_BACKPRESSURE).
    """
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_setup_stage2_extractor()

        shipment = _make_shipment()
        meta = {"subject": "Shipped", "from": "noreply@shopify.com", "date": "", "snippet": ""}

        # Fill the per-account in-flight cap (8) with distinct tracking numbers.
        for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
            filled = coord._enqueue_stage2(
                f"1Z_FILL_{i}",
                storage_key=f"1Z_FILL_{i}",
                shipment=shipment,
                html_body="<html/>",
                message_id=f"msg:fill:{i}",
                meta=meta,
            )
            assert filled is True
        assert coord._hub.inflight_count(coord.config_entry.entry_id) == (
            STAGE2_PER_ACCOUNT_INFLIGHT_CAP
        )

        # One more (different normalized_tn) triggers DROPPED_BACKPRESSURE (cap full).
        dropped_tn = "1Z_DROP"
        dropped = coord._enqueue_stage2(
            dropped_tn,
            storage_key=dropped_tn,
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:drop",
            meta=meta,
        )

        # (a) Return value is False — no job was created for this call.
        assert dropped is False
        # (b) Last scan event must be stage2_dropped_backpressure.
        assert coord.diagnostics.scan_events[-1]["outcome"] == "stage2_dropped_backpressure"
        # (c) Diagnostics counter bumped exactly once (R3: no silent drop).
        assert coord.diagnostics.stage2_dropped_backpressure_total == 1
        # (d) Dropped TN must NOT be in the shared hub's dedup set.
        assert not coord._hub.is_submitted(dropped_tn)
        # (e) Dropped TN must NOT be in the hub's global in-flight dedup set.
        assert dropped_tn not in coord._hub._stage2_enqueued_keys


# ---------------------------------------------------------------------------
# Test 7: Stage-2 branch bypasses POST (QUE-01 R4, Phase 18 D-03)
# ---------------------------------------------------------------------------


async def test_stage2_branch_bypasses_post(hass, mock_stage2_config_entry):
    """R4: With stage2_enabled=True, _async_update_data enqueues and makes ZERO parcel POSTs.

    Validates QUE-01 acceptance criterion R4: the Stage-2 branch routes emails to
    _enqueue_stage2 and completely bypasses the inline parcel POST section.

    Phase 32 cutover: the enqueued job now lands on the shared hub's queue (not the
    retired per-entry _stage2_queue) — the autouse test hub (conftest.py) never calls
    hub.async_setup(), so no worker drains it and the item is safe to inspect directly.
    """
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        # stage2_enabled is derived from entry.options in __init__.py, but here
        # we are testing the coordinator directly so set it explicitly.
        coord._diagnostics.stage2_enabled = True
        await coord.async_setup_stage2_extractor()

        await coord._async_update_data()

        # (a) No parcel POST must be made.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()
        # (b) The shared hub's queue must contain exactly 1 item.
        assert coord._hub._queue.qsize() == 1
        # (c) The item is a Stage2Job with the expected tracking number.
        job: Stage2Job = coord._hub._queue.get_nowait()
        assert job.shipment.tracking_number == "1Z999AA10123456784"
        # (d) Normalized TN is in the hub's global in-flight dedup set.
        assert "1Z999AA10123456784" in coord._hub._stage2_enqueued_keys


# ---------------------------------------------------------------------------
# Test 8: Poll loop is Ollama-free with a full queue (QUE-07)
# ---------------------------------------------------------------------------


async def test_poll_loop_ollama_free_with_full_queue(hass, mock_stage2_config_entry, caplog):
    """QUE-07: A full hub queue/cap triggers drop-newest and _async_update_data returns
    synchronously.

    Validates: hub.enqueue's put_nowait is used (not await put), no Ollama imports exist
    in coordinator subclass files, and the drop-newest backpressure event is emitted
    rather than hanging.

    Phase 32 cutover: backpressure is now enforced by the shared hub (global bound
    HUB_STAGE2_QUEUE_MAXLEN=64 + per-account cap STAGE2_PER_ACCOUNT_INFLIGHT_CAP=8),
    not the retired per-entry CONF_QUEUE_MAXLEN-sized queue — pre-fill the account's
    hub in-flight cap instead of the (now inert) per-entry queue.
    """
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg1"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg1"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        coord._diagnostics.stage2_enabled = True
        await coord.async_setup_stage2_extractor()

        # The autouse test hub (conftest.py) never calls hub.async_setup(), so there is
        # no live worker to drain the hub queue — Phase 32 retired the per-entry
        # worker entirely, so there is no per-entry sentinel left to assert here.

        # Pre-fill the account's hub in-flight cap with filler jobs (distinct TNs to
        # avoid dedup skip) so the incoming poll's enqueue hits DROPPED_BACKPRESSURE.
        filler_shipment = _make_shipment("filler_msg")
        for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
            coord._hub.enqueue(
                Stage2Job(
                    storage_key=f"filler-{i}",
                    normalized_tn=f"filler-{i}",
                    shipment=filler_shipment,
                    html_body="",
                    message_id=f"filler-msg-id-{i}",
                    meta={"subject": "filler", "from": "filler@example.com"},
                    entry_id=coord.config_entry.entry_id,
                )
            )
        assert coord._hub.inflight_count(coord.config_entry.entry_id) == (
            STAGE2_PER_ACCOUNT_INFLIGHT_CAP
        )

        # Run the poll loop — _async_update_data must return synchronously (no hang).
        with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
            await coord._async_update_data()

        # (a) No parcel POST calls (the Stage-2 branch intercepted the email).
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()

        # (b) The scan_events log must contain a stage2_dropped_backpressure outcome
        #     (proves put_nowait was used — await put would hang, not raise QueueFull).
        outcomes = [ev["outcome"] for ev in coord.diagnostics.scan_events]
        assert "stage2_dropped_backpressure" in outcomes, (
            f"Expected 'stage2_dropped_backpressure' in scan event outcomes, got: {outcomes}"
        )

        # (c) QUE-07: Verify no Ollama-related symbols are imported in the coordinator
        #     subclass files (Stage-2 worker is outside Phase 18 scope).
        base_dir = pathlib.Path(__file__).parent.parent / "custom_components" / "shop2parcel"
        for fname in ("gmail_coordinator.py", "imap_coordinator.py"):
            source = (base_dir / fname).read_text()
            assert "OllamaExtractor" not in source, (
                f"{fname} must not import OllamaExtractor (Phase 18 scope boundary)"
            )
            assert "OllamaClient" not in source, (
                f"{fname} must not import OllamaClient (Phase 18 scope boundary)"
            )


# ---------------------------------------------------------------------------
# Test 9: stage2_enabled=False entries do not construct queue (QUE-07 boundary)
# ---------------------------------------------------------------------------


async def test_stage2_disabled_does_not_construct_extractor(hass, mock_config_entry):
    """With stage2_enabled=False, async_setup_stage2_extractor is never called and
    the extractor sentinel stays None.

    Validates the CONSTRAINTS boundary: the extractor must NOT be built for
    non-Stage-2 entries. Also verifies that the legacy inline POST path still
    works for stage2_enabled=False entries.

    Phase 32 cutover: renamed from test_stage2_disabled_does_not_construct_queue
    — there is no per-entry queue to assert None on anymore (D-03/D-04); the
    surviving sentinel is the per-account extractor.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body shipped</html>",
        ),
        patch.object(
            Shop2ParcelCoordinator,
            "async_setup_stage2_extractor",
            new_callable=AsyncMock,
        ) as mock_setup_extractor,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # async_setup drives the __init__.py wiring (stage2_enabled=False since no ollama_url).
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True

        coord = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # (a) async_setup_stage2_extractor must NOT have been called.
        mock_setup_extractor.assert_not_called()
        # (b) _extractor sentinel must remain None (never built) on a
        # stage2_enabled=False coordinator.
        assert coord._extractor is None, (
            "_extractor must be None on a stage2_enabled=False coordinator"
        )

        # (c) The legacy inline POST path still works: exercise one poll with a matched email.
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(
            return_value=([{"id": "msg2"}], "q after:0")
        )
        mock_gmail_cls.return_value.async_get_message = AsyncMock(
            return_value={"internalDate": "1700000000000", "payload": {}}
        )
        mock_parser_cls.return_value.parse.return_value = _make_parse_result(_make_shipment("msg2"))
        await coord._async_update_data()
        mock_parcel_cls.return_value.async_add_delivery.assert_called_once()


# ---------------------------------------------------------------------------
# Test 10: Stage2Job extended with message_id + meta fields (D-06 / D-07)
# ---------------------------------------------------------------------------


def test_stage2job_has_message_id_and_meta_fields():
    """D-06: Stage2Job must accept message_id and meta as required keyword arguments."""
    shipment = _make_shipment()
    job = Stage2Job(
        storage_key="1Z999AA10123456784",
        normalized_tn="1Z999AA10123456784",
        shipment=shipment,
        html_body="<html/>",
        message_id="test-msg-id",
        meta={"subject": "test", "from": "test@example.com"},
        entry_id="test_entry",
    )
    assert job.message_id == "test-msg-id"
    assert job.meta == {"subject": "test", "from": "test@example.com"}
    assert job.storage_key == "1Z999AA10123456784"
    assert job.normalized_tn == "1Z999AA10123456784"
    assert job.html_body == "<html/>"


def test_stage2job_frozen_with_new_fields():
    """Stage2Job must remain frozen after adding message_id + meta (D-06)."""
    shipment = _make_shipment()
    job = Stage2Job(
        storage_key="1Z999AA10123456784",
        normalized_tn="1Z999AA10123456784",
        shipment=shipment,
        html_body="<html/>",
        message_id="test-msg-id",
        meta={"subject": "test", "from": "test@example.com"},
        entry_id="test_entry",
    )
    with pytest.raises(FrozenInstanceError):
        job.message_id = "other"  # type: ignore[misc]
