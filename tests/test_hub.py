"""Phase 29 Plan 01: Shop2ParcelHub lifecycle + wiring tests.

Covers LIFE-01..05 (SPEC.md R1-R5): hub singleton creation exactly once under
concurrent setup, the constructor-race asyncio.Lock, reference-counted
attach/detach lifecycle, the shop2parcel.__shared__ store version handling
(including the T-29-01 corrupt-version backstop), and — from Phase 32 Plan 03
onward — the real shared Stage-2 worker (_async_hub_worker, replacing the
Phase 29 _stub_worker): FIFO dispatch, entry_id resolution/skip, reload-to-
fresh-coordinator routing, and the crash-isolation ladder.

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


# ---------------------------------------------------------------------------
# Phase 30-03 (DEDUP-01..03): migration union + restart persistence
#
# Both tests below construct coordinators directly (GmailCoordinator /
# ImapCoordinator). They rely on conftest.py's autouse
# `_auto_attach_test_hub` fixture, which caches ONE shared, I/O-free test
# hub per hass instance under hass.data[DOMAIN]["_test_hub"] and assigns it
# to coordinator._hub on construction — exactly mirroring the production
# topology where every account attaches to the SAME hass-scoped hub. Two
# coordinators constructed in the same test therefore automatically share
# one hub, which is what the migration-union scenario requires.
# ---------------------------------------------------------------------------


async def test_migration_union_merges_overlapping_per_entry_lists(
    hass, mock_config_entry, mock_imap_config_entry
):
    """DEDUP-03: two coordinators (Gmail + IMAP), each with an overlapping
    per-entry submitted_tracking_numbers list, union-merge into the shared
    hub set via seed_from_list when each runs _async_load_store() — no
    duplicates, FIFO-capped, and neither per-entry store snapshot contains
    the 'submitted_tracking_numbers' key afterward (the key lives only in
    the shared shop2parcel.__shared__ store from this plan onward).
    """
    from custom_components.shop2parcel.const import MAX_SUBMITTED_TRACKING_NUMBERS  # noqa: PLC0415
    from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator  # noqa: PLC0415
    from custom_components.shop2parcel.imap_coordinator import ImapCoordinator  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls_a,
    ):
        mock_store_cls_a.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784", "TN_SHARED"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls_a.return_value.async_save = AsyncMock()
        coord_a = GmailCoordinator(hass, mock_config_entry)
        await coord_a._async_load_store()

    with patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls_b:
        mock_store_cls_b.return_value.async_load = AsyncMock(
            return_value={
                # TN_SHARED overlaps with account A's list (R3 adjacency:
                # same TN in two per-entry stores must not double-count).
                "submitted_tracking_numbers": ["TN_SHARED", "9400111899223197428490"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls_b.return_value.async_save = AsyncMock()
        coord_b = ImapCoordinator(hass, mock_imap_config_entry)
        await coord_b._async_load_store()

    # Both coordinators attached to the SAME per-test hub (the autouse fixture
    # caches one hub per hass instance) — this IS the shared-set topology.
    assert coord_a._hub is coord_b._hub
    hub = coord_a._hub

    assert hub.is_submitted("1Z999AA10123456784")
    assert hub.is_submitted("TN_SHARED")
    assert hub.is_submitted("9400111899223197428490")
    # Union, not sum of 2+2: TN_SHARED is counted exactly once.
    assert hub.submitted_count == 3
    assert hub.submitted_count <= MAX_SUBMITTED_TRACKING_NUMBERS

    # Neither per-entry snapshot persists the migrated key anymore (DEDUP-03 —
    # it lives only in the shared store from this plan onward).
    assert "submitted_tracking_numbers" not in coord_a._store_snapshot()
    assert "submitted_tracking_numbers" not in coord_b._store_snapshot()


async def test_restart_persistence_after_migration_reloads_migrated_tns(hass, mock_config_entry):
    """DEDUP-02 end-to-end: after a coordinator's one-time migration seeds and
    saves the shared hub, a FRESH hub instance whose shared store returns the
    saved payload restores the migrated tracking numbers as submitted — no
    re-POST on restart.
    """
    from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord = GmailCoordinator(hass, mock_config_entry)
        await coord._async_load_store()

    hub = coord._hub
    assert hub.is_submitted("1Z999AA10123456784")

    # Capture the payload the migration's hub.async_save() actually wrote to
    # this test's mocked shared Store — this is what a real restart would
    # read back from shop2parcel.__shared__.
    save_calls = hub._store.async_save.await_args_list
    assert save_calls, "a non-empty migration must call hub.async_save()"
    saved_payload = save_calls[-1].args[0]
    assert saved_payload["submitted_tracking_numbers"] == ["1Z999AA10123456784"]

    # Simulated restart: a brand-new hub whose shared store returns that payload.
    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls:
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=saved_payload)
        mock_hub_store_cls.return_value.async_save = AsyncMock()

        fresh_hub = Shop2ParcelHub(hass)
        await fresh_hub.async_setup()

        assert fresh_hub.is_submitted("1Z999AA10123456784")
        # check_and_mark returning True (already-present) is the no-re-POST contract.
        assert fresh_hub.check_and_mark("1Z999AA10123456784") is True

        await fresh_hub.async_shutdown()


async def test_second_restart_after_migration_reseeds_nothing(hass, mock_config_entry):
    """DEDUP-02/DEDUP-03 end-to-end: a SECOND coordinator restart (this
    account's per-entry store now saved WITHOUT the migrated key, per
    _store_snapshot()) migrates nothing new — seed_from_list([]) is a no-op —
    yet the coordinator still serves dedup correctly because the shared hub
    itself was durably persisted by the first restart's migration. No re-POST
    on the second restart either.
    """
    from custom_components.shop2parcel.gmail_coordinator import GmailCoordinator  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)

    # First restart (initial migration): per-entry store still has the old key.
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "submitted_tracking_numbers": ["1Z999AA10123456784"],
                "quota_exhausted_until": None,
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()
        coord1 = GmailCoordinator(hass, mock_config_entry)
        await coord1._async_load_store()

    per_entry_snapshot_after_first_restart = coord1._store_snapshot()
    assert "submitted_tracking_numbers" not in per_entry_snapshot_after_first_restart, (
        "the per-entry store must never persist the key again after this plan"
    )

    # Second restart: THIS account's per-entry store now returns the
    # post-migration snapshot (no 'submitted_tracking_numbers' key at all —
    # exactly what _store_snapshot() produces). A fresh coordinator (sharing
    # the SAME per-test hub via the autouse fixture, mirroring the same
    # hass-scoped hub in production) must migrate/seed nothing new.
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls2,
    ):
        mock_store_cls2.return_value.async_load = AsyncMock(
            return_value=per_entry_snapshot_after_first_restart
        )
        mock_store_cls2.return_value.async_save = AsyncMock()
        coord2 = GmailCoordinator(hass, mock_config_entry)
        await coord2._async_load_store()

    # Same shared hub (per-test fixture) — dedup is served from it, not re-seeded.
    assert coord2._hub is coord1._hub
    assert coord2._hub.is_submitted("1Z999AA10123456784"), (
        "dedup must still be served from the shared hub after a second restart "
        "with no per-entry key to migrate"
    )
    # The second restart's migration was a no-op — its per-entry mock's
    # async_save was never called (nothing to migrate: migrated_tns is empty).
    mock_store_cls2.return_value.async_save.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 31 Plan 01: shared-budget mutators (QUOTA-01, QUOTA-02, QUOTA-04)
# ---------------------------------------------------------------------------


def test_try_consume_returns_false_after_20(hass):
    """QUOTA-01/R1: 20 successful try_consume() calls all return True; the
    21st returns False and used_today stays at PARCELAPP_DAILY_LIMIT.
    """
    from custom_components.shop2parcel.const import PARCELAPP_DAILY_LIMIT  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)

    for _ in range(PARCELAPP_DAILY_LIMIT):
        assert hub.try_consume() is True

    assert hub.try_consume() is False
    assert hub.used_today == PARCELAPP_DAILY_LIMIT


def test_try_consume_two_callers_at_19_fcfs(hass):
    """QUOTA-01/R1: with used_today==19, two back-to-back try_consume() calls
    in one event-loop tick yield exactly one True and one False; used_today
    ends at PARCELAPP_DAILY_LIMIT (never exceeds it) — proves the no-await
    check-and-increment is race-free under a single-threaded loop.
    """
    from custom_components.shop2parcel.const import PARCELAPP_DAILY_LIMIT  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    for _ in range(PARCELAPP_DAILY_LIMIT - 1):
        hub.try_consume()
    assert hub.used_today == PARCELAPP_DAILY_LIMIT - 1

    results = [hub.try_consume(), hub.try_consume()]

    assert sorted(results) == [False, True]
    assert hub.used_today == PARCELAPP_DAILY_LIMIT


def test_refund_consume_clamps_at_zero(hass):
    """QUOTA-02/R2: refund_consume() on used_today==1 goes to 0; a second
    refund on an already-zero counter stays at 0 (no negative).
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.try_consume()
    assert hub.used_today == 1

    hub.refund_consume()
    assert hub.used_today == 0

    hub.refund_consume()
    assert hub.used_today == 0


def test_refund_after_reserve_returns_slot(hass):
    """QUOTA-02/R2: try_consume() to PARCELAPP_DAILY_LIMIT then
    refund_consume() reclaims exactly one slot (used_today == LIMIT - 1).
    """
    from custom_components.shop2parcel.const import PARCELAPP_DAILY_LIMIT  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    for _ in range(PARCELAPP_DAILY_LIMIT):
        hub.try_consume()
    assert hub.used_today == PARCELAPP_DAILY_LIMIT

    hub.refund_consume()

    assert hub.used_today == PARCELAPP_DAILY_LIMIT - 1


def test_record_quota_exhausted_sets_and_maxes(hass):
    """QUOTA-02/R3 (D-06): record_quota_exhausted() with a later timestamp
    then an earlier one leaves quota_exhausted_until at the LATER value —
    max-precedence, never shortening an active block. Calling with None
    falls back to the next UTC midnight (hub._next_midnight_utc()).
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)

    t_early = 1_000_000
    t_late = 2_000_000

    hub.record_quota_exhausted(t_late)
    assert hub.quota_exhausted_until == t_late

    hub.record_quota_exhausted(t_early)
    assert hub.quota_exhausted_until == t_late

    hub2 = Shop2ParcelHub(hass)
    hub2.record_quota_exhausted(None)

    from custom_components.shop2parcel.hub import _next_midnight_utc  # noqa: PLC0415

    assert hub2.quota_exhausted_until == _next_midnight_utc()


def test_poll_cap_shared_across_accounts(hass):
    """QUOTA-04/R4: poll_cap_reached() is False below MAX_STAGE2_POSTS_PER_POLL;
    record_poll_post() bumps the shared per-poll counter until the cap is
    reached. A fresh hub starts with poll_cap_reached() False and
    _stage2_posts_this_poll at 0.
    """
    from custom_components.shop2parcel.const import MAX_STAGE2_POSTS_PER_POLL  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    assert hub.poll_cap_reached() is False
    assert hub._stage2_posts_this_poll == 0

    for _ in range(MAX_STAGE2_POSTS_PER_POLL):
        assert hub.poll_cap_reached() is False
        hub.record_poll_post()

    assert hub.poll_cap_reached() is True

    fresh_hub = Shop2ParcelHub(hass)
    assert fresh_hub.poll_cap_reached() is False
    assert fresh_hub._stage2_posts_this_poll == 0


# ---------------------------------------------------------------------------
# Phase 31 Plan 02: quota persistence + migration (QUOTA-03, QUOTA-05)
# ---------------------------------------------------------------------------


async def test_async_load_quota_round_trip_after_restart(hass):
    """QUOTA-03/R3: hub A reserves 7 slots and records a quota-exhaustion
    window, then async_saves; a fresh hub B whose store returns hub A's
    payload loads used_today==7 and quota_exhausted_until==T — the day's
    usage is NOT reset to 0 on restart (boundary R3)."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    t_exhausted = 1_800_000_000

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub_a = Shop2ParcelHub(hass)
        await hub_a.async_setup()
        for _ in range(7):
            hub_a.try_consume()
        hub_a.record_quota_exhausted(t_exhausted)
        assert hub_a.used_today == 7

        await hub_a.async_save()
        saved_payload = mock_store_cls.return_value.async_save.call_args.args[0]
        await hub_a.async_shutdown()

        # Simulated restart: a fresh hub's store returns hub A's saved payload.
        mock_store_cls.return_value.async_load = AsyncMock(return_value=saved_payload)
        mock_store_cls.return_value.async_save.reset_mock()

        hub_b = Shop2ParcelHub(hass)
        await hub_b.async_setup()

        assert hub_b.used_today == 7
        assert hub_b.quota_exhausted_until == t_exhausted

        await hub_b.async_shutdown()


async def test_async_load_corrupt_quota_values_load_defaults(hass, caplog):
    """T-31-04: a store payload with non-int used_today and non-int
    quota_exhausted_until loads as used_today==0 / quota_exhausted_until==None
    with a WARNING — no crash. used_today_date is set to today so a
    legitimate UTC-rollover reset cannot be mistaken for the corrupt-value
    guard firing."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub, _today_utc_str  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(
            return_value={
                "version": 1,
                "used_today": "seven",
                "used_today_date": _today_utc_str(),
                "quota_exhausted_until": "not-an-int",
            }
        )
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        with caplog.at_level(logging.WARNING):
            await hub.async_setup()

        assert hub._used_today == 0
        assert hub.quota_exhausted_until is None
        assert "used_today" in caplog.text
        assert "quota_exhausted_until" in caplog.text

        await hub.async_shutdown()


def test_migration_quota_max_across_accounts(hass):
    """QUOTA-05/R5: seed_quota_from_account(None) then (T1) then (T2) yields
    quota_exhausted_until == max(T1, T2); used_today stays 0 (migration day
    starts conservatively — boundary R5). Also asserts (WR-03) that each
    non-None seed call re-arms the shared expiry timer, mirroring
    record_quota_exhausted()'s always-arm behavior — a None seed is a no-op
    and must not touch the timer."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    t1 = 1_000_000
    t2 = 2_000_000

    with patch.object(
        hub, "_arm_quota_expiry_timer", wraps=hub._arm_quota_expiry_timer
    ) as mock_arm:
        hub.seed_quota_from_account(None)
        assert hub.quota_exhausted_until is None
        mock_arm.assert_not_called()

        hub.seed_quota_from_account(t1)
        assert hub.quota_exhausted_until == t1
        mock_arm.assert_called_once()

        hub.seed_quota_from_account(t2)
        assert hub.quota_exhausted_until == t2
        assert hub.used_today == 0
        assert mock_arm.call_count == 2


def test_migration_quota_all_none_stays_none(hass):
    """QUOTA-05/R5 (empty): no per-account quota keys present -> repeated
    seed_quota_from_account(None) leaves quota_exhausted_until == None and
    used_today == 0."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.seed_quota_from_account(None)
    hub.seed_quota_from_account(None)

    assert hub.quota_exhausted_until is None
    assert hub.used_today == 0


def test_migration_quota_order_independent(hass):
    """QUOTA-05/R5 (ordering): seeding accounts A-then-B equals B-then-A for
    quota_exhausted_until — max() is commutative."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    t_a = 1_500_000
    t_b = 2_500_000

    hub_ab = Shop2ParcelHub(hass)
    hub_ab.seed_quota_from_account(t_a)
    hub_ab.seed_quota_from_account(t_b)

    hub_ba = Shop2ParcelHub(hass)
    hub_ba.seed_quota_from_account(t_b)
    hub_ba.seed_quota_from_account(t_a)

    assert hub_ab.quota_exhausted_until == hub_ba.quota_exhausted_until == max(t_a, t_b)


async def test_poll_counter_not_persisted(hass):
    """QUOTA-04 (constraint): _stage2_posts_this_poll is ephemeral by design
    — record_poll_post() bumps it, but async_save()/async_load() never
    round-trips it; a fresh hub over the saved payload always starts at 0."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub_a = Shop2ParcelHub(hass)
        await hub_a.async_setup()
        hub_a.record_poll_post()
        assert hub_a._stage2_posts_this_poll == 1

        await hub_a.async_save()
        saved_payload = mock_store_cls.return_value.async_save.call_args.args[0]
        assert "_stage2_posts_this_poll" not in saved_payload
        assert "stage2_posts_this_poll" not in saved_payload
        await hub_a.async_shutdown()

        mock_store_cls.return_value.async_load = AsyncMock(return_value=saved_payload)
        mock_store_cls.return_value.async_save.reset_mock()

        hub_b = Shop2ParcelHub(hass)
        await hub_b.async_setup()

        assert hub_b._stage2_posts_this_poll == 0

        await hub_b.async_shutdown()


async def test_second_restart_after_quota_key_drop_reseeds_nothing(hass):
    """QUOTA-05: after a first migration (seed_quota_from_account + save), a
    second restart whose per-entry payload no longer carries per-account
    quota keys (the 31-04 coordinator-side migration will not re-call
    seed_quota_from_account once its own snapshot is quota-key-free) does not
    change the shared used_today/quota_exhausted_until — idempotent, no
    re-inflation."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    t_migrated = 1_700_000_000

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub_a = Shop2ParcelHub(hass)
        await hub_a.async_setup()
        # First migration: one per-account store seeds its quota_exhausted_until.
        hub_a.seed_quota_from_account(t_migrated)
        await hub_a.async_save()
        first_restart_payload = mock_store_cls.return_value.async_save.call_args.args[0]
        await hub_a.async_shutdown()

    # Second restart: a fresh hub loads the persisted (post-first-migration)
    # payload. No further seed_quota_from_account calls happen (mirrors a
    # per-entry store whose quota keys have already been dropped).
    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls2:
        mock_store_cls2.return_value.async_load = AsyncMock(return_value=first_restart_payload)
        mock_store_cls2.return_value.async_save = AsyncMock()

        hub_b = Shop2ParcelHub(hass)
        await hub_b.async_setup()

        assert hub_b.quota_exhausted_until == t_migrated
        assert hub_b.used_today == 0

        await hub_b.async_shutdown()


# ---------------------------------------------------------------------------
# Phase 31 Plan 03: hub-owned timers (QUOTA-03, QUOTA-04)
# ---------------------------------------------------------------------------


async def test_single_midnight_timer_after_two_accounts(hass):
    """QUOTA-03/R3: async_setup() arms exactly one midnight timer; attach()
    never arms another — there is only one hub instance, so two attach()
    calls leave hub._midnight_unsub as the same single handle (no
    per-account timer storm)."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        assert hub._midnight_unsub is not None
        first_handle = hub._midnight_unsub

        coordinator_a = MagicMock()
        coordinator_b = MagicMock()
        hub.attach(coordinator_a)
        hub.attach(coordinator_b)

        assert hub._midnight_unsub is first_handle, "attach() must not arm a second midnight timer"

        await hub.async_shutdown()


async def test_midnight_tick_resets_used_today(hass):
    """QUOTA-03/R3: the UTC-midnight timer forces the used_today rollover
    reset and reschedules itself for the next midnight.

    Drives _on_midnight directly (mirrors test_coordinator.py's
    test_midnight_refresh_resets_used_today) rather than via
    async_fire_time_changed: the hub's _maybe_reset_used_today reads the
    real wall-clock UTC date, which a simulated HA time-fire does not
    advance — so a stale used_today_date is set explicitly to force the
    rollover check to trip.
    """
    import homeassistant.util.dt as dt_util  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()
        assert hub._midnight_unsub is not None, "async_setup must schedule the midnight timer"
        # async_setup already scheduled a real next-midnight timer. Cancel it
        # before driving _on_midnight directly below, so _on_midnight's own
        # self-reschedule is the only handle left (no leaked prior timer).
        hub._midnight_unsub()
        hub._midnight_unsub = None

        hub.try_consume()
        hub.try_consume()
        hub.used_today_date = "2000-01-01"  # stale prior day forces a reset
        assert hub._used_today == 2

        hub._on_midnight(dt_util.utcnow())

        assert hub._used_today == 0, "midnight tick must reset used_today"
        assert hub._midnight_unsub is not None, "midnight timer must reschedule itself"

        await hub.async_shutdown()


async def test_quota_expiry_timer_clears_block(hass):
    """QUOTA-03/R3 (D-04): record_quota_exhausted() re-arms the single hub
    expiry timer; when it fires, quota_exhausted_until is cleared and
    quota_is_exhausted reads False."""
    import time as time_module  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    import homeassistant.util.dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import (
        async_fire_time_changed,  # noqa: PLC0415
    )

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        hub.record_quota_exhausted(int(time_module.time()) + 30)
        assert hub.quota_is_exhausted is True
        assert hub._quota_expiry_unsub is not None

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
        await hass.async_block_till_done()

        assert hub.quota_exhausted_until is None, "expiry timer must clear the stale block"
        assert hub.quota_is_exhausted is False

        await hub.async_shutdown()


async def test_poll_window_tick_resets_counter(hass):
    """QUOTA-04/R4: firing time forward by HUB_STAGE2_POLL_WINDOW resets the
    shared per-poll Stage-2 POST counter to 0."""
    from datetime import timedelta  # noqa: PLC0415

    import homeassistant.util.dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import (
        async_fire_time_changed,  # noqa: PLC0415
    )

    from custom_components.shop2parcel.const import HUB_STAGE2_POLL_WINDOW  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        hub.record_poll_post()
        assert hub._stage2_posts_this_poll == 1

        async_fire_time_changed(
            hass, dt_util.utcnow() + HUB_STAGE2_POLL_WINDOW + timedelta(seconds=1)
        )
        await hass.async_block_till_done()

        assert hub._stage2_posts_this_poll == 0, "poll-window tick must reset the shared counter"

        await hub.async_shutdown()


async def test_shutdown_cancels_all_three_timers(hass):
    """QUOTA-03/R3: async_shutdown() cancels all three hub-owned timers
    (refcount 0) — no lingering-timer warning, all handles cleared to None."""
    import time as time_module  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()
        hub.record_quota_exhausted(int(time_module.time()) + 3600)

        assert hub._midnight_unsub is not None
        assert hub._quota_expiry_unsub is not None
        assert hub._poll_window_unsub is not None

        await hub.async_shutdown()

        assert hub._midnight_unsub is None
        assert hub._quota_expiry_unsub is None
        assert hub._poll_window_unsub is None


# ---------------------------------------------------------------------------
# Phase 32 Plan 02: bounded queue + enqueue/_release_inflight/inflight_count
# (Task 1) and entry_id -> coordinator registry in attach/detach (Task 2)
# ---------------------------------------------------------------------------


def _make_stage2_job(entry_id: str = "entry-a", normalized_tn: str = "TN-1"):
    """Build a minimal real Stage2Job for hub.enqueue() tests.

    Mirrors tests/test_stage2_worker.py's _make_shipment/Stage2Job
    construction pattern.
    """
    from custom_components.shop2parcel.api.email_parser import ShipmentData  # noqa: PLC0415
    from custom_components.shop2parcel.coordinator import Stage2Job  # noqa: PLC0415

    shipment = ShipmentData(
        tracking_number=normalized_tn,
        carrier_name="UPS",
        order_name="#1234",
        message_id=f"msg-{normalized_tn}",
        email_date=1700000000,
    )
    return Stage2Job(
        storage_key=normalized_tn,
        normalized_tn=normalized_tn,
        shipment=shipment,
        html_body="<html/>",
        message_id=f"msg-{normalized_tn}",
        meta={"subject": "test", "from": "test@example.com"},
        entry_id=entry_id,
    )


def test_enqueue_fresh_job_returns_enqueued_and_records_inflight(hass):
    """D-02/D-05/D-06: a fresh normalized_tn on an entry with 0 in-flight is
    ENQUEUED; the job lands in the queue and both _inflight and
    _stage2_enqueued_keys gain the tn."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-1")

    assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

    assert hub._queue.qsize() == 1
    assert hub.inflight_count("entry-a") == 1
    assert "TN-1" in hub._stage2_enqueued_keys


def test_enqueue_duplicate_tn_returns_skipped_dup(hass):
    """R3 dedup: a normalized_tn already in _stage2_enqueued_keys (from ANY
    entry_id) is SKIPPED_DUP; queue size and both structures unchanged."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    first = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-DUP")
    assert hub.enqueue(first) is EnqueueOutcome.ENQUEUED

    dup_from_other_account = _make_stage2_job(entry_id="entry-b", normalized_tn="TN-DUP")
    assert hub.enqueue(dup_from_other_account) is EnqueueOutcome.SKIPPED_DUP

    assert hub._queue.qsize() == 1
    assert hub.inflight_count("entry-b") == 0


def test_enqueue_per_account_cap_boundary(hass):
    """WORK-03 boundary + empty edges: the 8th job for an entry_id is
    ENQUEUED, the 9th is DROPPED_BACKPRESSURE, and a second entry_id with 0
    in-flight still ENQUEUES (R3 empty edge)."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
        EnqueueOutcome,
    )
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)

    for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
        job = _make_stage2_job(entry_id="entry-a", normalized_tn=f"TN-A-{i}")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

    assert hub.inflight_count("entry-a") == STAGE2_PER_ACCOUNT_INFLIGHT_CAP

    ninth_job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-A-9TH")
    assert hub.enqueue(ninth_job) is EnqueueOutcome.DROPPED_BACKPRESSURE
    assert hub.inflight_count("entry-a") == STAGE2_PER_ACCOUNT_INFLIGHT_CAP
    assert "TN-A-9TH" not in hub._stage2_enqueued_keys

    other_job = _make_stage2_job(entry_id="entry-b", normalized_tn="TN-B-1")
    assert hub.enqueue(other_job) is EnqueueOutcome.ENQUEUED
    assert hub.inflight_count("entry-b") == 1


def test_enqueue_global_bound_fills_then_65th_dropped(hass):
    """R3 boundary edge: filling the global queue to HUB_STAGE2_QUEUE_MAXLEN
    (64, spread across 8 entry_ids x 8 jobs so no per-account cap is hit)
    leaves the 65th job DROPPED_BACKPRESSURE via QueueFull, with nothing
    recorded for it."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        HUB_STAGE2_QUEUE_MAXLEN,
        STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
        EnqueueOutcome,
    )
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    accounts = HUB_STAGE2_QUEUE_MAXLEN // STAGE2_PER_ACCOUNT_INFLIGHT_CAP

    for acct in range(accounts):
        for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
            job = _make_stage2_job(entry_id=f"entry-{acct}", normalized_tn=f"TN-{acct}-{i}")
            assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

    assert hub._queue.qsize() == HUB_STAGE2_QUEUE_MAXLEN

    overflow_job = _make_stage2_job(entry_id="entry-overflow", normalized_tn="TN-OVERFLOW")
    assert hub.enqueue(overflow_job) is EnqueueOutcome.DROPPED_BACKPRESSURE
    assert "TN-OVERFLOW" not in hub._stage2_enqueued_keys
    assert hub.inflight_count("entry-overflow") == 0


def test_enqueue_gate_order_dup_before_cap(hass):
    """Gate order is dedup -> per-account cap -> global bound: a duplicate
    tn at a FULL per-account cap still returns SKIPPED_DUP, not
    DROPPED_BACKPRESSURE."""
    from custom_components.shop2parcel.const import (  # noqa: PLC0415
        STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
        EnqueueOutcome,
    )
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
        hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn=f"TN-A-{i}"))
    assert hub.inflight_count("entry-a") == STAGE2_PER_ACCOUNT_INFLIGHT_CAP

    dup_job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-A-0")
    assert hub.enqueue(dup_job) is EnqueueOutcome.SKIPPED_DUP


def test_enqueue_verbatim_tn_no_normalization(hass):
    """Prohibition: normalized_tn is matched verbatim — a byte-different key
    (differing case/whitespace) is treated as new, not re-normalized."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="1z 999aa"))

    different_case = _make_stage2_job(entry_id="entry-b", normalized_tn="1Z999AA")
    assert hub.enqueue(different_case) is EnqueueOutcome.ENQUEUED


def test_release_inflight_decrements_and_deletes_empty_key(hass):
    """_release_inflight removes tn from _inflight[entry_id] (deleting the
    entry_id key once its set empties) and discards it from
    _stage2_enqueued_keys."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="TN-1"))
    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="TN-2"))
    assert hub.inflight_count("entry-a") == 2

    hub._release_inflight("entry-a", "TN-1")
    assert hub.inflight_count("entry-a") == 1
    assert "TN-1" not in hub._stage2_enqueued_keys
    assert "entry-a" in hub._inflight

    hub._release_inflight("entry-a", "TN-2")
    assert hub.inflight_count("entry-a") == 0
    assert "entry-a" not in hub._inflight


def test_release_inflight_idempotent_on_absent_tn(hass):
    """_release_inflight on an absent entry_id/tn is a safe no-op."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="TN-1"))

    hub._release_inflight("entry-unknown", "TN-UNKNOWN")
    hub._release_inflight("entry-a", "TN-NEVER-ENQUEUED")

    assert hub.inflight_count("entry-a") == 1


def test_enqueue_and_release_inflight_are_plain_sync_defs(hass):
    """source: enqueue/_release_inflight are plain `def`, no `await` between
    check and mutate (sync lock-free mutator discipline)."""
    import inspect  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    assert not inspect.iscoroutinefunction(Shop2ParcelHub.enqueue)
    assert not inspect.iscoroutinefunction(Shop2ParcelHub._release_inflight)


# ---------------------------------------------------------------------------
# Phase 32 Plan 02, Task 2: entry_id -> coordinator registry (attach/detach)
# ---------------------------------------------------------------------------


def test_attach_registers_coordinator_by_entry_id(hass):
    """D-01: attach() inserts _coordinators[entry_id] = coordinator."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    coordinator_a = MagicMock()
    coordinator_a.config_entry.entry_id = "entry-a"

    hub.attach(coordinator_a)

    assert hub._coordinators["entry-a"] is coordinator_a


def test_reload_same_entry_id_keeps_fresh_coordinator_registered(hass):
    """D-01 identity guard: attach(new) then detach(old) for the SAME
    entry_id leaves the registry pointing at `new` — `old`'s detach cannot
    evict the freshly-attached replacement."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    old_coordinator = MagicMock()
    old_coordinator.config_entry.entry_id = "entry-a"
    new_coordinator = MagicMock()
    new_coordinator.config_entry.entry_id = "entry-a"

    hub.attach(old_coordinator)
    hub.attach(new_coordinator)
    hub.detach(old_coordinator)

    assert hub._coordinators["entry-a"] is new_coordinator


def test_detach_removes_registry_entry_when_identity_matches(hass):
    """detach() removes the registry entry when it still points at the
    detaching instance."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    coordinator_a = MagicMock()
    coordinator_a.config_entry.entry_id = "entry-a"

    hub.attach(coordinator_a)
    hub.detach(coordinator_a)

    assert "entry-a" not in hub._coordinators


def test_detach_purges_inflight_tns_for_entry(hass):
    """D-05/WORK-04: detach purges the removed account's in-flight tns from
    both _inflight and _stage2_enqueued_keys; inflight_count(entry_id)
    becomes 0."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    coordinator_a = MagicMock()
    coordinator_a.config_entry.entry_id = "entry-a"
    hub.attach(coordinator_a)

    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="TN-1"))
    hub.enqueue(_make_stage2_job(entry_id="entry-a", normalized_tn="TN-2"))
    assert hub.inflight_count("entry-a") == 2

    hub.detach(coordinator_a)

    assert hub.inflight_count("entry-a") == 0
    assert "TN-1" not in hub._stage2_enqueued_keys
    assert "TN-2" not in hub._stage2_enqueued_keys


def test_detach_with_no_inflight_is_a_noop_purge(hass):
    """R4 empty edge: detach for an entry_id with no in-flight tns is a
    no-op purge — no crash."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    coordinator_a = MagicMock()
    coordinator_a.config_entry.entry_id = "entry-a"
    hub.attach(coordinator_a)

    hub.detach(coordinator_a)

    assert hub.inflight_count("entry-a") == 0


def test_attach_detach_with_config_entry_none_does_not_crash(hass):
    """RESEARCH.md Pitfall 5 / D-01: a coordinator whose config_entry is
    None is tolerated — attach/detach skip the registry write, mirroring
    _debug_mode_active's None tolerance. Not exercised by any existing
    test (bare MagicMock() auto-mocks a truthy config_entry)."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    coordinator_bare = MagicMock(config_entry=None)

    hub.attach(coordinator_bare)
    assert hub._coordinators == {}

    hub.detach(coordinator_bare)
    assert hub._coordinators == {}


# ---------------------------------------------------------------------------
# Phase 32 Plan 03: the real shared worker (_async_hub_worker, replacing
# _stub_worker). Task 1: resolve + drain + dispatch + finally release
# (FIFO/routing/skip/reload/empty-idle). Task 2: crash-isolation ladder +
# purge-vs-drain-race backstop.
#
# Driving pattern: hub.async_setup() spawns the real worker, which
# immediately blocks on `await self._queue.get()`. Because Python's asyncio
# is single-threaded and cooperative, the worker task is NOT scheduled to
# run until the test coroutine hits its own `await` — so a sequence of
# purely-synchronous hub calls (enqueue/attach/detach) after the worker has
# started is guaranteed to complete BEFORE the worker ever wakes up. Tests
# exploit this to set up "job enqueued, THEN registry mutated" orderings
# deterministically, then `await hub._queue.join()` to let the worker drain
# and observe the outcome. Every test ends with `await hub.async_shutdown()`
# to cancel the worker cleanly (mirrors test_worker_task_survives_single_
# entry_removal above).
# ---------------------------------------------------------------------------


async def test_worker_dispatches_single_job_and_releases_inflight_in_finally(hass):
    """Task 1 behavior: a single enqueued job is dispatched to its resolved
    coordinator's _async_process_stage2_job exactly once (drain runs first),
    and the hub in-flight slot is released afterward (finally always runs)."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        coordinator_a = MagicMock()
        coordinator_a.config_entry.entry_id = "entry-a"
        coordinator_a._async_drain_pending_posts = AsyncMock()
        coordinator_a._async_process_stage2_job = AsyncMock()
        hub.attach(coordinator_a)

        job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-1")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        await hub._queue.join()

        coordinator_a._async_drain_pending_posts.assert_awaited_once()
        coordinator_a._async_process_stage2_job.assert_awaited_once_with(job)
        assert hub.inflight_count("entry-a") == 0
        assert "TN-1" not in hub._stage2_enqueued_keys

        await hub.async_shutdown()


async def test_worker_processes_two_accounts_fifo_routing_no_cross_leak(hass):
    """Task 1 behavior: two accounts each enqueue one job; the single worker
    processes them in enqueue (FIFO) order and each result lands on its own
    resolved coordinator — never crossed."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        dispatch_order: list[str] = []

        coordinator_a = MagicMock()
        coordinator_a.config_entry.entry_id = "entry-a"
        coordinator_a._async_drain_pending_posts = AsyncMock()
        coordinator_a._async_process_stage2_job = AsyncMock(
            side_effect=lambda job: dispatch_order.append(job.entry_id)
        )
        coordinator_b = MagicMock()
        coordinator_b.config_entry.entry_id = "entry-b"
        coordinator_b._async_drain_pending_posts = AsyncMock()
        coordinator_b._async_process_stage2_job = AsyncMock(
            side_effect=lambda job: dispatch_order.append(job.entry_id)
        )
        hub.attach(coordinator_a)
        hub.attach(coordinator_b)

        job_a = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-A")
        job_b = _make_stage2_job(entry_id="entry-b", normalized_tn="TN-B")
        assert hub.enqueue(job_a) is EnqueueOutcome.ENQUEUED
        assert hub.enqueue(job_b) is EnqueueOutcome.ENQUEUED

        await hub._queue.join()

        assert dispatch_order == ["entry-a", "entry-b"]
        coordinator_a._async_process_stage2_job.assert_awaited_once_with(job_a)
        coordinator_b._async_process_stage2_job.assert_awaited_once_with(job_b)
        assert hub.inflight_count("entry-a") == 0
        assert hub.inflight_count("entry-b") == 0

        await hub.async_shutdown()


async def test_worker_skip_job_with_no_attached_coordinator(hass):
    """Task 1 behavior: a job whose entry_id has NO attached coordinator is
    skipped — no dispatch, no error, task_done + hub in-flight release still
    run, and the worker keeps running."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        job = _make_stage2_job(entry_id="entry-never-attached", normalized_tn="TN-ORPHAN")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        await hub._queue.join()

        assert hub.inflight_count("entry-never-attached") == 0
        assert "TN-ORPHAN" not in hub._stage2_enqueued_keys
        assert not hub._worker_task.done()

        await hub.async_shutdown()


async def test_worker_reload_dispatches_to_fresh_coordinator(hass):
    """Task 1 behavior (WORK-02, D-01 non-negotiable): a job enqueued for
    entry_id X while the OLD coordinator is attached dispatches to the FRESH
    coordinator once X is detached+re-attached BEFORE the worker gets a
    chance to run — dispatch-time resolution, not enqueue-time."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        old_coordinator = MagicMock()
        old_coordinator.config_entry.entry_id = "entry-x"
        old_coordinator._async_drain_pending_posts = AsyncMock()
        old_coordinator._async_process_stage2_job = AsyncMock()

        new_coordinator = MagicMock()
        new_coordinator.config_entry.entry_id = "entry-x"
        new_coordinator._async_drain_pending_posts = AsyncMock()
        new_coordinator._async_process_stage2_job = AsyncMock()

        hub.attach(old_coordinator)
        job = _make_stage2_job(entry_id="entry-x", normalized_tn="TN-RELOAD")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        # No `await` has occurred since enqueue — the worker task cannot
        # have been scheduled yet, so this reload race is deterministic.
        hub.detach(old_coordinator)
        hub.attach(new_coordinator)

        await hub._queue.join()

        new_coordinator._async_process_stage2_job.assert_awaited_once_with(job)
        old_coordinator._async_process_stage2_job.assert_not_awaited()

        await hub.async_shutdown()


async def test_worker_idles_on_empty_queue_no_spin_no_error(hass):
    """Task 1 behavior (R1 empty edge): an empty queue leaves the worker
    blocked on queue.get() — no busy-loop, no exception, worker stays alive."""
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        for _ in range(5):
            await asyncio.sleep(0)

        assert not hub._worker_task.done()
        assert not hub._worker_task.cancelled()

        await hub.async_shutdown()


async def test_async_setup_spawns_async_hub_worker_not_stub(hass):
    """source: async_setup spawns _async_hub_worker (not _stub_worker) and
    _stub_worker is deleted."""
    import inspect  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    assert hasattr(Shop2ParcelHub, "_async_hub_worker")
    assert not hasattr(Shop2ParcelHub, "_stub_worker")

    setup_source = inspect.getsource(Shop2ParcelHub.async_setup)
    assert "self._async_hub_worker()" in setup_source
    assert "_stub_worker" not in setup_source


def test_hub_release_and_task_done_appear_once_in_finally_not_duplicated(hass):
    """source (Task 2 acceptance): the hub in-flight release + task_done
    appear exactly once, in a single finally — not duplicated per except
    branch."""
    import inspect  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    worker_source = inspect.getsource(Shop2ParcelHub._async_hub_worker)
    # Strip the docstring (which narrates both calls in prose) so the count
    # below reflects actual code, not documentation text.
    worker_code = worker_source.split('"""', 2)[-1]
    assert worker_code.count("self._release_inflight(job.entry_id, normalized_tn)") == 1
    assert worker_code.count("self._queue.task_done()") == 1
    # Both calls live inside the single `finally:` block that closes the
    # per-job try — not scattered across the except branches above it.
    finally_block = worker_code.split("finally:", 1)[1]
    assert "self._release_inflight(job.entry_id, normalized_tn)" in finally_block
    assert "self._queue.task_done()" in finally_block


async def test_worker_exception_isolation_join_does_not_hang_and_next_account_processes(
    hass,
):
    """Task 2 behavior: a job whose _async_process_stage2_job raises a
    generic Exception is isolated via coord._record_stage2_failure and
    coord._release_inflight (the per-account msg-gate release); the worker
    continues and a subsequent job for a DIFFERENT account is still
    processed; queue.join() does not hang."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        boom = ValueError("Ollama exploded")
        coordinator_a = MagicMock()
        coordinator_a.config_entry.entry_id = "entry-a"
        coordinator_a._async_drain_pending_posts = AsyncMock()
        coordinator_a._async_process_stage2_job = AsyncMock(side_effect=boom)
        coordinator_a._release_inflight = MagicMock()
        coordinator_a._record_stage2_failure = MagicMock()

        coordinator_b = MagicMock()
        coordinator_b.config_entry.entry_id = "entry-b"
        coordinator_b._async_drain_pending_posts = AsyncMock()
        coordinator_b._async_process_stage2_job = AsyncMock()

        hub.attach(coordinator_a)
        hub.attach(coordinator_b)

        job_a = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-A-FAIL")
        job_b = _make_stage2_job(entry_id="entry-b", normalized_tn="TN-B-OK")
        assert hub.enqueue(job_a) is EnqueueOutcome.ENQUEUED
        assert hub.enqueue(job_b) is EnqueueOutcome.ENQUEUED

        await asyncio.wait_for(hub._queue.join(), timeout=5.0)  # must not hang

        coordinator_a._record_stage2_failure.assert_called_once_with(job_a, boom)
        coordinator_a._release_inflight.assert_called_once_with(job_a)
        coordinator_b._async_process_stage2_job.assert_awaited_once_with(job_b)
        assert hub.inflight_count("entry-a") == 0
        assert hub.inflight_count("entry-b") == 0
        assert not hub._worker_task.done()

        await hub.async_shutdown()


async def test_worker_auth_failure_logged_not_recorded_as_ollama_failure(hass, caplog):
    """Task 2 behavior (WR-05): a job raising ConfigEntryAuthFailed is
    logged and does NOT increment _record_stage2_failure (auth is not an
    Ollama failure), but DOES call coord._release_inflight(job) (the
    per-account msg-gate release); the worker continues."""
    from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: PLC0415

    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        auth_err = ConfigEntryAuthFailed("bad parcelapp key")
        coordinator_a = MagicMock()
        coordinator_a.config_entry.entry_id = "entry-a"
        coordinator_a._async_drain_pending_posts = AsyncMock()
        coordinator_a._async_process_stage2_job = AsyncMock(side_effect=auth_err)
        coordinator_a._release_inflight = MagicMock()
        coordinator_a._record_stage2_failure = MagicMock()
        hub.attach(coordinator_a)

        job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-AUTH")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        with caplog.at_level(logging.ERROR):
            await asyncio.wait_for(hub._queue.join(), timeout=5.0)

        coordinator_a._record_stage2_failure.assert_not_called()
        coordinator_a._release_inflight.assert_called_once_with(job)
        assert "auth error" in caplog.text
        assert hub.inflight_count("entry-a") == 0
        assert not hub._worker_task.done()

        await hub.async_shutdown()


async def test_worker_cancelled_error_propagates_and_stops_worker(hass):
    """Task 2 behavior (R5 adjacency): CancelledError raised during a job
    runs coord._release_inflight(job), the hub in-flight release + task_done
    run in finally, and CancelledError propagates out (stops the worker) —
    the only thing that stops it."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        coordinator_a = MagicMock()
        coordinator_a.config_entry.entry_id = "entry-a"
        coordinator_a._async_drain_pending_posts = AsyncMock()
        coordinator_a._async_process_stage2_job = AsyncMock(side_effect=asyncio.CancelledError())
        coordinator_a._release_inflight = MagicMock()
        hub.attach(coordinator_a)

        job = _make_stage2_job(entry_id="entry-a", normalized_tn="TN-CANCEL")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        await asyncio.wait_for(hub._queue.join(), timeout=5.0)
        for _ in range(5):
            await asyncio.sleep(0)

        coordinator_a._release_inflight.assert_called_once_with(job)
        assert hub.inflight_count("entry-a") == 0
        assert hub._worker_task.done()
        assert hub._worker_task.cancelled()

        # The worker already exited on its own — async_shutdown must not
        # raise when the task is already done.
        await hub.async_shutdown()


async def test_worker_purge_vs_drain_race_backstop_skips_departed_account(hass):
    """Task 2 behavior (🧪 held-out backstop, R4 ordering): a job for
    account X is enqueued, then X is detached (purging its in-flight keys
    and registry entry) BEFORE the worker dequeues it; when the worker
    dequeues, coord resolves to None and the job is skipped — no dispatch,
    no orphaned POST, no crash."""
    from custom_components.shop2parcel.const import EnqueueOutcome  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    with patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_store_cls:
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()

        hub = Shop2ParcelHub(hass)
        await hub.async_setup()

        coordinator_x = MagicMock()
        coordinator_x.config_entry.entry_id = "entry-departed"
        coordinator_x._async_drain_pending_posts = AsyncMock()
        coordinator_x._async_process_stage2_job = AsyncMock()
        hub.attach(coordinator_x)

        job = _make_stage2_job(entry_id="entry-departed", normalized_tn="TN-DEPARTED")
        assert hub.enqueue(job) is EnqueueOutcome.ENQUEUED

        # Detach BEFORE any `await` since enqueue — the worker cannot have
        # dequeued yet, so this purge-vs-drain race is deterministic: the
        # job is dequeued strictly AFTER detach's purge has already run.
        hub.detach(coordinator_x)

        await asyncio.wait_for(hub._queue.join(), timeout=5.0)

        coordinator_x._async_process_stage2_job.assert_not_awaited()
        assert hub.inflight_count("entry-departed") == 0
        assert "TN-DEPARTED" not in hub._stage2_enqueued_keys
        assert not hub._worker_task.done()

        await hub.async_shutdown()
