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
    """QUE-05 timeout: async_stop_stage2 completes within the 5-second backstop window.

    With explicit task.cancel() before wait_for (WR-02 fix), an idle or shielded worker
    receives CancelledError and exits quickly. The 5-second wait_for is a backstop for
    truly stuck workers; the normal path completes well under 5 seconds.
    """
    mock_stage2_config_entry.add_to_hass(hass)

    _inner_task: asyncio.Task | None = None

    async def _hang(self) -> None:  # noqa: RUF029
        """Simulates a worker that ignores CancelledError by shielding its internal wait."""
        nonlocal _inner_task
        _inner_task = asyncio.get_running_loop().create_task(asyncio.Event().wait())
        try:
            await asyncio.shield(_inner_task)
        except asyncio.CancelledError:
            raise

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

        # With task.cancel() before wait_for, the worker receives CancelledError promptly.
        # The 5-second backstop remains in place for pathological cases but normal shutdown
        # completes well under 5 seconds (WR-02 fix: no longer waits the full timeout).
        assert elapsed <= 6.0, (
            f"async_stop_stage2 must complete within backstop window, got {elapsed:.2f}s"
        )
        assert coord._stage2_worker_task is None
        # Cancel the inner shielded task so Python 3.14 doesn't flag it as a lingering task.
        if _inner_task is not None and not _inner_task.done():
            _inner_task.cancel()
            try:
                await _inner_task
            except asyncio.CancelledError:
                pass


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
            normalized_tn="1Z999AA10123456784",
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
            normalized_tn="1Z999AA10123456784",
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
            normalized_tn="1Z999",
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
            normalized_tn="1Z999",
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
            normalized_tn="1Z999",
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
            storage_key="msg-17a3f4c8b",
            normalized_tn="1Z999",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        # Pre-seed _stage2_enqueued_keys as _enqueue_stage2 would have done.
        # Without this, the discard assertion is trivially true even when discard is a no-op.
        coord._stage2_enqueued_keys.add(job.normalized_tn)
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
    """I6 precondition: When stage1.tracking_number is None, _async_process_stage2_job
    raises AssertionError — coordinators only enqueue emails with a resolved Stage-1
    tracking number (I6 contract). Stage-2 promotion of None is a contract violation."""
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
            normalized_tn="STAGE2TN123456",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "test", "from": "test@example.com"},
        )
        with pytest.raises(AssertionError, match="stage1.tracking_number must be non-None"):
            await coord._async_process_stage2_job(job)

        # Per I6 contract, POST must NOT be called when stage1.tracking_number is None.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()


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
            normalized_tn="ABC123",
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
            normalized_tn="ABC",
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
            normalized_tn="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # FAIL-03: POST must NOT be called.
        assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0
        # FAIL-03: Tracking number must NOT be written to dedup store.
        assert job.normalized_tn not in coord._submitted_tracking_numbers


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
            normalized_tn="1Z999AA10123456784",
            shipment=shipment,
            html_body="<html/>",
            message_id="test-msg-id",
            meta={"subject": "Shipped", "from": "noreply@shopify.com"},
        )
        await coord._async_process_stage2_job(job)

        # FAIL-03: POST must NOT be called.
        assert mock_parcel_cls.return_value.async_add_delivery.call_count == 0
        # FAIL-03: Tracking number must NOT be written to dedup store.
        assert job.normalized_tn not in coord._submitted_tracking_numbers


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
        mock_extractor_cls.return_value.async_extract = AsyncMock(
            return_value=MagicMock(latency_ms=100.0, passes_used=1)
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Drive 3 jobs with distinct storage_keys.
        for i in range(1, 4):
            shipment = _make_shipment(message_id=f"msg{i}")
            job = Stage2Job(
                storage_key=f"TN{i:06d}",
                normalized_tn=f"TN{i:06d}",
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
        patch("custom_components.shop2parcel.coordinator.persistent_notification") as mock_pn,
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
                normalized_tn=f"TN{i:06d}",
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
        mock_extractor_cls.return_value.async_extract = AsyncMock(
            return_value=MagicMock(latency_ms=100.0, passes_used=1)
        )
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        # Drive 2 successful POSTs.
        for i in range(1, 3):
            shipment = _make_shipment(message_id=f"msg{i}")
            job = Stage2Job(
                storage_key=f"TN{i:06d}",
                normalized_tn=f"TN{i:06d}",
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

    source = pathlib.Path("custom_components/shop2parcel/api/ollama_client.py").read_text()
    assert '"temperature": 0' in source


async def test_async_remove_entry_dismisses_cap_notification(hass, mock_stage2_config_entry):
    """async_remove_entry must dismiss debug-mode, Stage-2 cap, AND Stage-2 failing notifications."""
    from custom_components.shop2parcel import async_remove_entry
    from custom_components.shop2parcel.const import (
        debug_mode_notification_id,
        stage2_cap_notification_id,
        stage2_failing_notification_id,
    )

    mock_stage2_config_entry.add_to_hass(hass)
    with patch("homeassistant.components.persistent_notification.async_dismiss") as mock_dismiss:
        await async_remove_entry(hass, mock_stage2_config_entry)

    # Three dismiss calls — one for each notification type (I2 fix adds stage2_failing).
    assert mock_dismiss.call_count == 3
    notification_ids = {call.kwargs["notification_id"] for call in mock_dismiss.call_args_list}
    assert debug_mode_notification_id(mock_stage2_config_entry.entry_id) in notification_ids
    assert stage2_cap_notification_id(mock_stage2_config_entry.entry_id) in notification_ids
    assert stage2_failing_notification_id(mock_stage2_config_entry.entry_id) in notification_ids


# ---------------------------------------------------------------------------
# Phase 21 Plan 02: Task 1 — Foundation layer (FAIL-04/05 state vars)
# ---------------------------------------------------------------------------


async def test_coordinator_init_sets_failure_counter_to_zero(hass, mock_stage2_config_entry):
    """Phase 21 FAIL-04/05: _stage2_consecutive_failures is 0 and _stage2_last_notify_ts is
    None immediately after coordinator construction (before any Stage-2 activity).
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
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        assert coord._stage2_consecutive_failures == 0
        assert coord._stage2_last_notify_ts is None


async def test_async_stop_stage2_resets_failure_counter_to_zero(hass, mock_stage2_config_entry):
    """Phase 21 SPEC Req #5: async_stop_stage2 resets _stage2_consecutive_failures to 0
    and _stage2_last_notify_ts to None, even if they were non-zero/non-None before the call.
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
        await coord.async_start_stage2()

        # Manually set non-default state to simulate a prior streak.
        coord._stage2_consecutive_failures = 5
        coord._stage2_last_notify_ts = 1700000000.0

        await coord.async_stop_stage2()

        assert coord._stage2_consecutive_failures == 0
        assert coord._stage2_last_notify_ts is None


async def test_async_stop_stage2_resets_even_when_worker_is_none(hass, mock_stage2_config_entry):
    """Phase 21 SPEC Req #5: async_stop_stage2 resets failure counter UNCONDITIONALLY.

    The Phase 18 CR-01 sentinel means async_stop_stage2 has an early-return path
    when _stage2_queue is None (worker never started). The reset must fire even on
    this path — this test guards against a naive 'append at end' placement that would
    be skipped by the early return.
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
            return_value="<html>body</html>",
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Do NOT call async_start_stage2 — _stage2_queue remains None (CR-01 sentinel path).
        assert coord._stage2_queue is None
        assert coord._stage2_worker_task is None

        # Set non-default failure state.
        coord._stage2_consecutive_failures = 3

        await coord.async_stop_stage2()

        # Reset must have fired despite the early-return path.
        assert coord._stage2_consecutive_failures == 0
        assert coord._stage2_last_notify_ts is None


# ---------------------------------------------------------------------------
# Phase 21 Plan 02: Task 2 — FAIL-01/02/04/05 helper methods + wiring
# ---------------------------------------------------------------------------

# Shared patch list for Task 2 tests — avoids duplication.
_COORD_PATCHES = [
    "custom_components.shop2parcel.gmail_coordinator.GmailClient",
    "custom_components.shop2parcel.gmail_coordinator.ParcelAppClient",
    "custom_components.shop2parcel.gmail_coordinator.EmailParser",
    "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow",
]


def _make_job(subject="Order Shipped", sender="noreply@shopify.com", normalized_tn="1Z999TN"):
    """Helper: build a Stage2Job for testing failure/success helpers."""
    return Stage2Job(
        storage_key=f"{normalized_tn}::job",
        normalized_tn=normalized_tn,
        shipment=_make_shipment(message_id="msg-fail-test"),
        html_body="<html/>",
        message_id="msg-fail-test",
        meta={"subject": subject, "from": sender},
    )


@pytest.fixture
def coord_for_fail_tests(hass, mock_stage2_config_entry):
    """Yield a GmailCoordinator instance ready for _record_stage2_failure/_success tests.

    Worker is NOT started — tests call helpers directly or use _async_process_stage2_job.
    """
    import contextlib
    import unittest.mock

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
        yield coord


# ---------------------------------------------------------------------------
# FAIL-01: _record_stage2_failure emits exactly one ERROR log with 4 fields
# ---------------------------------------------------------------------------


async def test_fail_01_ollama_transient_error_logs_error_with_4_fields(
    caplog, coord_for_fail_tests
):
    """FAIL-01: OllamaTransientError causes exactly one ERROR log with subject, sender,
    error class name, and error message (formerly swallowed at DEBUG level).
    """
    from custom_components.shop2parcel.api.exceptions import OllamaTransientError

    coord = coord_for_fail_tests
    job = _make_job(subject="Your order has shipped", sender="noreply@shopify.com")
    err = OllamaTransientError("connection refused")

    import logging

    with caplog.at_level(logging.ERROR):
        coord._record_stage2_failure(job, err)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    msg = error_records[0].getMessage()
    assert "OllamaTransientError" in msg
    assert "connection refused" in msg
    assert "Your order has shipped" in msg
    assert "noreply@shopify.com" in msg


async def test_fail_01_ollama_schema_error_logs_error_with_4_fields(caplog, coord_for_fail_tests):
    """FAIL-01: OllamaSchemaError causes exactly one ERROR log with the 4 required fields."""
    from custom_components.shop2parcel.api.exceptions import OllamaSchemaError

    coord = coord_for_fail_tests
    job = _make_job(subject="Shipment update", sender="carrier@example.com")
    err = OllamaSchemaError("bad json")

    import logging

    with caplog.at_level(logging.ERROR):
        coord._record_stage2_failure(job, err)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    msg = error_records[0].getMessage()
    assert "OllamaSchemaError" in msg
    assert "bad json" in msg
    assert "Shipment update" in msg
    assert "carrier@example.com" in msg


async def test_fail_01_worker_outer_generic_exception_logs_error_with_4_fields(
    hass, mock_stage2_config_entry, caplog
):
    """FAIL-01: Generic RuntimeError from the worker outer-except path also produces
    an ERROR log with the 4 required fields (worker-outer wiring, not just Ollama except).
    """
    import logging

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
            side_effect=RuntimeError("unexpected"),
        ),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        await coord.async_start_stage2()

        job = _make_job()
        coord._stage2_queue.put_nowait(job)

        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(0)
            await hass.async_block_till_done()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) >= 1
        combined = " ".join(r.getMessage() for r in error_records)
        assert "RuntimeError" in combined
        assert "unexpected" in combined


# ---------------------------------------------------------------------------
# FAIL-02: _record_stage2_failure appends stage2_failed event to scan_events
# ---------------------------------------------------------------------------


async def test_fail_02_emits_stage2_failed_event_on_ollama_transient_error(coord_for_fail_tests):
    """FAIL-02: OllamaTransientError causes a stage2_failed scan_event with error metadata."""
    from custom_components.shop2parcel.api.exceptions import OllamaTransientError

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaTransientError("timeout")

    coord._record_stage2_failure(job, err)

    assert len(coord.diagnostics.scan_events) == 1
    evt = coord.diagnostics.scan_events[-1]
    assert evt["outcome"] == "stage2_failed"
    assert evt["error_type"] == "OllamaTransientError"
    assert "timeout" in evt["error_msg"]
    assert evt["tracking_number"] == job.normalized_tn
    assert evt["message_id"] == job.message_id


async def test_fail_02_emits_stage2_failed_event_on_ollama_schema_error(coord_for_fail_tests):
    """FAIL-02: OllamaSchemaError causes a stage2_failed scan_event."""
    from custom_components.shop2parcel.api.exceptions import OllamaSchemaError

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaSchemaError("no opening brace")

    coord._record_stage2_failure(job, err)

    evt = coord.diagnostics.scan_events[-1]
    assert evt["outcome"] == "stage2_failed"
    assert evt["error_type"] == "OllamaSchemaError"
    assert "no opening brace" in evt["error_msg"]


async def test_fail_02_emits_stage2_failed_event_on_worker_outer_exception(coord_for_fail_tests):
    """FAIL-02: Generic Exception from worker outer-except also appends stage2_failed event."""
    coord = coord_for_fail_tests
    job = _make_job()
    err = RuntimeError("generic crash")

    coord._record_stage2_failure(job, err)

    evt = coord.diagnostics.scan_events[-1]
    assert evt["outcome"] == "stage2_failed"
    assert evt["error_type"] == "RuntimeError"
    assert "generic crash" in evt["error_msg"]


# ---------------------------------------------------------------------------
# FAIL-04: threshold notification + cooldown + re-fire
# ---------------------------------------------------------------------------


async def test_fail_04_notification_fires_at_threshold(hass, coord_for_fail_tests):
    """FAIL-04: After 3 consecutive Ollama failures, persistent_notification.async_create
    fires exactly once with the correct notification_id, title, and message body.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import (
        STAGE2_NOTIFY_THRESHOLD,
        stage2_failing_notification_id,
    )

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaTransientError("connection refused")

    with patch.object(persistent_notification, "async_create") as mock_create:
        for _ in range(STAGE2_NOTIFY_THRESHOLD):
            coord._record_stage2_failure(job, err)

        assert mock_create.call_count == 1
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["notification_id"] == stage2_failing_notification_id(
            coord.config_entry.entry_id
        )
        assert call_kwargs["title"] == "Shop2Parcel Stage-2 Failing"
        assert "failed 3 times in a row" in call_kwargs["message"]
        assert "Stage-1" in call_kwargs["message"]


async def test_fail_04_notification_does_not_refire_within_cooldown(hass, coord_for_fail_tests):
    """FAIL-04 cooldown: A 4th failure within STAGE2_NOTIFY_COOLDOWN_S does NOT re-fire."""
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import (
        STAGE2_NOTIFY_COOLDOWN_S,
        STAGE2_NOTIFY_THRESHOLD,
    )

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaTransientError("connection refused")

    BASE_TIME = 1700000000.0

    with patch.object(persistent_notification, "async_create") as mock_create:
        with patch("custom_components.shop2parcel.coordinator._time.time", return_value=BASE_TIME):
            for _ in range(STAGE2_NOTIFY_THRESHOLD):
                coord._record_stage2_failure(job, err)
            assert mock_create.call_count == 1

        # Still within cooldown window — should NOT re-fire.
        inside_cooldown = BASE_TIME + STAGE2_NOTIFY_COOLDOWN_S - 1
        with patch(
            "custom_components.shop2parcel.coordinator._time.time", return_value=inside_cooldown
        ):
            coord._record_stage2_failure(job, err)

        assert mock_create.call_count == 1  # no re-fire


async def test_fail_04_notification_refires_after_cooldown(hass, coord_for_fail_tests):
    """FAIL-04 re-fire: After STAGE2_NOTIFY_COOLDOWN_S elapses, the next failure DOES re-fire."""
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import (
        STAGE2_NOTIFY_COOLDOWN_S,
        STAGE2_NOTIFY_THRESHOLD,
    )

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaTransientError("connection refused")

    BASE_TIME = 1700000000.0

    with patch.object(persistent_notification, "async_create") as mock_create:
        with patch("custom_components.shop2parcel.coordinator._time.time", return_value=BASE_TIME):
            for _ in range(STAGE2_NOTIFY_THRESHOLD):
                coord._record_stage2_failure(job, err)
            assert mock_create.call_count == 1

        # Past cooldown — should re-fire.
        after_cooldown = BASE_TIME + STAGE2_NOTIFY_COOLDOWN_S + 1
        with patch(
            "custom_components.shop2parcel.coordinator._time.time", return_value=after_cooldown
        ):
            coord._record_stage2_failure(job, err)
            assert mock_create.call_count == 2
            # _stage2_last_notify_ts must be updated to the new fire time.
            assert coord._stage2_last_notify_ts == after_cooldown


async def test_fail_04_counter_does_not_reset_after_notification_fires(hass, coord_for_fail_tests):
    """FAIL-04 D-09: The failure counter is NOT reset when notification fires; cooldown
    alone gates re-fires. Counter keeps growing on subsequent failures.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import STAGE2_NOTIFY_THRESHOLD

    coord = coord_for_fail_tests
    job = _make_job()
    err = OllamaTransientError("connection refused")

    with patch.object(persistent_notification, "async_create"):
        for _ in range(STAGE2_NOTIFY_THRESHOLD):
            coord._record_stage2_failure(job, err)
        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD

        # Counter keeps growing; cooldown prevents re-fire.
        coord._record_stage2_failure(job, err)
        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD + 1


async def test_fail_04_parcelapp_transient_error_does_not_count_toward_threshold(
    hass, mock_stage2_config_entry
):
    """FAIL-04 D-05: ParcelAppTransientError does NOT increment _stage2_consecutive_failures.
    5 consecutive ParcelApp failures should leave the counter at 0 and notification unfired.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import ParcelAppTransientError

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_create") as mock_create,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppTransientError("5xx from parcelapp")
        )

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        # Wire extractor directly — do NOT start the background worker to avoid races.
        mock_extractor = MagicMock()
        mock_extractor.async_extract = AsyncMock(
            return_value=MagicMock(latency_ms=100.0, passes_used=1)
        )
        coord._extractor = mock_extractor

        for i in range(5):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        assert coord._stage2_consecutive_failures == 0
        assert mock_create.call_count == 0


async def test_fail_04_parcelapp_already_added_does_not_count_toward_threshold(
    hass, mock_stage2_config_entry
):
    """FAIL-04 D-05: ParcelAppAlreadyAddedError does NOT increment the failure counter."""
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import ParcelAppAlreadyAddedError

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_create") as mock_create,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()
        # Wire extractor directly — do NOT start the background worker to avoid races.
        mock_extractor = MagicMock()
        mock_extractor.async_extract = AsyncMock(
            return_value=MagicMock(latency_ms=100.0, passes_used=1)
        )
        coord._extractor = mock_extractor

        for i in range(5):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        assert coord._stage2_consecutive_failures == 0
        assert mock_create.call_count == 0


# ---------------------------------------------------------------------------
# FAIL-05: recovery — counter reset + notification dismiss on success
# ---------------------------------------------------------------------------


async def test_fail_05_first_success_after_streak_dismisses_notification(
    hass, mock_stage2_config_entry
):
    """FAIL-05: After a failure streak + notification fire, a successful POST resets the
    counter to 0 and calls persistent_notification.async_dismiss with the correct ID.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import (
        STAGE2_NOTIFY_THRESHOLD,
        stage2_failing_notification_id,
    )

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_create") as mock_create,
        patch.object(persistent_notification, "async_dismiss") as mock_dismiss,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # GmailCoordinator.__init__ calls async_dismiss(debug_mode_notification_id) when
        # CONF_DEBUG_MODE is False — capture baseline BEFORE the success test to isolate it.
        baseline_dismiss_count = mock_dismiss.call_count

        await coord._async_load_store()
        # Wire extractor directly — do NOT start the background worker to avoid races.
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[
                OllamaTransientError("err1"),
                OllamaTransientError("err2"),
                OllamaTransientError("err3"),
            ]
        )
        coord._extractor = fail_extractor

        # 3 failures — notification fires.
        for i in range(STAGE2_NOTIFY_THRESHOLD):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        assert mock_create.call_count == 1
        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD

        # Switch extractor to success mode + run success job.
        success_extractor = MagicMock()
        success_extractor.async_extract = AsyncMock(return_value=MagicMock())
        coord._extractor = success_extractor

        success_job = _make_job(normalized_tn="TNSUCCESS")
        await coord._async_process_stage2_job(success_job)

        assert coord._stage2_consecutive_failures == 0
        # Exactly ONE new dismiss (from _record_stage2_success) since the baseline.
        assert mock_dismiss.call_count == baseline_dismiss_count + 1
        assert mock_dismiss.call_args.kwargs["notification_id"] == stage2_failing_notification_id(
            coord.config_entry.entry_id
        )


async def test_fail_05_already_added_is_not_a_success(hass, mock_stage2_config_entry):
    """FAIL-05 D-06: ParcelAppAlreadyAddedError is a graceful rejection, not a success.
    Counter must NOT reset and async_dismiss must NOT be called.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import (
        OllamaTransientError,
        ParcelAppAlreadyAddedError,
    )
    from custom_components.shop2parcel.const import STAGE2_NOTIFY_THRESHOLD

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss") as mock_dismiss,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already added")
        )

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Capture baseline (GmailCoordinator.__init__ calls dismiss for debug-mode notification).
        baseline_dismiss_count = mock_dismiss.call_count

        await coord._async_load_store()
        # Wire extractors directly — do NOT start background worker.
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[OllamaTransientError("err")] * STAGE2_NOTIFY_THRESHOLD
        )
        coord._extractor = fail_extractor

        for i in range(STAGE2_NOTIFY_THRESHOLD):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        # Switch extractor to success mode; parcel raises AlreadyAdded — NOT a success.
        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(return_value=MagicMock())
        coord._extractor = ok_extractor

        already_added_job = _make_job(normalized_tn="TNALREADY")
        await coord._async_process_stage2_job(already_added_job)

        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD
        # No NEW dismiss calls beyond the baseline (AlreadyAdded is NOT a success).
        assert mock_dismiss.call_count == baseline_dismiss_count


async def test_fail_05_cap_skip_is_not_a_success(hass, mock_stage2_config_entry):
    """FAIL-05 D-06: A cap-skipped job (MAX_STAGE2_POSTS_PER_POLL reached) is NOT a success.
    Counter must NOT reset and async_dismiss must NOT be called.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import (
        MAX_STAGE2_POSTS_PER_POLL,
        STAGE2_NOTIFY_THRESHOLD,
    )

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss") as mock_dismiss,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Capture baseline (GmailCoordinator.__init__ calls dismiss for debug-mode notification).
        baseline_dismiss_count = mock_dismiss.call_count

        await coord._async_load_store()
        # Wire extractor — fail 3 times to build up streak.
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[OllamaTransientError("err")] * STAGE2_NOTIFY_THRESHOLD
        )
        coord._extractor = fail_extractor

        for i in range(STAGE2_NOTIFY_THRESHOLD):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        # Exhaust the cap so the next job hits the cap-skip path (no extractor call).
        coord._stage2_posts_this_poll = MAX_STAGE2_POSTS_PER_POLL
        cap_job = _make_job(normalized_tn="TNCAP")
        await coord._async_process_stage2_job(cap_job)

        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD
        # No NEW dismiss calls beyond the baseline (cap-skip is NOT a success).
        assert mock_dismiss.call_count == baseline_dismiss_count


async def test_fail_05_quota_exhausted_skip_is_not_a_success(hass, mock_stage2_config_entry):
    """FAIL-05 D-06: A quota-exhausted skip (quota guard before POST) is NOT a success.
    Counter must NOT reset and async_dismiss must NOT be called.
    """
    import time as stdlib_time

    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaTransientError
    from custom_components.shop2parcel.const import STAGE2_NOTIFY_THRESHOLD

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss") as mock_dismiss,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Capture baseline (GmailCoordinator.__init__ calls dismiss for debug-mode notification).
        baseline_dismiss_count = mock_dismiss.call_count

        await coord._async_load_store()
        # Wire extractor — fail 3 times to build up streak.
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[OllamaTransientError("err")] * STAGE2_NOTIFY_THRESHOLD
        )
        coord._extractor = fail_extractor

        for i in range(STAGE2_NOTIFY_THRESHOLD):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)

        # Simulate quota exhausted — next job extracts but POST is deferred (Phase 23
        # decoupled semantics). A quota-deferred POST is a parcelapp suppression, NOT an
        # Ollama failure — the failure streak must NOT increment, and dismiss must NOT fire
        # (no success happened because no actual POST was made).
        from custom_components.shop2parcel.extractors.types import Stage2Result

        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=5.0)
        )
        coord._extractor = ok_extractor
        coord._quota_exhausted_until = int(stdlib_time.time()) + 9999

        quota_job = _make_job(normalized_tn="TNQUOTA")
        await coord._async_process_stage2_job(quota_job)

        assert coord._stage2_consecutive_failures == STAGE2_NOTIFY_THRESHOLD
        # No NEW dismiss calls beyond the baseline (quota-deferred POST is NOT a success).
        assert mock_dismiss.call_count == baseline_dismiss_count


@pytest.mark.asyncio
async def test_quota_gate_increments_quota_skipped_total_and_skips_extractor(
    hass, mock_stage2_config_entry
):
    """AC-1 / DIAG-02 (inverted): With quota exhausted the extractor IS called and
    stage2_quota_skipped_total increments — but no parcelapp POST is attempted.

    Decoupled semantics (Phase 23): LLM extraction runs regardless of parcelapp quota.
    The counter now means "extracted, POST deferred" — not "skipped before extractor".
    The quota guard moved to AFTER extraction+merge so Ollama always runs per job.

    RED until plan 03 lands.
    """
    import time as stdlib_time

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        # Wire extractor with a spy that returns a realistic Stage2Result shape.
        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(
                locked={},
                custom={},
                passes_used=1,
                latency_ms=5.0,
            )
        )
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Exhaust quota before processing any job.
        coord._quota_exhausted_until = int(stdlib_time.time()) + 9999

        job = _make_job(normalized_tn="TNQUOTA2")
        await coord._async_process_stage2_job(job)

        # Counter must increment (semantics: "extracted, POST deferred").
        assert coord._diagnostics.stage2_quota_skipped_total == 1
        # Extractor MUST be called — decoupled path: extraction runs despite quota.
        spy_extractor.async_extract.assert_awaited_once()
        # Success counter must NOT increment — no POST happened.
        assert coord._diagnostics.stage2_succeeded_total == 0
        # No parcelapp POST must be attempted.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()


async def test_fail_05_dismiss_unconditional_even_when_no_prior_notification(
    hass, mock_stage2_config_entry
):
    """FAIL-05 D-04: async_dismiss is called unconditionally on success even with counter=0.
    HA's async_dismiss is a no-op on unknown notification IDs so this is safe.
    """
    from homeassistant.components import persistent_notification

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(persistent_notification, "async_dismiss") as mock_dismiss,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # GmailCoordinator.__init__ calls async_dismiss(debug_mode_notification_id) when
        # CONF_DEBUG_MODE is False — capture baseline BEFORE the success test.
        baseline_dismiss_count = mock_dismiss.call_count

        await coord._async_load_store()
        # Wire extractor directly — do NOT start background worker.
        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(return_value=MagicMock())
        coord._extractor = ok_extractor

        # No prior failure streak — counter is 0.
        assert coord._stage2_consecutive_failures == 0

        success_job = _make_job(normalized_tn="TNNOSTREAK")
        await coord._async_process_stage2_job(success_job)

        # Dismiss must still be called even with no prior notification — exactly 1 new call.
        assert mock_dismiss.call_count == baseline_dismiss_count + 1


# ---------------------------------------------------------------------------
# Phase 21 Plan 03 — DIAG-02: PollStats stage2_*_total counters and
# stage2_queue_depth property
# ---------------------------------------------------------------------------


def test_pollstats_default_stage2_counters_zero():
    """DIAG-02: All stage2_*_total fields default to 0 on PollStats construction."""
    from dataclasses import fields

    from custom_components.shop2parcel.coordinator import PollStats

    ps = PollStats()
    assert ps.stage2_enqueued_total == 0
    assert ps.stage2_succeeded_total == 0
    assert ps.stage2_failed_total == 0
    assert ps.stage2_dropped_backpressure_total == 0
    assert ps.stage2_schema_error_total == 0
    assert ps.stage2_conflict_total == 0
    assert ps.stage2_quota_skipped_total == 0


def test_pollstats_asdict_contains_all_stage2_counter_keys():
    """DIAG-02: dataclasses.asdict(PollStats()) includes all stage2 counter keys.

    Proves the diagnostics download auto-includes them (Assumption A2).
    """
    from dataclasses import asdict

    from custom_components.shop2parcel.coordinator import PollStats

    d = asdict(PollStats())
    expected_keys = {
        "stage2_enqueued_total",
        "stage2_succeeded_total",
        "stage2_failed_total",
        "stage2_dropped_backpressure_total",
        "stage2_schema_error_total",
        "stage2_conflict_total",
        "stage2_quota_skipped_total",
    }
    assert expected_keys <= set(d.keys()), f"missing keys: {expected_keys - set(d.keys())}"


async def test_stage2_queue_depth_returns_zero_when_queue_is_none(hass, mock_stage2_config_entry):
    """DIAG-02: stage2_queue_depth returns 0 when _stage2_queue is None (stage2 not started)."""
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
        assert coord._stage2_queue is None
        assert coord.stage2_queue_depth == 0


async def test_stage2_queue_depth_returns_qsize_when_queue_active(hass, mock_stage2_config_entry):
    """DIAG-02: stage2_queue_depth returns qsize() when queue is active."""
    import asyncio

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
        # Manually assign queue with 3 items.
        coord._stage2_queue = asyncio.Queue(maxsize=32)
        shipment = _make_shipment()
        for i in range(3):
            job = Stage2Job(
                storage_key=f"key{i}",
                normalized_tn=f"TN{i}",
                shipment=shipment,
                html_body="<html/>",
                message_id=f"msg{i}",
                meta={},
            )
            coord._stage2_queue.put_nowait(job)
        assert coord.stage2_queue_depth == 3


async def test_diag_02_stage2_enqueued_total_increments_on_successful_put(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_enqueued_total increments on each successful put_nowait."""
    import asyncio

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
        coord._stage2_queue = asyncio.Queue(maxsize=32)
        shipment = _make_shipment()
        # Enqueue 2 distinct tracking numbers.
        coord._enqueue_stage2("TN001", "key1", shipment, "<html/>", message_id="m1", meta={})
        coord._enqueue_stage2("TN002", "key2", shipment, "<html/>", message_id="m2", meta={})
        assert coord.diagnostics.stage2_enqueued_total == 2


async def test_diag_02_stage2_enqueued_total_does_not_increment_on_dedup_skip(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_enqueued_total does NOT increment on QUE-06 dedup short-circuit."""
    import asyncio

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
        coord._stage2_queue = asyncio.Queue(maxsize=32)
        shipment = _make_shipment()
        # First enqueue succeeds.
        coord._enqueue_stage2("TN001", "key1", shipment, "<html/>", message_id="m1", meta={})
        assert coord.diagnostics.stage2_enqueued_total == 1
        # Second enqueue with same key hits dedup short-circuit — counter stays at 1.
        coord._enqueue_stage2("TN001", "key1", shipment, "<html/>", message_id="m1", meta={})
        assert coord.diagnostics.stage2_enqueued_total == 1


async def test_diag_02_stage2_dropped_backpressure_total_increments_on_queue_full(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_dropped_backpressure_total increments when queue is full;
    stage2_enqueued_total does NOT increment on the dropped item.
    """
    import asyncio

    from homeassistant.components import persistent_notification

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
        patch.object(persistent_notification, "async_create"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        coord._stage2_queue = asyncio.Queue(maxsize=1)
        shipment = _make_shipment()
        # First enqueue fills the queue.
        coord._enqueue_stage2("TN001", "key1", shipment, "<html/>", message_id="m1", meta={})
        assert coord.diagnostics.stage2_enqueued_total == 1
        # Second enqueue with distinct key hits QueueFull.
        coord._enqueue_stage2("TN002", "key2", shipment, "<html/>", message_id="m2", meta={})
        assert coord.diagnostics.stage2_dropped_backpressure_total == 1
        # Dropped item does NOT count as enqueued.
        assert coord.diagnostics.stage2_enqueued_total == 1


async def test_diag_02_stage2_failed_total_increments_on_each_ollama_failure(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_failed_total increments on each OllamaTransientError failure."""
    from homeassistant.components import persistent_notification

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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[
                OllamaTransientError("e1"),
                OllamaTransientError("e2"),
                OllamaTransientError("e3"),
            ]
        )
        coord._extractor = fail_extractor
        for i in range(3):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)
        assert coord.diagnostics.stage2_failed_total == 3


async def test_diag_02_stage2_schema_error_total_increments_only_on_schema_errors(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_schema_error_total only increments for OllamaSchemaError;
    stage2_failed_total counts both OllamaTransientError and OllamaSchemaError.
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import OllamaSchemaError, OllamaTransientError

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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient"),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        fail_extractor = MagicMock()
        fail_extractor.async_extract = AsyncMock(
            side_effect=[
                OllamaTransientError("transient"),
                OllamaSchemaError("schema1"),
                OllamaSchemaError("schema2"),
            ]
        )
        coord._extractor = fail_extractor
        for i in range(3):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)
        # All 3 count as failures.
        assert coord.diagnostics.stage2_failed_total == 3
        # Only 2 are schema errors.
        assert coord.diagnostics.stage2_schema_error_total == 2


async def test_diag_02_stage2_failed_total_does_not_increment_on_parcelapp_errors(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_failed_total does NOT increment on ParcelAppTransientError (D-05 scope)."""
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import ParcelAppTransientError
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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=5.0)
        )
        coord._extractor = ok_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=[ParcelAppTransientError("net")] * 5
        )
        for i in range(5):
            job = _make_job(normalized_tn=f"TN{i:04d}")
            await coord._async_process_stage2_job(job)
        # ParcelApp errors must NOT bump stage2_failed_total.
        assert coord.diagnostics.stage2_failed_total == 0


async def test_diag_02_stage2_succeeded_total_increments_only_at_line_710_path(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_succeeded_total increments on real 2xx POST; stays unchanged on
    ParcelAppAlreadyAddedError (graceful-reject, D-06).
    """
    from homeassistant.components import persistent_notification

    from custom_components.shop2parcel.api.exceptions import ParcelAppAlreadyAddedError
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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=5.0)
        )
        coord._extractor = ok_extractor
        # First call: real 2xx success.
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()
        success_job = _make_job(normalized_tn="TNSUCCESS")
        await coord._async_process_stage2_job(success_job)
        assert coord.diagnostics.stage2_succeeded_total == 1

        # Second call: ParcelAppAlreadyAddedError (graceful-reject) — succeeded_total stays at 1.
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock(
            side_effect=ParcelAppAlreadyAddedError("already")
        )
        already_job = _make_job(normalized_tn="TNALREADY")
        await coord._async_process_stage2_job(already_job)
        assert coord.diagnostics.stage2_succeeded_total == 1


async def test_diag_02_stage2_conflict_total_increments_on_mrg_03_conflict(
    hass, mock_stage2_config_entry
):
    """DIAG-02: stage2_conflict_total increments in MRG-03 branch; stage2_failed_total stays 0."""
    from homeassistant.components import persistent_notification

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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Extractor returns a locked tracking_number different from Stage-1 → conflict.
        stage2_result = Stage2Result(
            locked={
                "tracking_number": "STAGE2_CONFLICT_TN",
                "carrier_name": None,
                "order_name": None,
            },
            custom={},
            passes_used=1,
            latency_ms=10.0,
        )
        conflict_extractor = MagicMock()
        conflict_extractor.async_extract = AsyncMock(return_value=stage2_result)
        coord._extractor = conflict_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        job = _make_job(normalized_tn="1Z999AA10123456784")
        await coord._async_process_stage2_job(job)

        # conflict counted; failure counter NOT bumped.
        assert coord.diagnostics.stage2_conflict_total == 1
        assert coord.diagnostics.stage2_failed_total == 0


async def test_diag_02_stage2_conflict_total_zero_when_no_conflict(hass, mock_stage2_config_entry):
    """DIAG-02: stage2_conflict_total stays 0 when Stage-2 result matches Stage-1."""
    from homeassistant.components import persistent_notification

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
        patch.object(persistent_notification, "async_create"),
        patch.object(persistent_notification, "async_dismiss"),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        # Extractor returns empty locked dict (no overrides) → no conflict.
        stage2_result = Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=5.0)
        ok_extractor = MagicMock()
        ok_extractor.async_extract = AsyncMock(return_value=stage2_result)
        coord._extractor = ok_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        job = _make_job(normalized_tn="TNNOCONFLICT")
        await coord._async_process_stage2_job(job)

        assert coord.diagnostics.stage2_conflict_total == 0


# ---------------------------------------------------------------------------
# Phase 23 Plan 01 — Wave 0 RED scaffolds
# AC-1: extractor called despite quota exhausted
# AC-2: no POST + item in _pending_posts when quota exhausted
# AC-3: pending item not re-extracted on drain
# AC-4: drain POSTs pending items when quota freed (cap respected)
# AC-5: debug mode — extract runs, no POST, no store write, dry_run_suppressed
# AC-8: quota WARNING throttled to once per poll
# ---------------------------------------------------------------------------


async def test_extractor_called_despite_quota_exhausted(hass, mock_stage2_config_entry):
    """AC-1: With parcelapp quota exhausted, the extractor is still awaited once.

    Decoupled semantics (Phase 23 LD-03): LLM extraction is independent of parcelapp
    quota. The extractor must run for every dequeued job; the POST gate fires separately.
    RED until plan 03 moves the quota guard to after extraction.
    """
    import time as stdlib_time

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=8.0)
        )
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Quota is exhausted.
        coord._quota_exhausted_until = int(stdlib_time.time()) + 9999

        job = _make_job(normalized_tn="TNAC1")
        await coord._async_process_stage2_job(job)

        # AC-1: extractor was called exactly once.
        spy_extractor.async_extract.assert_awaited_once()
        # LLM attempts counter incremented.
        assert coord._diagnostics.stage2_llm_attempts_total == 1


async def test_quota_exhausted_no_post_item_in_pending_posts(hass, mock_stage2_config_entry):
    """AC-2: With quota exhausted, no parcelapp POST is attempted and the merged
    shipment is recorded as post-pending in coord._pending_posts.

    RED until plan 02 (store schema) and plan 03 (quota gate decoupling) land.
    """
    import time as stdlib_time

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=8.0)
        )
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Quota is exhausted.
        coord._quota_exhausted_until = int(stdlib_time.time()) + 9999

        job = _make_job(normalized_tn="TNAC2")
        await coord._async_process_stage2_job(job)

        # AC-2a: no POST was attempted.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()
        # AC-2b: merged shipment recorded as post-pending (durable for next quota window).
        assert job.storage_key in coord._pending_posts


async def test_pending_post_not_re_extracted_on_drain(hass, mock_stage2_config_entry):
    """AC-3: A quota-blocked item pre-populated in _pending_posts is NOT re-extracted
    when the drain runs — the already-merged shipment is used directly.

    RED until plan 04 adds _async_drain_pending_posts.
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
            return_value="<html>body</html>",
        ),
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock()
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Pre-populate _pending_posts with an already-merged shipment.
        merged_shipment = _make_shipment(message_id="msg-pending-1")
        merged_shipment_updated = ShipmentData(
            tracking_number="1ZPENDING123",
            carrier_name="UPS",
            order_name="#9001",
            message_id="msg-pending-1",
            email_date=1700000001,
        )
        coord._pending_posts = {"storage_key_1": merged_shipment_updated}

        # Quota is NOT exhausted.
        coord._quota_exhausted_until = None

        # Trigger drain (added in plan 04 — RED until then).
        await coord._async_drain_pending_posts()

        # AC-3: extractor was NOT called — drain uses cached merged shipment.
        spy_extractor.async_extract.assert_not_called()
        # POST was called for the pending item.
        mock_parcel_cls.return_value.async_add_delivery.assert_awaited_once()
        # Item removed from _pending_posts after successful drain.
        assert "storage_key_1" not in coord._pending_posts


async def test_drain_posts_pending_when_quota_freed(hass, mock_stage2_config_entry):
    """AC-4: When quota frees, _async_drain_pending_posts POSTs pending items
    up to MAX_STAGE2_POSTS_PER_POLL. Populating >5 items asserts at most 5 POSTs.

    RED until plan 04 adds _async_drain_pending_posts.
    """
    import time as stdlib_time

    from custom_components.shop2parcel.const import MAX_STAGE2_POSTS_PER_POLL

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Quota freed: set to a past epoch so it is no longer exhausted.
        coord._quota_exhausted_until = int(stdlib_time.time()) - 1

        # Populate 7 pending items — more than MAX_STAGE2_POSTS_PER_POLL (= 5).
        for i in range(7):
            shipment = ShipmentData(
                tracking_number=f"1ZDRAIN{i:04d}",
                carrier_name="UPS",
                order_name=f"#{9000 + i}",
                message_id=f"msg-drain-{i}",
                email_date=1700000000 + i,
            )
            coord._pending_posts[f"drain_key_{i}"] = shipment

        # Trigger drain (added in plan 04 — RED until then).
        await coord._async_drain_pending_posts()

        # AC-4: at most MAX_STAGE2_POSTS_PER_POLL POSTs were made.
        assert (
            mock_parcel_cls.return_value.async_add_delivery.await_count <= MAX_STAGE2_POSTS_PER_POLL
        )


async def test_debug_mode_extractor_runs_no_post_no_store_write(hass, mock_stage2_config_entry):
    """AC-5: With debug_mode=True, the extractor IS called but no parcelapp POST is made,
    no dedup/store entry is written, _pending_posts stays empty, and a dry_run_suppressed
    scan event is emitted.

    LD-02: Stage-2 POST must follow debug suppression. Extraction still runs so dry-run
    testing exercises the real extraction path.
    RED until plan 03 adds the debug-mode early-return after extraction.
    """
    import logging

    from custom_components.shop2parcel.const import CONF_DEBUG_MODE
    from custom_components.shop2parcel.extractors.types import Stage2Result

    mock_stage2_config_entry.add_to_hass(hass)
    # Enable debug mode while preserving the Ollama URL so the extractor stays wired.
    hass.config_entries.async_update_entry(
        mock_stage2_config_entry,
        options={
            CONF_DEBUG_MODE: True,
            CONF_OLLAMA_URL: "http://localhost:11434",
            CONF_OLLAMA_MODEL: "qwen3.5:2b",
            CONF_OLLAMA_TIMEOUT: 60,
            CONF_QUEUE_MAXLEN: DEFAULT_QUEUE_MAXLEN,
            CONF_CUSTOM_FIELDS: [],
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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=6.0)
        )
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        job = _make_job(normalized_tn="TNAC5DEBUG")
        await coord._async_process_stage2_job(job)

        # AC-5a: extractor IS called (dry-run exercises real extraction path).
        spy_extractor.async_extract.assert_awaited_once()
        # AC-5b: no parcelapp POST attempted.
        mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()
        # AC-5c: a dry_run_suppressed scan event was emitted.
        dry_run_events = [
            e for e in coord._diagnostics.scan_events if e.get("outcome") == "dry_run_suppressed"
        ]
        assert len(dry_run_events) >= 1, (
            f"Expected at least 1 dry_run_suppressed event, got: {list(coord._diagnostics.scan_events)}"
        )
        # AC-5d: _pending_posts is empty — debug mode never accumulates post-pending items.
        assert coord._pending_posts == {}
        # AC-5e: tracking number NOT written to submitted_tracking_numbers (dedup not written).
        assert job.normalized_tn not in coord._submitted_tracking_numbers


async def test_quota_skip_warning_throttled_after_first(hass, mock_stage2_config_entry, caplog):
    """AC-8: The quota-skip WARNING is emitted at most once per poll when quota is exhausted.

    Processing two jobs with the same exhausted-quota condition should produce exactly one
    WARNING log record matching the quota-skip phrase. The _stage2_quota_warned_this_poll
    flag is set to True after the first skip.
    RED until plan 03 adds the _stage2_quota_warned_this_poll throttle flag.
    """
    import logging
    import time as stdlib_time

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
        patch.object(Shop2ParcelCoordinator, "_async_save_store", new_callable=AsyncMock),
        patch("custom_components.shop2parcel.coordinator.ParcelAppClient") as mock_parcel_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        coord = GmailCoordinator(hass, mock_stage2_config_entry)
        await coord._async_load_store()

        spy_extractor = MagicMock()
        spy_extractor.async_extract = AsyncMock(
            return_value=Stage2Result(locked={}, custom={}, passes_used=1, latency_ms=7.0)
        )
        coord._extractor = spy_extractor
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        # Exhaust quota.
        coord._quota_exhausted_until = int(stdlib_time.time()) + 9999

        with caplog.at_level(logging.WARNING, logger="custom_components.shop2parcel.coordinator"):
            # Process two jobs with distinct tracking numbers in the same poll.
            job1 = _make_job(normalized_tn="TNAC8A")
            job2 = _make_job(normalized_tn="TNAC8B")
            await coord._async_process_stage2_job(job1)
            await coord._async_process_stage2_job(job2)

        # AC-8a: count WARNING records that mention the quota-skip phrase.
        quota_warn_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "quota" in r.getMessage().lower()
        ]
        assert len(quota_warn_records) <= 1, (
            f"Expected at most 1 quota-skip WARNING, got {len(quota_warn_records)}: "
            f"{[r.getMessage() for r in quota_warn_records]}"
        )
        # AC-8b: throttle flag is set after first skip.
        assert coord._stage2_quota_warned_this_poll is True
