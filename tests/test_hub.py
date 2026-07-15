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


async def test_check_and_mark_new_tn_returns_false_then_true(hass):
    """R1: check_and_mark on a NEW tn returns False and inserts it; a
    subsequent call with the same tn returns True (already present).
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)

    assert hub.check_and_mark("1Z999AA") is False
    assert hub.is_submitted("1Z999AA") is True
    assert hub.check_and_mark("1Z999AA") is True


def test_check_and_mark_existing_tn_does_not_change_length(hass):
    """R1: marking an already-present tn returns True without changing the
    set length.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.check_and_mark("1Z999AA")
    length_before = hub.submitted_count

    assert hub.check_and_mark("1Z999AA") is True
    assert hub.submitted_count == length_before


def test_check_and_mark_fifo_evicts_oldest_at_1001st_insert(hass):
    """R1/AC: after marking 1001 distinct TNs, len == 1000, the first-inserted
    TN was FIFO-evicted, and the 1001st TN is present.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    tns = [f"TN{i:05d}" for i in range(1001)]
    for tn in tns:
        hub.check_and_mark(tn)

    assert len(hub._submitted_tracking_numbers) == 1000
    assert hub.is_submitted(tns[0]) is False
    assert hub.is_submitted(tns[-1]) is True


def test_is_submitted_does_not_mutate_set(hass):
    """R1: is_submitted is a read-only accessor — length is unchanged after
    calls for both present and absent tns.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.check_and_mark("1Z999AA")
    length_before = hub.submitted_count

    assert hub.is_submitted("1Z999AA") is True
    assert hub.submitted_count == length_before

    assert hub.is_submitted("NEVER-SEEN") is False
    assert hub.submitted_count == length_before


def test_submitted_count_matches_set_length(hass):
    """R1: submitted_count equals len(_submitted_tracking_numbers) after a
    sequence of marks.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    for tn in ["A", "B", "C"]:
        hub.check_and_mark(tn)

    assert hub.submitted_count == 3
    assert hub.submitted_count == len(hub._submitted_tracking_numbers)


def test_check_and_mark_stores_tn_verbatim_no_normalization(hass):
    """R1/Prohibition: the hub does not re-normalize — a lowercase or
    whitespace-padded tn is stored/read back exactly as passed.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.check_and_mark("1z 999aa")

    assert hub.is_submitted("1z 999aa") is True
    assert hub.is_submitted("1Z999AA") is False


# ---------------------------------------------------------------------------
# seed_from_list tests (Task 2 — union-merge migration seeding)
# ---------------------------------------------------------------------------


def test_seed_from_list_union_no_reorder_on_overlap(hass):
    """R3: seed_from_list of two overlapping lists yields the union with no
    duplicate keys; an overlapping key keeps its first-write position (not
    moved to the end).
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.seed_from_list(["A", "B"])
    hub.seed_from_list(["B", "C"])

    assert set(hub._submitted_tracking_numbers.keys()) == {"A", "B", "C"}
    assert list(hub._submitted_tracking_numbers.keys()) == ["A", "B", "C"]


def test_seed_from_list_double_seed_is_idempotent(hass):
    """R3: seeding the same list twice is idempotent — no duplicate, no
    position move, len unchanged.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.seed_from_list(["A", "B", "C"])
    keys_before = list(hub._submitted_tracking_numbers.keys())
    len_before = hub.submitted_count

    hub.seed_from_list(["A", "B", "C"])

    assert list(hub._submitted_tracking_numbers.keys()) == keys_before
    assert hub.submitted_count == len_before


def test_seed_from_list_caps_at_1000_after_seeding(hass):
    """R3/Prohibition: seed_from_list of 1200 distinct TNs leaves len == 1000
    — the FIFO cap is enforced AFTER seeding, not only inside check_and_mark.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    tns = [f"TN{i:05d}" for i in range(1200)]
    hub.seed_from_list(tns)

    assert len(hub._submitted_tracking_numbers) == 1000
    assert hub.is_submitted(tns[0]) is False
    assert hub.is_submitted(tns[-1]) is True


def test_seed_from_list_empty_is_safe_no_op(hass):
    """R3/AC: seed_from_list([]) is a safe no-op — no crash, set unchanged."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.check_and_mark("A")

    hub.seed_from_list([])

    assert list(hub._submitted_tracking_numbers.keys()) == ["A"]
    assert hub.submitted_count == 1


def test_seed_from_list_ignores_non_str_items(hass):
    """T-30-02: non-str items passed to seed_from_list are dropped (defensive,
    mirrors the coordinator's isinstance guard).
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.seed_from_list(["A", 123, None, "B", 4.5])

    assert set(hub._submitted_tracking_numbers.keys()) == {"A", "B"}


# ---------------------------------------------------------------------------
# Persistence tests (Plan 30-02 — async_save / async_load)
# ---------------------------------------------------------------------------


async def test_async_save_writes_version_and_submitted_tracking_numbers(hass):
    """async_save persists both 'version' and 'submitted_tracking_numbers'
    (the current dedup set keys) to the shared store."""
    from custom_components.shop2parcel.hub import (  # noqa: PLC0415
        SHARED_STORAGE_VERSION,
        Shop2ParcelHub,
    )

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        hub.check_and_mark("TN-A")
        hub.check_and_mark("TN-B")

        await hub.async_save()

        last_call = mock_store_cls.return_value.async_save.call_args
        payload = last_call.args[0]
        assert payload["version"] == SHARED_STORAGE_VERSION
        assert set(payload["submitted_tracking_numbers"]) == {"TN-A", "TN-B"}

        await hub.async_shutdown()


async def test_async_shutdown_flushes_submitted_tracking_numbers(hass):
    """async_shutdown must flush the dedup set, not just the version — guards
    against regressing to the Phase 29 shutdown that saved only {'version': 1}."""
    from custom_components.shop2parcel.hub import (  # noqa: PLC0415
        SHARED_STORAGE_VERSION,
        Shop2ParcelHub,
    )

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        hub.check_and_mark("TN-SHUTDOWN")

        await hub.async_shutdown()

        last_call = mock_store_cls.return_value.async_save.call_args
        payload = last_call.args[0]
        assert payload["version"] == SHARED_STORAGE_VERSION
        assert "TN-SHUTDOWN" in payload["submitted_tracking_numbers"]


# ---------------------------------------------------------------------------
# Restart round-trip tests (Plan 30-02 — async_load)
# ---------------------------------------------------------------------------


async def test_async_load_restart_round_trip_restores_tns(hass):
    """A save-then-reload round-trip restores every TN: hubA marks TNs and
    async_saves; hubB (whose store returns hubA's payload) has is_submitted
    True and check_and_mark True for each prior TN after async_setup."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub_a = Shop2ParcelHub(hass)
        await hub_a.async_setup()
        hub_a.check_and_mark("TN-1")
        hub_a.check_and_mark("TN-2")
        await hub_a.async_save()
        saved_payload = mock_store_cls.return_value.async_save.call_args.args[0]
        await hub_a.async_shutdown()

        # Simulated restart: a fresh hub's store returns hubA's saved payload.
        mock_store_cls.return_value.async_load = AsyncMock(return_value=saved_payload)
        mock_store_cls.return_value.async_save.reset_mock()

        hub_b = Shop2ParcelHub(hass)
        await hub_b.async_setup()

        assert hub_b.is_submitted("TN-1") is True
        assert hub_b.is_submitted("TN-2") is True
        assert hub_b.check_and_mark("TN-1") is True
        assert hub_b.check_and_mark("TN-2") is True

        await hub_b.async_shutdown()


async def test_async_load_non_list_submitted_tracking_numbers_loads_empty(hass, caplog):
    """T-30-04: a non-list 'submitted_tracking_numbers' value loads as empty
    with a WARNING — no crash."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={"version": 1, "submitted_tracking_numbers": "notalist"}
        )
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        with caplog.at_level(logging.WARNING):
            await hub.async_setup()

        assert hub.submitted_count == 0
        assert "submitted_tracking_numbers" in caplog.text

        await hub.async_shutdown()


async def test_async_load_absent_key_loads_empty_set(hass):
    """R2 empty backstop: a restart with an empty/absent
    'submitted_tracking_numbers' key loads an empty set with no crash
    (Phase 29 seeds {'version': 1})."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value={"version": 1})
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        assert hub.submitted_count == 0

        await hub.async_shutdown()


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
