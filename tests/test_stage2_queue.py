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
            return_value="<html>body</html>",
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
    )
    with pytest.raises(FrozenInstanceError):
        job.storage_key = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 2: queue maxsize matches config value (QUE-01)
# ---------------------------------------------------------------------------


async def test_queue_maxsize_matches_config(hass, mock_stage2_config_entry):
    """After async_start_stage2, _stage2_queue.maxsize equals the configured queue_maxlen."""
    mock_stage2_config_entry.add_to_hass(hass)
    # Override to 64 to distinguish from the default.
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={CONF_OLLAMA_URL: "http://localhost:11434", CONF_QUEUE_MAXLEN: 64},
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()
        assert coord._stage2_queue.maxsize == 64
        assert coord._stage2_enqueued_keys == set()


# ---------------------------------------------------------------------------
# Test 3: maxsize clamped to [1, 256] for out-of-range values (QUE-01 constraint)
# ---------------------------------------------------------------------------


async def test_queue_maxsize_clamped(hass, mock_stage2_config_entry):
    """async_start_stage2 clamps queue_maxlen to [1, 256] regardless of options value."""
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        # Case 1: 0 → clamped to 1
        hass.config_entries.async_update_entry(
            mock_stage2_config_entry,
            options={CONF_OLLAMA_URL: "http://localhost:11434", CONF_QUEUE_MAXLEN: 0},
        )
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()
        assert coord._stage2_queue.maxsize == 1

        # Case 2: 9999 → clamped to 256
        hass.config_entries.async_update_entry(
            mock_stage2_config_entry,
            options={CONF_OLLAMA_URL: "http://localhost:11434", CONF_QUEUE_MAXLEN: 9999},
        )
        coord2 = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord2._async_load_store()
        await coord2.async_start_stage2()
        assert coord2._stage2_queue.maxsize == 256

        # Case 3: default (32) stays 32
        hass.config_entries.async_update_entry(
            mock_stage2_config_entry,
            options={
                CONF_OLLAMA_URL: "http://localhost:11434",
                CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            },
        )
        coord3 = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord3._async_load_store()
        await coord3.async_start_stage2()
        assert coord3._stage2_queue.maxsize == DEFAULT_QUEUE_MAXLEN


# ---------------------------------------------------------------------------
# Test 4: async_stop_stage2 clears all state (QUE-01 lifecycle)
# ---------------------------------------------------------------------------


async def test_stop_stage2_clears_state(hass, mock_stage2_config_entry):
    """After async_stop_stage2, queue is empty and _stage2_enqueued_keys is empty."""
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Manually put an item in the queue to confirm stop drains it.
        shipment = _make_shipment()
        job = Stage2Job(
            storage_key="1Z999AA10123456784",
            normalized_tn="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        coord._stage2_queue.put_nowait(job)
        coord._stage2_enqueued_keys.add("1Z999AA10123456784")
        assert not coord._stage2_queue.empty()
        assert len(coord._stage2_enqueued_keys) == 1

        await coord.async_stop_stage2()
        assert coord._stage2_queue.empty() is True
        assert len(coord._stage2_enqueued_keys) == 0


# ---------------------------------------------------------------------------
# Test 5: in-flight dedup prevents double-enqueue (QUE-06)
# ---------------------------------------------------------------------------


async def test_in_flight_dedup_prevents_double_enqueue(hass, mock_stage2_config_entry):
    """Calling _enqueue_stage2 twice with the same normalized_tn enqueues exactly one item."""
    mock_stage2_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        meta = {"subject": "Shipped", "from": "noreply@shopify.com", "date": "", "snippet": ""}
        normalized_tn = "1Z1"

        coord._enqueue_stage2(
            normalized_tn,
            storage_key=normalized_tn,
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:1",
            meta=meta,
        )
        # Second call with the same normalized_tn — must be silently skipped.
        coord._enqueue_stage2(
            normalized_tn,
            storage_key=normalized_tn,
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:1",
            meta=meta,
        )

        assert coord._stage2_queue.qsize() == 1
        assert coord._stage2_enqueued_keys == {"1Z1"}


# ---------------------------------------------------------------------------
# Test 6: drop-newest backpressure on QueueFull (QUE-03)
# ---------------------------------------------------------------------------


async def test_drop_newest_backpressure(hass, mock_stage2_config_entry, caplog):
    """On QueueFull: warning logged, backpressure event emitted, dropped TN NOT in dedup."""
    mock_stage2_config_entry.add_to_hass(hass)
    # Use maxsize=1 so the second enqueue triggers QueueFull.
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={CONF_OLLAMA_URL: "http://localhost:11434", CONF_QUEUE_MAXLEN: 1},
    )
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"),
        patch(
            "custom_components.shop2parcel.gmail_coordinator.extract_html_body",
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        meta = {"subject": "Shipped", "from": "noreply@shopify.com", "date": "", "snippet": ""}

        # First enqueue fills the queue (maxsize=1).
        coord._enqueue_stage2(
            "1Z_FILL",
            storage_key="1Z_FILL",
            shipment=shipment,
            html_body="<html/>",
            message_id="msg:fill",
            meta=meta,
        )
        assert coord._stage2_queue.qsize() == 1

        # Second enqueue with a DIFFERENT normalized_tn triggers QueueFull (not dedup skip).
        dropped_tn = "1Z_DROP"
        with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
            coord._enqueue_stage2(
                dropped_tn,
                storage_key=dropped_tn,
                shipment=shipment,
                html_body="<html/>",
                message_id="msg:drop",
                meta=meta,
            )

        # (a) Warning must have been logged.
        assert any("Stage-2 queue full" in rec.message for rec in caplog.records), (
            "Expected 'Stage-2 queue full' warning in caplog"
        )
        # (b) Last scan event must be stage2_dropped_backpressure.
        assert coord.diagnostics.scan_events[-1]["outcome"] == "stage2_dropped_backpressure"
        # (c) Dropped TN must NOT be in _submitted_tracking_numbers.
        assert dropped_tn not in coord._submitted_tracking_numbers
        # (d) Dropped TN must NOT be in _stage2_enqueued_keys.
        assert dropped_tn not in coord._stage2_enqueued_keys


# ---------------------------------------------------------------------------
# Test 7: Stage-2 branch bypasses POST (QUE-01 R4, Phase 18 D-03)
# ---------------------------------------------------------------------------


async def test_stage2_branch_bypasses_post(hass, mock_stage2_config_entry):
    """R4: With stage2_enabled=True, _async_update_data enqueues and makes ZERO parcel POSTs.

    Validates QUE-01 acceptance criterion R4: the Stage-2 branch routes emails to
    _enqueue_stage2 and completely bypasses the inline parcel POST section.
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
            return_value="<html>body</html>",
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
        await coord.async_start_stage2()

        await coord._async_update_data()

        # (a) No parcel POST must be made.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_called()
        # (b) Queue must contain exactly 1 item.
        assert coord._stage2_queue.qsize() == 1
        # (c) The item is a Stage2Job with the expected tracking number.
        job: Stage2Job = coord._stage2_queue.get_nowait()
        assert job.shipment.tracking_number == "1Z999AA10123456784"
        # (d) Normalized TN is in _stage2_enqueued_keys.
        assert "1Z999AA10123456784" in coord._stage2_enqueued_keys


# ---------------------------------------------------------------------------
# Test 8: Poll loop is Ollama-free with a full queue (QUE-07)
# ---------------------------------------------------------------------------


async def test_poll_loop_ollama_free_with_full_queue(hass, mock_stage2_config_entry, caplog):
    """QUE-07: Pre-filled queue triggers drop-newest and _async_update_data returns synchronously.

    Validates: put_nowait is used (not await put), no Ollama imports exist in coordinator
    subclass files, and the drop-newest backpressure event is emitted rather than hanging.
    """
    # Set maxsize=1 to trigger QueueFull on the first enqueue after pre-fill.
    mock_stage2_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={CONF_OLLAMA_URL: "http://localhost:11434", CONF_QUEUE_MAXLEN: 1},
    )
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
            return_value="<html>body</html>",
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
        await coord.async_start_stage2()

        # Pre-fill queue to maxsize=1 with a filler job (different TN to avoid dedup skip).
        filler_shipment = _make_shipment("filler_msg")
        coord._stage2_queue.put_nowait(
            Stage2Job(
                storage_key="filler",
                normalized_tn="filler",
                shipment=filler_shipment,
                html_body="",
                message_id="filler-msg-id",
                meta={"subject": "filler", "from": "filler@example.com"},
            )
        )
        assert coord._stage2_queue.full()

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


async def test_stage2_disabled_does_not_construct_queue(hass, mock_config_entry):
    """With stage2_enabled=False, async_start_stage2 is never called and _stage2_queue is absent.

    Validates the CONSTRAINTS boundary: queue must NOT be constructed for non-Stage-2 entries.
    Also verifies that the legacy inline POST path still works for stage2_enabled=False entries.
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
            return_value="<html>body</html>",
        ),
        patch.object(
            Shop2ParcelCoordinator,
            "async_start_stage2",
            new_callable=AsyncMock,
        ) as mock_start_stage2,
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

        # (a) async_start_stage2 must NOT have been called.
        mock_start_stage2.assert_not_called()
        # (b) _stage2_queue sentinel must remain None (never started) on a
        # stage2_enabled=False coordinator (CR-01 fix: __init__ sets None sentinel).
        assert coord._stage2_queue is None, (
            "_stage2_queue must be None on a stage2_enabled=False coordinator"
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
    )
    with pytest.raises(FrozenInstanceError):
        job.message_id = "other"  # type: ignore[misc]
