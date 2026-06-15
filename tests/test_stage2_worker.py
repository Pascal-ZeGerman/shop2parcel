"""Phase 19: Stage-2 worker lifecycle tests — QUE-02, QUE-04, QUE-05, MRG-01."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ShipmentData
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
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        # Wire extractor instance with a callable async_extract.
        mock_extractor_instance = mock_extractor_cls.return_value
        mock_extractor_instance.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(
            storage_key="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        # MRG-01: extractor must have been called with the job's html_body and shipment.
        mock_extractor_instance.async_extract.assert_awaited_once_with(job.html_body, job.shipment)


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
        job = Stage2Job(
            storage_key="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
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
        job = Stage2Job(
            storage_key="msg1::1Z999",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        coord._stage2_queue.put_nowait(job)

        await asyncio.sleep(0)
        await hass.async_block_till_done()

        assert mock_set_data.called
        post_arg = mock_set_data.call_args.args[0]
        # D-06: snapshot — argument MUST be a new dict, not the same object.
        assert post_arg is not pre
        # The snapshot must contain the job's storage_key mapped to a ShipmentData.
        # Phase 20: merge_llm_authoritative always creates a new ShipmentData via
        # dataclasses.replace, so we check content equality rather than identity.
        assert "msg1::1Z999" in post_arg
        stored = post_arg["msg1::1Z999"]
        assert stored.tracking_number == shipment.tracking_number
        assert stored.carrier_name == shipment.carrier_name
        assert stored.order_name == shipment.order_name


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
        job = Stage2Job(
            storage_key="1Z999",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
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
        job = Stage2Job(
            storage_key="1Z999",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
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
        job = Stage2Job(
            storage_key="1Z999",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        coord._stage2_queue.put_nowait(job)

        # Let worker receive the job and the CancelledError propagate.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Worker should have self-terminated due to CancelledError propagation.
        task = coord._stage2_worker_task
        assert task is not None
        # Give the task a moment to be done.
        assert task.done() or task.cancelled()

        # Key must be discarded so next poll can re-enqueue this tracking number.
        assert "1Z999" not in coord._stage2_enqueued_keys


# ---------------------------------------------------------------------------
# Task 2: MRG-02 / MRG-03 — merge wiring + stage2_conflict emission
# ---------------------------------------------------------------------------


async def test_merge_promotes_stage2_value_when_stage1_none(hass, mock_stage2_config_entry):
    """MRG-02: When stage1.tracking_number is None and stage2 provides a valid value,
    POST receives the stage2 value (promotion path)."""
    from custom_components.shop2parcel.extractors.types import Stage2Result

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

        # Stage-2 provides a tracking number; Stage-1 has None.
        stage2_result = Stage2Result(
            locked={"tracking_number": "STAGE2TN123456", "carrier_name": None, "order_name": None},
            custom={},
            passes_used=1,
            latency_ms=10.0,
        )
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=stage2_result)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Stage-1 tracking_number is None — stage2 should promote.
        shipment = ShipmentData(
            tracking_number=None,
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg1",
            email_date=1700000000,
        )
        job = Stage2Job(
            storage_key="STAGE2TN123456",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        await coord._async_process_stage2_job(job)

        # POST must receive the stage2 tracking number, not None.
        mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
        call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args.kwargs
        assert call_kwargs["tracking_number"] == "STAGE2TN123456"


async def test_merge_conflict_keeps_stage1_and_emits_event(hass, mock_stage2_config_entry):
    """MRG-03: When stage1 and stage2 have different tracking_numbers, POST uses stage1
    and exactly one stage2_conflict event is emitted with the conflict payload."""
    from custom_components.shop2parcel.extractors.types import Stage2Result

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

        # Conflict: stage1 has "ABC123", stage2 has "XYZ789".
        stage2_result = Stage2Result(
            locked={"tracking_number": "XYZ789", "carrier_name": None, "order_name": None},
            custom={},
            passes_used=1,
            latency_ms=10.0,
        )
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=stage2_result)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = ShipmentData(
            tracking_number="ABC123",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg1",
            email_date=1700000000,
        )
        job = Stage2Job(
            storage_key="ABC123",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # Stage-1 wins on conflict — POST receives "ABC123".
        call_kwargs = mock_parcel_cls.return_value.async_add_delivery.call_args.kwargs
        assert call_kwargs["tracking_number"] == "ABC123"

        # Exactly one stage2_conflict event.
        conflict_events = [
            e for e in coord._diagnostics.scan_events if e["outcome"] == "stage2_conflict"
        ]
        assert len(conflict_events) == 1
        extra_conflicts = conflict_events[0].get("conflicts", [])
        assert len(extra_conflicts) == 1
        assert extra_conflicts[0]["field"] == "tracking_number"
        assert extra_conflicts[0]["stage1"] == "ABC123"
        assert extra_conflicts[0]["stage2"] == "XYZ789"


async def test_two_field_conflict_emits_single_event(hass, mock_stage2_config_entry):
    """MRG-03: Two conflicting fields produce exactly ONE stage2_conflict event
    containing two entries in extra['conflicts']."""
    from custom_components.shop2parcel.extractors.types import Stage2Result

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

        # Two conflicts: tracking_number AND carrier_name.
        stage2_result = Stage2Result(
            locked={"tracking_number": "XYZ", "carrier_name": "FedEx", "order_name": None},
            custom={},
            passes_used=1,
            latency_ms=10.0,
        )
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=stage2_result)
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = ShipmentData(
            tracking_number="ABC",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg1",
            email_date=1700000000,
        )
        job = Stage2Job(
            storage_key="ABC",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # Exactly ONE event regardless of how many fields conflict (MRG-03).
        conflict_events = [
            e for e in coord._diagnostics.scan_events if e["outcome"] == "stage2_conflict"
        ]
        assert len(conflict_events) == 1
        extra_conflicts = conflict_events[0].get("conflicts", [])
        # Two field conflicts in ONE event.
        assert len(extra_conflicts) == 2


# ---------------------------------------------------------------------------
# Task 3: FAIL-03 — Ollama errors skip POST and dedup write
# ---------------------------------------------------------------------------


async def test_ollama_transient_no_post_no_dedup(hass, mock_stage2_config_entry):
    """FAIL-03: OllamaTransientError → POST not called; tracking number absent from dedup store."""
    from custom_components.shop2parcel.api.exceptions import OllamaTransientError

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

        # Extractor raises OllamaTransientError (network/timeout/5xx).
        mock_extractor_cls.return_value.async_extract = AsyncMock(
            side_effect=OllamaTransientError("timeout")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(
            storage_key="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # FAIL-03: POST must NOT be called.
        assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0
        # FAIL-03: Tracking number must NOT be written to dedup store.
        assert job.storage_key not in coord._submitted_tracking_numbers


async def test_ollama_schema_no_post_no_dedup(hass, mock_stage2_config_entry):
    """FAIL-03: OllamaSchemaError → POST not called; tracking number absent from dedup store."""
    from custom_components.shop2parcel.api.exceptions import OllamaSchemaError

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

        # Extractor raises OllamaSchemaError (auth/404/malformed).
        mock_extractor_cls.return_value.async_extract = AsyncMock(
            side_effect=OllamaSchemaError("malformed")
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        shipment = _make_shipment()
        job = Stage2Job(
            storage_key="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # FAIL-03: POST must NOT be called.
        assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0
        # FAIL-03: Tracking number must NOT be written to dedup store.
        assert job.storage_key not in coord._submitted_tracking_numbers


# ---------------------------------------------------------------------------
# Phase 20 Plan 03 Task 1: MRG-05 scaffolding tests (RED gate)
# ---------------------------------------------------------------------------


def test_max_stage2_posts_per_poll_constant_exists():
    """MRG-05 D-08: MAX_STAGE2_POSTS_PER_POLL must equal 5 in const.py."""
    from custom_components.shop2parcel.const import MAX_STAGE2_POSTS_PER_POLL

    assert MAX_STAGE2_POSTS_PER_POLL == 5


def test_stage2_cap_notification_id_prefix_exists():
    """MRG-05 D-08: STAGE2_CAP_NOTIFICATION_ID_PREFIX must be 'shop2parcel_stage2_cap'."""
    from custom_components.shop2parcel.const import STAGE2_CAP_NOTIFICATION_ID_PREFIX

    assert STAGE2_CAP_NOTIFICATION_ID_PREFIX == "shop2parcel_stage2_cap"


def test_stage2_cap_notification_id_helper():
    """MRG-05 D-08: stage2_cap_notification_id returns expected string for a given entry_id."""
    from custom_components.shop2parcel.const import stage2_cap_notification_id

    result = stage2_cap_notification_id("abc123")
    assert result == "shop2parcel_stage2_cap_abc123"


async def test_coordinator_has_stage2_poll_counter_attrs(hass, mock_stage2_config_entry):
    """MRG-05 D-11: Coordinator __init__ must set _stage2_posts_this_poll and
    _stage2_cap_notified_this_poll to 0 / False respectively."""
    mock_stage2_config_entry.add_to_hass(hass)
    with (
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
    ):
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        assert coord._stage2_posts_this_poll == 0
        assert coord._stage2_cap_notified_this_poll is False


async def test_coordinator_has_reset_stage2_poll_counters_method(hass, mock_stage2_config_entry):
    """MRG-05 D-11: _reset_stage2_poll_counters must exist and reset both attrs."""
    mock_stage2_config_entry.add_to_hass(hass)
    with (
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
    ):
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Manually set to non-defaults to verify reset works.
        coord._stage2_posts_this_poll = 3
        coord._stage2_cap_notified_this_poll = True
        coord._reset_stage2_poll_counters()
        assert coord._stage2_posts_this_poll == 0
        assert coord._stage2_cap_notified_this_poll is False


# ---------------------------------------------------------------------------
# Phase 20 Plan 03 Task 2: MRG-05 cap gate + notification + increment (RED gate)
# ---------------------------------------------------------------------------


async def test_cap_skips_after_max_posts(hass, mock_stage2_config_entry):
    """MRG-05: When MAX_STAGE2_POSTS_PER_POLL reached, subsequent jobs are skipped.

    With cap patched to 2, driving 3 jobs must result in exactly 2 POSTs.
    The 3rd job's storage_key must not appear in _submitted_tracking_numbers
    (cap-skipped items are NOT deduplicated — retryable next poll).
    """
    from custom_components.shop2parcel.const import stage2_cap_notification_id

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
        patch("custom_components.shop2parcel.coordinator.MAX_STAGE2_POSTS_PER_POLL", 2),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Drive 3 jobs with distinct storage_keys.
        for i in range(1, 4):
            shipment = _make_shipment(message_id=f"msg{i}")
            job = Stage2Job(
                storage_key=f"TN{i:06d}",
                shipment=shipment,
                html_body="<html/>",
                message_id=f"msg-id-{i}",
                meta={"subject": f"Shipped {i}", "from": "noreply@shopify.com"},
            )
            await coord._async_process_stage2_job(job)

        # Exactly 2 POSTs (cap = 2).
        assert mock_parcel_cls.return_value.async_add_delivery.call_count == 2
        # 3rd job NOT in dedup store — retryable next poll.
        assert "TN000003" not in coord._submitted_tracking_numbers
        # 3rd job NOT in enqueued keys (was discarded by cap gate).
        assert "TN000003" not in coord._stage2_enqueued_keys


async def test_cap_notification_fires_once(hass, mock_stage2_config_entry):
    """MRG-05 D-10: Cap notification fires exactly once per poll regardless of cap-hit count.

    With cap patched to 1, driving 3 jobs:
      - job 1: POST succeeds (counter = 1, cap not yet hit)
      - job 2: cap hit — notification fires (call_count = 1)
      - job 3: cap hit — notification does NOT fire again (call_count still 1)
    """
    from custom_components.shop2parcel.const import stage2_cap_notification_id

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
        patch("custom_components.shop2parcel.coordinator.MAX_STAGE2_POSTS_PER_POLL", 1),
        patch(
            "custom_components.shop2parcel.coordinator.persistent_notification"
        ) as mock_pn,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_extractor_cls.return_value.async_extract = AsyncMock(return_value=MagicMock())
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        mock_pn.async_create = MagicMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        for i in range(1, 4):
            shipment = _make_shipment(message_id=f"msg{i}")
            job = Stage2Job(
                storage_key=f"TN{i:06d}",
                shipment=shipment,
                html_body="<html/>",
                message_id=f"msg-id-{i}",
                meta={"subject": f"Shipped {i}", "from": "noreply@shopify.com"},
            )
            await coord._async_process_stage2_job(job)

        # Notification fired exactly once (first cap-hit only).
        assert mock_pn.async_create.call_count == 1
        # Notification ID matches stage2_cap_notification_id for this entry.
        call_kwargs = mock_pn.async_create.call_args.kwargs
        assert call_kwargs["notification_id"] == stage2_cap_notification_id(
            mock_stage2_config_entry.entry_id
        )


async def test_reset_clears_counters_for_next_poll(hass, mock_stage2_config_entry):
    """MRG-05: After driving 2 successful POSTs, reset clears both counters to defaults."""
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

        # Drive 2 successful POSTs.
        for i in range(1, 3):
            shipment = _make_shipment(message_id=f"msg{i}")
            job = Stage2Job(
                storage_key=f"TN{i:06d}",
                shipment=shipment,
                html_body="<html/>",
                message_id=f"msg-id-{i}",
                meta={"subject": f"Shipped {i}", "from": "noreply@shopify.com"},
            )
            await coord._async_process_stage2_job(job)

        # Counter should be 2 after 2 successful POSTs.
        assert coord._stage2_posts_this_poll == 2
        # Now reset (simulates start of next poll).
        coord._reset_stage2_poll_counters()
        assert coord._stage2_posts_this_poll == 0
        assert coord._stage2_cap_notified_this_poll is False


# ---------------------------------------------------------------------------
# Phase 20 Plan 03 Task 3: dismissal + temperature:0 verification (RED gate)
# ---------------------------------------------------------------------------


def test_ollama_client_uses_temperature_zero():
    """SPEC §AC-13: OllamaClient must POST temperature:0 in options dict.

    Static source-code verification — no live Ollama call required. This is a
    boundary check that confirms the option has not been accidentally removed or
    changed in a future refactor (SPEC §Boundaries: verification only, no code change).
    """
    # SPEC §Acceptance Criteria item 13 — static source verification of temperature:0
    import pathlib

    source = pathlib.Path(
        "custom_components/shop2parcel/api/ollama_client.py"
    ).read_text()
    assert '"temperature": 0' in source


async def test_async_remove_entry_dismisses_cap_notification(hass, mock_stage2_config_entry):
    """async_remove_entry must dismiss BOTH debug-mode AND Stage-2 cap notifications."""
    from custom_components.shop2parcel import async_remove_entry
    from custom_components.shop2parcel.const import (
        debug_mode_notification_id,
        stage2_cap_notification_id,
    )

    mock_stage2_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.shop2parcel.persistent_notification"
    ) as mock_pn:
        mock_pn.async_dismiss = MagicMock()
        await async_remove_entry(hass, mock_stage2_config_entry)

    # Two dismiss calls — one for each notification type.
    assert mock_pn.async_dismiss.call_count == 2
    notification_ids = {
        call.kwargs["notification_id"]
        for call in mock_pn.async_dismiss.call_args_list
    }
    assert debug_mode_notification_id(mock_stage2_config_entry.entry_id) in notification_ids
    assert stage2_cap_notification_id(mock_stage2_config_entry.entry_id) in notification_ids
