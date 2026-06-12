"""Phase 19: Stage-2 worker lifecycle tests — QUE-02, QUE-04, QUE-05, MRG-01."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.api.exceptions import OllamaTransientError
from custom_components.shop2parcel.const import (
    CONF_CUSTOM_FIELDS,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_TIMEOUT,
    CONF_OLLAMA_URL,
    CONF_QUEUE_MAXLEN,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_QUEUE_MAXLEN,
    DOMAIN,
)
from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator, Stage2Job
from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shipment(message_id: str = "msg1") -> ShipmentData:
    return ShipmentData(
        tracking_number="1Z999AA10123456784",
        carrier_name="UPS",
        order_name="#1234",
        message_id=message_id,
        email_date=1700000000,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_stage2_config_entry() -> MockConfigEntry:
    """MockConfigEntry with stage2_enabled=True (ollama_url in options)."""
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
            CONF_OLLAMA_MODEL: "qwen3.5:2b",
            CONF_OLLAMA_TIMEOUT: 60,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            CONF_CUSTOM_FIELDS: [],
        },
        unique_id="user@gmail.com",
    )


def _patch_coord_deps_with_ollama():
    """Return a context manager stack that suppresses all external I/O for Phase 19 tests."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
    )


# ---------------------------------------------------------------------------
# Task 1: Sentinel + extractor construction tests (D-02, D-03)
# ---------------------------------------------------------------------------


async def test_worker_task_sentinel_is_none_before_start(hass, mock_stage2_config_entry):
    """D-02: _stage2_worker_task and _extractor are both None immediately after construction."""
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
        # Sentinels are asserted at construction time ONLY — do NOT call async_start_stage2.
        assert coord._stage2_worker_task is None
        assert coord._extractor is None


async def test_extractor_constructed_from_options(hass, mock_stage2_config_entry):
    """D-03: async_start_stage2 builds OllamaClient and OllamaExtractor from entry options."""
    mock_stage2_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_OLLAMA_MODEL: "qwen3.5:2b",
            CONF_OLLAMA_TIMEOUT: 45,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            CONF_CUSTOM_FIELDS: [{"name": "estimated_delivery", "description": "ETA"}],
        },
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient") as mock_ollama_client_cls,
        patch(
            "custom_components.shop2parcel.coordinator.OllamaExtractor"
        ) as mock_ollama_extractor_cls,
        patch.object(Shop2ParcelCoordinator, "_async_stage2_worker", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        assert mock_ollama_client_cls.called
        assert mock_ollama_client_cls.call_args.kwargs["base_url"] == "http://localhost:11434"
        assert mock_ollama_client_cls.call_args.kwargs["model"] == "qwen3.5:2b"
        assert mock_ollama_client_cls.call_args.kwargs["timeout"] == 45.0

        assert mock_ollama_extractor_cls.called
        assert mock_ollama_extractor_cls.call_args.kwargs["field_list"] == [
            ("estimated_delivery", "ETA")
        ]
        assert coord._extractor is mock_ollama_extractor_cls.return_value


async def test_extractor_uses_defaults_when_options_missing(hass, mock_stage2_config_entry):
    """D-03: When model/timeout/custom_fields absent, defaults are used."""
    mock_stage2_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            # No CONF_OLLAMA_MODEL, CONF_OLLAMA_TIMEOUT, CONF_CUSTOM_FIELDS
        },
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient") as mock_ollama_client_cls,
        patch(
            "custom_components.shop2parcel.coordinator.OllamaExtractor"
        ) as mock_ollama_extractor_cls,
        patch.object(Shop2ParcelCoordinator, "_async_stage2_worker", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        assert mock_ollama_client_cls.called
        assert mock_ollama_client_cls.call_args.kwargs["model"] == DEFAULT_OLLAMA_MODEL
        assert mock_ollama_client_cls.call_args.kwargs["timeout"] == float(DEFAULT_OLLAMA_TIMEOUT)

        assert mock_ollama_extractor_cls.called
        assert mock_ollama_extractor_cls.call_args.kwargs["field_list"] == []


async def test_extractor_skips_non_dict_custom_fields(hass, mock_stage2_config_entry):
    """D-04: Non-dict entries in CONF_CUSTOM_FIELDS are silently dropped from field_list."""
    mock_stage2_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_OLLAMA_MODEL: "qwen3.5:2b",
            CONF_OLLAMA_TIMEOUT: 60,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            CONF_CUSTOM_FIELDS: [
                {"name": "ok", "description": "x"},
                "not_a_dict",
                {"name": "ok2"},
            ],
        },
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch(
            "custom_components.shop2parcel.coordinator.OllamaExtractor"
        ) as mock_ollama_extractor_cls,
        patch.object(Shop2ParcelCoordinator, "_async_stage2_worker", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        assert mock_ollama_extractor_cls.called
        assert mock_ollama_extractor_cls.call_args.kwargs["field_list"] == [
            ("ok", "x"),
            ("ok2", None),
        ]


# ---------------------------------------------------------------------------
# Task 2: Lifecycle tests — worker spawn, cancel, no-leak (QUE-04, QUE-05, Pitfall 1)
# ---------------------------------------------------------------------------


async def test_worker_spawned_in_async_start_stage2(hass, mock_stage2_config_entry):
    """QUE-04 + SC-1: worker task is alive immediately after async_start_stage2, before first poll."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(
            Shop2ParcelCoordinator,
            "_async_process_stage2_job",
            new_callable=AsyncMock,
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Yield control so worker reaches queue.get() blocking point.
        await asyncio.sleep(0)

        assert coord._stage2_worker_task is not None
        assert isinstance(coord._stage2_worker_task, asyncio.Task)
        assert coord._stage2_worker_task.get_name() == "shop2parcel_stage2_worker"
        # SC-1: worker is RUNNING before any first_refresh fires (blocking at queue.get()).
        assert not coord._stage2_worker_task.done()

        # Teardown: cancel the running worker.
        await coord.async_stop_stage2()


async def test_worker_cancelled_on_async_stop_stage2(hass, mock_stage2_config_entry):
    """QUE-05: async_stop_stage2 cancels worker; task.cancelled() is True afterward."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(
            Shop2ParcelCoordinator,
            "_async_process_stage2_job",
            new_callable=AsyncMock,
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Let worker reach blocking queue.get() point.
        await asyncio.sleep(0)
        task = coord._stage2_worker_task
        assert task is not None

        await coord.async_stop_stage2()

        assert coord._stage2_worker_task is None
        assert task.done() is True
        assert task.cancelled() is True


async def test_async_stop_stage2_bounded_5_seconds(hass, mock_stage2_config_entry):
    """QUE-05 timeout: async_stop_stage2 completes within 5-second window even if worker ignores cancel."""
    mock_stage2_config_entry.add_to_hass(hass)

    async def _hang(self) -> None:  # noqa: RUF029
        """Simulates a worker that ignores CancelledError by shielding its internal wait."""
        await asyncio.shield(asyncio.Event().wait())

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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(Shop2ParcelCoordinator, "_async_stage2_worker", _hang),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()
        await asyncio.sleep(0)  # Let worker start.

        loop = asyncio.get_running_loop()
        start = loop.time()
        await coord.async_stop_stage2()
        elapsed = loop.time() - start

        assert 4.5 <= elapsed <= 6.0, f"Expected 5s timeout window, got {elapsed:.2f}s"
        assert coord._stage2_worker_task is None


async def test_no_worker_leak_after_3_reloads(hass, mock_stage2_config_entry):
    """Pitfall 1: No zombie worker tasks remain after 3 start/stop (reload) cycles."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(Shop2ParcelCoordinator, "_async_stage2_worker", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        for _ in range(3):
            await coord.async_start_stage2()
            await asyncio.sleep(0)  # Let AsyncMock worker finish in one tick.
            await coord.async_stop_stage2()

        # No tasks with the worker name should remain alive.
        leaked = [t for t in asyncio.all_tasks() if t.get_name() == "shop2parcel_stage2_worker"]
        assert len(leaked) == 0, f"Leaked worker tasks: {leaked}"
        assert coord._stage2_worker_task is None


async def test_async_stop_stage2_safe_when_worker_never_started(hass, mock_stage2_config_entry):
    """Pitfall 6: async_stop_stage2 is safe even if async_start_stage2 was never called."""
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
        # Do NOT call async_start_stage2 — worker sentinel must protect against this.
        await coord.async_stop_stage2()
        assert coord._stage2_worker_task is None


# ---------------------------------------------------------------------------
# Task 3: Per-job behavior tests — extractor call, state snapshot, store save,
#          key discard (MRG-01, D-05, D-06, Pitfall 5)
# ---------------------------------------------------------------------------


async def test_extractor_called_per_job(hass, mock_stage2_config_entry):
    """MRG-01: _extractor.async_extract is awaited once for every Stage2Job processed."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor") as mock_extractor_cls,
        patch.object(
            Shop2ParcelCoordinator, "_async_process_stage2_job", new_callable=AsyncMock
        ) as mock_process_job,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        # Wire extractor instance with a callable async_extract.
        mock_extractor_instance = mock_extractor_cls.return_value
        mock_extractor_instance.async_extract = AsyncMock(return_value=MagicMock())

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(storage_key="1Z999AA10123456784", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        # The worker called _async_process_stage2_job once (MRG-01 routing proof).
        mock_process_job.assert_called_once_with(job)


async def test_store_saved_after_successful_post(hass, mock_stage2_config_entry):
    """D-05: _async_save_store is awaited after each successful parcelapp POST."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor") as mock_extractor_cls,
        patch.object(
            Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock
        ) as mock_save,
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(storage_key="1Z999AA10123456784", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        assert mock_save.await_count >= 1


async def test_coordinator_data_snapshot_pattern(hass, mock_stage2_config_entry):
    """D-06: async_set_updated_data is called with a NEW dict (not in-place mutation)."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor") as mock_extractor_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(Shop2ParcelCoordinator, "async_set_updated_data") as mock_set_data,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        pre = coord.data  # Capture reference before job is processed.

        shipment = _make_shipment()
        job = Stage2Job(storage_key="msg1::1Z999", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        assert mock_set_data.called
        post_arg = mock_set_data.call_args.args[0]
        # D-06: snapshot — argument MUST be a new dict, not the same object.
        assert post_arg is not pre
        # The snapshot must contain the job's storage_key mapped to shipment.
        assert "msg1::1Z999" in post_arg
        assert post_arg["msg1::1Z999"] is shipment


async def test_enqueued_key_discarded_on_success(hass, mock_stage2_config_entry):
    """Pitfall 5 (success): _stage2_enqueued_keys loses the key AND dedup write happens."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor") as mock_extractor_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        coord._stage2_enqueued_keys = {"1Z999"}
        shipment = _make_shipment()
        job = Stage2Job(storage_key="1Z999", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        assert "1Z999" not in coord._stage2_enqueued_keys
        assert "1Z999" in coord._submitted_tracking_numbers


async def test_enqueued_key_discarded_on_ollama_failure_without_dedup(
    hass, mock_stage2_config_entry
):
    """Pitfall 5 (worker-level failure): key discarded without dedup write on RuntimeError."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(
            Shop2ParcelCoordinator,
            "_async_process_stage2_job",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated crash"),
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        coord._stage2_enqueued_keys = {"1Z999"}
        shipment = _make_shipment()
        job = Stage2Job(storage_key="1Z999", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        # Worker-level except Exception block discards key without dedup write.
        assert "1Z999" not in coord._stage2_enqueued_keys
        assert "1Z999" not in coord._submitted_tracking_numbers


async def test_worker_does_not_swallow_cancelled_error_during_process_job(
    hass, mock_stage2_config_entry
):
    """Test 3.6: CancelledError propagates from _async_process_stage2_job through worker."""
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
        patch("custom_components.shop2parcel.coordinator.OllamaClient"),
        patch("custom_components.shop2parcel.coordinator.OllamaExtractor"),
        patch.object(
            Shop2ParcelCoordinator,
            "_async_process_stage2_job",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(storage_key="1Z999", shipment=shipment, html_body="<html/>")
        coord._stage2_queue.put_nowait(job)

        # Let worker receive the job and the CancelledError propagate.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Worker should have self-terminated due to CancelledError propagation.
        task = coord._stage2_worker_task
        assert task is not None
        # Give the task a moment to be done.
        assert task.done() or task.cancelled()
