"""Phase 29 Plan 01: Shop2ParcelHub lifecycle + wiring tests.

Covers LIFE-01..05 (SPEC.md R1-R5): hub singleton creation exactly once under
concurrent setup, the constructor-race asyncio.Lock, reference-counted
attach/detach lifecycle, the hass-scoped worker stub (NotImplementedError on
any dequeue, D-02), and the shop2parcel.__shared__ store version handling
(including the T-29-01 corrupt-version backstop).

Direct-hub tests construct Shop2ParcelHub(hass) directly and mock
Shop2ParcelStore at the hub's own import path. Wiring tests drive the real
async_setup_entry / async_unload_entry via hass.config_entries.async_setup /
async_unload (the same HA config-entry machinery test_init.py already uses
for this integration), patching Gmail/ParcelApp/EmailParser internals plus
both Store import paths (per-entry coordinator.py + shared hub.py) so no
real I/O occurs.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.const import DOMAIN

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config_entry_b() -> MockConfigEntry:
    """A second Gmail-shaped account, distinct from conftest's mock_config_entry.

    Used by the wiring tests that need two concurrently-setup accounts.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token-b",
                "refresh_token": "fake-refresh-token-b",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key-b",
        },
        unique_id="user2@gmail.com",
    )


# ---------------------------------------------------------------------------
# Direct-hub tests (construct Shop2ParcelHub(hass) directly)
# ---------------------------------------------------------------------------


async def test_stub_worker_raises_not_implemented(hass):
    """D-02/R4: enqueuing a job directly (hub._queue.put_nowait) makes the
    stub worker crash with NotImplementedError — no coordinator wiring needed.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        hub._queue.put_nowait("sentinel-job")
        # async_create_background_task tasks are not awaited by
        # hass.async_block_till_done(); give the loop a few ticks to run the
        # stub past its `await self._queue.get()` suspension point.
        for _ in range(5):
            await asyncio.sleep(0)

        assert hub._worker_task.done()
        with pytest.raises(NotImplementedError):
            hub._worker_task.result()


async def test_shared_store_version_written_on_first_setup(hass):
    """R5: an empty shared store gets seeded with {"version": SHARED_STORAGE_VERSION}."""
    from custom_components.shop2parcel.hub import (  # noqa: PLC0415
        SHARED_STORAGE_VERSION,
        Shop2ParcelHub,
    )

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        mock_store_cls.return_value.async_save.assert_called_once_with(
            {"version": SHARED_STORAGE_VERSION}
        )
        await hub.async_shutdown()


async def test_shared_store_version_not_overwritten_on_restart(hass):
    """R5 idempotency: a simulated restart (setup twice) must not reset or
    overwrite an existing version=1 store.
    """
    from custom_components.shop2parcel.hub import (  # noqa: PLC0415
        SHARED_STORAGE_VERSION,
        Shop2ParcelHub,
    )

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        # First "boot": store starts empty, gets seeded.
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub1 = Shop2ParcelHub(hass)
        await hub1.async_setup()
        await hub1.async_shutdown()

        # Simulated restart: the store now reports the persisted payload.
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"version": SHARED_STORAGE_VERSION}
        )
        mock_store_cls.return_value.async_save.reset_mock()

        hub2 = Shop2ParcelHub(hass)
        await hub2.async_setup()

        mock_store_cls.return_value.async_save.assert_not_called()
        await hub2.async_shutdown()


async def test_shared_store_corrupt_version_reset(hass):
    """T-29-01: a non-int "version" value is reset to {"version": 1} instead
    of crashing hub setup.
    """
    from custom_components.shop2parcel.hub import (  # noqa: PLC0415
        SHARED_STORAGE_VERSION,
        Shop2ParcelHub,
    )

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value={"version": "garbage"})
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        mock_store_cls.return_value.async_save.assert_called_once_with(
            {"version": SHARED_STORAGE_VERSION}
        )
        await hub.async_shutdown()


async def test_worker_task_survives_single_entry_removal(hass):
    """R4: detaching one of two attached coordinators leaves hub._worker_task
    alive (not done/cancelled) — the worker is hass-scoped, not entry-scoped.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        coordinator_a = MagicMock()
        coordinator_b = MagicMock()
        hub.attach(coordinator_a)
        hub.attach(coordinator_b)
        assert hub._refcount == 2

        hub.detach(coordinator_a)
        assert hub._refcount == 1

        assert not hub._worker_task.done()
        assert not hub._worker_task.cancelled()

        await hub.async_shutdown()


# ---------------------------------------------------------------------------
# Wiring tests (drive async_setup_entry / async_unload_entry via the real
# hass.config_entries machinery, with two MockConfigEntry accounts)
# ---------------------------------------------------------------------------


async def test_concurrent_setup_creates_hub_once(
    hass, mock_config_entry, mock_config_entry_b, caplog
):
    """R1/R2/LIFE-01/LIFE-05: two concurrent async_setup_entry calls create
    the hub exactly once — "shared hub created" is logged once and
    hass.data[DOMAIN]["__shared__"] holds a single Shop2ParcelHub instance.
    """
    mock_config_entry.add_to_hass(hass)
    mock_config_entry_b.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))

        with caplog.at_level(logging.INFO):
            results = await asyncio.gather(
                hass.config_entries.async_setup(mock_config_entry.entry_id),
                hass.config_entries.async_setup(mock_config_entry_b.entry_id),
            )

    assert all(results)
    assert caplog.text.count("shared hub created") == 1

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = hass.data[DOMAIN]["__shared__"]
    assert isinstance(hub, Shop2ParcelHub)


async def test_init_lock_is_asyncio_lock(hass, mock_config_entry):
    """R2/LIFE-05: after the first async_setup_entry, hass.data[DOMAIN]["_init_lock"]
    is an asyncio.Lock instance.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))

        await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert isinstance(hass.data[DOMAIN]["_init_lock"], asyncio.Lock)


async def test_remove_one_of_two_accounts_leaves_hub(hass, mock_config_entry, mock_config_entry_b):
    """R3/LIFE-02/LIFE-03: after two accounts set up, removing one leaves
    hass.data[DOMAIN]["__shared__"] present with refcount 1.
    """
    mock_config_entry.add_to_hass(hass)
    mock_config_entry_b.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))

        # Both entries were added_to_hass before any setup call, so a single
        # hass.config_entries.async_setup() call bootstraps the "shop2parcel"
        # domain, which sets up ALL of its not-yet-loaded entries together
        # (homeassistant/config_entries.py: "Setting up the component will set
        # up all its config entries") — a second explicit async_setup() call
        # for mock_config_entry_b would raise OperationNotAllowed (already
        # loaded).
        await hass.config_entries.async_setup(mock_config_entry.entry_id)

        hub = hass.data[DOMAIN]["__shared__"]
        assert hub._refcount == 2

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

        assert "__shared__" in hass.data[DOMAIN]
        assert hass.data[DOMAIN]["__shared__"] is hub
        assert hub._refcount == 1

        # Cleanup: unload the remaining account so the hub worker task is
        # cancelled before the test's mocked-Store context exits.
        await hass.config_entries.async_unload(mock_config_entry_b.entry_id)


async def test_remove_last_account_tears_down_hub(hass, mock_config_entry, mock_config_entry_b):
    """R3/R4/LIFE-04: removing the last account tears down the hub —
    hass.data[DOMAIN]["__shared__"] is absent and hub.async_shutdown() was called.
    """
    mock_config_entry.add_to_hass(hass)
    mock_config_entry_b.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))

        # See test_remove_one_of_two_accounts_leaves_hub: a single async_setup()
        # call bootstraps the domain and sets up both pre-added entries together.
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        hub = hass.data[DOMAIN]["__shared__"]

        with patch.object(hub, "async_shutdown", wraps=hub.async_shutdown) as spy_shutdown:
            await hass.config_entries.async_unload(mock_config_entry.entry_id)
            spy_shutdown.assert_not_called()

            await hass.config_entries.async_unload(mock_config_entry_b.entry_id)
            spy_shutdown.assert_called_once()

    assert "__shared__" not in hass.data.get(DOMAIN, {})
