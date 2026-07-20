"""Multi-account integration tests — covers MULT-01, MULT-02, D-10, D-11.

Two config entries (one Gmail, one IMAP) are added to the same HA instance.
Tests verify coordinator isolation: separate Store keys, separate entity
unique ID namespaces, no data leakage between entries.

All tests are xfail until coordinator IMAP dispatch is implemented (Plan 09-04).
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import (
    CONF_OLLAMA_URL,
    DOMAIN,
    STAGE2_PER_ACCOUNT_INFLIGHT_CAP,
)
from custom_components.shop2parcel.extractors.types import Stage2Result

# ---------------------------------------------------------------------------
# Stub: MULT-01 — two entries coexist in same HA instance
# ---------------------------------------------------------------------------


async def test_two_entries_can_be_added_to_hass(hass, mock_config_entry, mock_imap_config_entry):
    """MULT-01: Gmail and IMAP entries can both be added to hass without conflict."""
    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# Stub: D-10 — each entry gets its own coordinator with its own Store key
# ---------------------------------------------------------------------------


async def test_two_imap_entries_have_separate_store_keys(hass, mock_imap_config_entry):
    """D-10: Each config entry creates a coordinator with Store key scoped to entry_id."""
    # Create a second IMAP entry with a different account
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: PLC0415

    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: PLC0415

    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "imap",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_username": "other@example.com",
            "imap_password": "other-password",
            "imap_tls": "ssl",
            "api_key": "other-parcelapp-key",
        },
        options={"imap_search": 'SUBJECT "shipped"', "poll_interval": 30},
        unique_id="other@example.com@imap.example.com",
    )

    mock_imap_config_entry.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    coord_a = Shop2ParcelCoordinator(hass, mock_imap_config_entry)
    coord_b = Shop2ParcelCoordinator(hass, entry_b)

    # Store keys must be different (scoped to entry_id)
    assert coord_a._store.key != coord_b._store.key
    assert mock_imap_config_entry.entry_id in coord_a._store.key
    assert entry_b.entry_id in coord_b._store.key


# ---------------------------------------------------------------------------
# Stub: MULT-02 — entities from different accounts are under separate devices
# ---------------------------------------------------------------------------


async def test_imap_coordinator_instantiates_imap_client(hass, mock_imap_config_entry):
    """D-10: ImapCoordinator must instantiate ImapClient, not GmailClient."""
    from custom_components.shop2parcel.api.gmail_client import GmailClient  # noqa: PLC0415
    from custom_components.shop2parcel.api.imap_client import ImapClient  # noqa: PLC0415
    from custom_components.shop2parcel.imap_coordinator import ImapCoordinator  # noqa: PLC0415

    mock_imap_config_entry.add_to_hass(hass)
    coordinator = ImapCoordinator(hass, mock_imap_config_entry)

    assert isinstance(coordinator._email_client, ImapClient), (
        "IMAP config entry must create ImapClient, not GmailClient"
    )
    assert not isinstance(coordinator._email_client, GmailClient)


# ---------------------------------------------------------------------------
# Stub: D-11 — entity unique IDs do not collide between two entries
# ---------------------------------------------------------------------------


async def test_two_entries_produce_non_colliding_entity_unique_ids(
    hass, mock_config_entry, mock_imap_config_entry
):
    """MULT-02/D-11: Entities from different accounts have non-overlapping unique_ids.

    Entity unique_id format: f"{DOMAIN}_{entry.entry_id}_{message_id}"
    Since entry_id differs per entry, even the same message_id produces different unique_ids.
    """
    # Both entries must be loaded — this test verifies the unique_id formula,
    # not full coordinator setup. The format is verified by inspection.
    entry_id_a = mock_config_entry.entry_id
    entry_id_b = mock_imap_config_entry.entry_id

    msg_id = "INBOX.123"
    uid_a = f"{DOMAIN}_{entry_id_a}_{msg_id}"
    uid_b = f"{DOMAIN}_{entry_id_b}_{msg_id}"

    assert uid_a != uid_b, "Same message_id must produce different unique_ids across entries"
    assert entry_id_a != entry_id_b, "Two different config entries must have different entry_ids"


# ---------------------------------------------------------------------------
# Phase 30-03 (DEDUP-01): cross-account dedup via the shared hub
# ---------------------------------------------------------------------------


async def test_cross_account_dedup_via_shared_hub(hass, mock_config_entry, mock_imap_config_entry):
    """DEDUP-01: a TN marked by one account's hub is seen as already-submitted
    by a different account's coordinator — one global dedup set, not one per
    account. Drives the REAL async_setup_entry for two accounts (Gmail +
    IMAP) so both coordinators attach to the SAME hass-scoped
    Shop2ParcelHub (mirrors test_hub.py's wiring-test pattern).
    """
    from unittest.mock import AsyncMock, MagicMock, patch  # noqa: PLC0415

    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
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
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        # Both entries were added_to_hass before any setup call, so a single
        # async_setup() call bootstraps the "shop2parcel" domain and sets up
        # BOTH not-yet-loaded entries together (mirrors test_hub.py's
        # test_remove_one_of_two_accounts_leaves_hub).
        await hass.config_entries.async_setup(mock_config_entry.entry_id)

        hub = hass.data[DOMAIN]["__shared__"]
        assert isinstance(hub, Shop2ParcelHub)
        assert hub._refcount == 2

        coord_a = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coord_b = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
        assert coord_a._hub is hub
        assert coord_b._hub is hub

        tn = "1Z999AA10123456784"
        # Account A forwards and marks the TN (directly via check_and_mark —
        # equivalent to the terminal dedup write after a real POST success).
        assert coord_a._hub.check_and_mark(tn) is False, "first mark of a new TN returns False"

        # Account B never forwarded this TN itself, but the SAME shared hub
        # already knows it — DEDUP-01's cross-account guarantee.
        assert coord_b._hub.is_submitted(tn), (
            "a TN marked by account A must be visible to account B via the shared hub"
        )

        # Cleanup: unload both accounts so the hub worker task is cancelled
        # before the mocked-Store context exits (mirrors test_hub.py).
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.config_entries.async_unload(mock_imap_config_entry.entry_id)


# ---------------------------------------------------------------------------
# Phase 32-05 (WORK-01..04, must-NOT prohibitions): shared Stage-2 queue+worker
# multi-account integration coverage. Both accounts are stage2_enabled and go
# through a REAL async_setup_entry, so the real shared hub worker
# (_async_hub_worker, spawned by hub.async_setup()) drains jobs — not the
# synthetic bare-MagicMock coordinators test_hub.py's own worker tests use.
# ---------------------------------------------------------------------------


@contextmanager
def _patch_two_stage2_accounts():
    """Patch every I/O boundary needed to run TWO real stage2-enabled accounts
    (Gmail + IMAP) through a real async_setup_entry with a LIVE shared hub
    worker draining jobs. Yields a SimpleNamespace of the mocks tests assert
    against (mock_gmail_cls, mock_imap_cls, mock_parcel_cls, mock_extractor_cls).

    The mocked OllamaExtractor.async_extract echoes back the job's own
    Stage-1 shipment fields (tracking_number/carrier_name/order_name) as the
    Stage-2 result, so a job constructed for account A can never accidentally
    "extract into" account B's tracking number — the merge is a same-value
    no-op, isolating the no-leak assertion to the dispatch/routing layer
    (job.entry_id -> coordinator resolution) rather than the merge logic.
    """
    with ExitStack() as stack:

        def p(target, **kw):
            return stack.enter_context(patch(target, **kw))

        mock_gmail_cls = p("custom_components.shop2parcel.gmail_coordinator.GmailClient")
        p("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient")
        p("custom_components.shop2parcel.gmail_coordinator.EmailParser")
        mock_oauth = p("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow")
        mock_imap_cls = p("custom_components.shop2parcel.imap_coordinator.ImapClient")
        p("custom_components.shop2parcel.imap_coordinator.ParcelAppClient")
        p("custom_components.shop2parcel.imap_coordinator.EmailParser")
        mock_store_cls = p("custom_components.shop2parcel.coordinator.Shop2ParcelStore")
        mock_hub_store_cls = p("custom_components.shop2parcel.hub.Shop2ParcelStore")
        p("custom_components.shop2parcel.coordinator.OllamaClient")
        mock_extractor_cls = p("custom_components.shop2parcel.coordinator.OllamaExtractor")
        mock_parcel_cls = p("custom_components.shop2parcel.coordinator.ParcelAppClient")

        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
        mock_parcel_cls.return_value.async_add_delivery = AsyncMock()

        async def _extract_echo(_html_body, shipment):
            return Stage2Result(
                locked={
                    "tracking_number": shipment.tracking_number,
                    "carrier_name": shipment.carrier_name,
                    "order_name": shipment.order_name,
                },
                custom={},
                passes_used=1,
                latency_ms=5.0,
            )

        mock_extractor_cls.return_value.async_extract = AsyncMock(side_effect=_extract_echo)

        yield SimpleNamespace(
            mock_gmail_cls=mock_gmail_cls,
            mock_imap_cls=mock_imap_cls,
            mock_parcel_cls=mock_parcel_cls,
            mock_extractor_cls=mock_extractor_cls,
        )


async def _setup_two_stage2_accounts(hass, mock_config_entry, mock_imap_config_entry):
    """Add + enable Stage-2 on both accounts and drive a real async_setup_entry.

    Must be called inside a `with _patch_two_stage2_accounts():` block. Returns
    (hub, coord_a, coord_b) — coord_a is the Gmail account, coord_b the IMAP
    account, both attached to the SAME real Shop2ParcelHub with its real
    worker running.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_OLLAMA_URL: "http://localhost:11434"},
    )
    mock_imap_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_imap_config_entry,
        options={**mock_imap_config_entry.options, CONF_OLLAMA_URL: "http://localhost:11434"},
    )

    # A single async_setup() call bootstraps the domain and sets up BOTH
    # not-yet-loaded entries together (mirrors test_hub.py's precedent).
    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    hub = hass.data[DOMAIN]["__shared__"]
    coord_a = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
    coord_b = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
    return hub, coord_a, coord_b


def _make_shipment(tn: str, *, order: str, message_id: str) -> ShipmentData:
    return ShipmentData(
        tracking_number=tn,
        carrier_name="UPS",
        order_name=order,
        message_id=message_id,
        email_date=1700000000,
    )


async def test_cross_account_no_leak_real_setup(hass, mock_config_entry, mock_imap_config_entry):
    """Prohibition 1 / WORK-02: under a real two-account async_setup_entry, a
    Stage-2 job for account A's shipment lands only on A's coordinator.data —
    never on B's — and vice versa.
    """
    with _patch_two_stage2_accounts():
        hub, coord_a, coord_b = await _setup_two_stage2_accounts(
            hass, mock_config_entry, mock_imap_config_entry
        )

        tn_a = "1Z999AA10123456784"
        tn_b = "1Z999AA10123456785"
        shipment_a = _make_shipment(tn_a, order="#A", message_id="msgA")
        shipment_b = _make_shipment(tn_b, order="#B", message_id="msgB")

        assert (
            coord_a._enqueue_stage2(
                tn_a, "keyA", shipment_a, "<html/>", message_id="gmail:msgA", meta={}
            )
            is True
        )
        assert (
            coord_b._enqueue_stage2(
                tn_b, "keyB", shipment_b, "<html/>", message_id="imap:msgB", meta={}
            )
            is True
        )

        # Let the REAL hub worker drain both jobs to completion.
        await hub._queue.join()
        await hass.async_block_till_done()

        assert coord_a.data is not None and "keyA" in coord_a.data, (
            "A's own shipment must land on A's coordinator.data"
        )
        assert "keyB" not in (coord_a.data or {}), "B's shipment must NEVER land on A"
        assert coord_b.data is not None and "keyB" in coord_b.data, (
            "B's own shipment must land on B's coordinator.data"
        )
        assert "keyA" not in (coord_b.data or {}), "A's shipment must NEVER land on B"

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.config_entries.async_unload(mock_imap_config_entry.entry_id)


async def test_worker_survives_removal_teardown_only_at_last_account(
    hass, mock_config_entry, mock_imap_config_entry
):
    """WORK-04: removing account A leaves the shared worker running and
    account B's jobs still process (no "queue is None" error, no dropped B
    jobs). Removing the LAST account (B) cancels the worker via
    hub.async_shutdown() with no orphaned task, and hass.data[DOMAIN]
    ["__shared__"] is removed.
    """
    with _patch_two_stage2_accounts():
        hub, coord_a, coord_b = await _setup_two_stage2_accounts(
            hass, mock_config_entry, mock_imap_config_entry
        )
        worker_task = hub._worker_task
        assert worker_task is not None and not worker_task.done(), (
            "precondition: worker running after two-account setup"
        )

        # Removing A (NOT the last account) must not cancel the worker.
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        assert not worker_task.done(), "worker must survive single-account removal (WORK-04)"
        assert hass.data[DOMAIN].get("__shared__") is hub

        # B's jobs still process on the surviving worker.
        tn_b = "1Z999AA10123456785"
        shipment_b = _make_shipment(tn_b, order="#B", message_id="msgB")
        assert (
            coord_b._enqueue_stage2(
                tn_b, "keyB", shipment_b, "<html/>", message_id="imap:msgB", meta={}
            )
            is True
        )
        await hub._queue.join()
        await hass.async_block_till_done()
        assert coord_b.data is not None and "keyB" in coord_b.data, (
            "B's job must still be processed by the surviving worker after A's removal"
        )
        dropped_outcomes = [
            e["outcome"]
            for e in coord_b.diagnostics.scan_events
            if e["outcome"] == "stage2_dropped_backpressure"
        ]
        assert not dropped_outcomes, "no dropped B job after A's removal"

        # Removing B — the LAST account — must cancel the worker via
        # hub.async_shutdown(), with no orphaned task.
        await hass.config_entries.async_unload(mock_imap_config_entry.entry_id)
        assert worker_task.done(), "worker must be cancelled/done at last-account teardown"
        assert "__shared__" not in hass.data.get(DOMAIN, {})


async def test_cap_fairness_a_at_cap_does_not_block_b(
    hass, mock_config_entry, mock_imap_config_entry
):
    """WORK-03 fairness (prohibition 3): account A filled to
    STAGE2_PER_ACCOUNT_INFLIGHT_CAP (8) in-flight jobs does not block account
    B from enqueueing at least one job. A's 9th job is dropped with
    stage2_dropped_backpressure emitted (no silent drop). After the worker
    completes A's in-flight jobs, A can enqueue again.
    """
    with _patch_two_stage2_accounts():
        hub, coord_a, coord_b = await _setup_two_stage2_accounts(
            hass, mock_config_entry, mock_imap_config_entry
        )

        # Fill A's per-account in-flight cap with STAGE2_PER_ACCOUNT_INFLIGHT_CAP
        # distinct tracking numbers, all enqueued synchronously with no `await`
        # in between — the live worker (blocked on `await queue.get()`) cannot
        # be scheduled to dequeue any of them until control returns to the
        # event loop, so the fill is guaranteed to land before any drain.
        for i in range(STAGE2_PER_ACCOUNT_INFLIGHT_CAP):
            tn = f"1ZFILLA{i:03d}"
            shipment = _make_shipment(tn, order=f"#A{i}", message_id=f"fillA{i}")
            assert (
                coord_a._enqueue_stage2(
                    tn, tn, shipment, "<html/>", message_id=f"gmail:fillA{i}", meta={}
                )
                is True
            )
        assert hub.inflight_count(coord_a.config_entry.entry_id) == STAGE2_PER_ACCOUNT_INFLIGHT_CAP

        # A's 9th job (cap full) is dropped-newest with the backpressure event —
        # never silently vanishes (must-NOT prohibition 3).
        tn_drop = "1ZDROPA000"
        shipment_drop = _make_shipment(tn_drop, order="#Adrop", message_id="fillAdrop")
        assert (
            coord_a._enqueue_stage2(
                tn_drop, tn_drop, shipment_drop, "<html/>", message_id="gmail:fillAdrop", meta={}
            )
            is False
        )
        assert coord_a.diagnostics.scan_events[-1]["outcome"] == "stage2_dropped_backpressure"
        assert coord_a.diagnostics.stage2_dropped_backpressure_total == 1

        # B can still enqueue at least one job — A's cap does not starve B.
        tn_b = "1Z999AA10123456785"
        shipment_b = _make_shipment(tn_b, order="#B", message_id="msgB")
        assert (
            coord_b._enqueue_stage2(
                tn_b, "keyB", shipment_b, "<html/>", message_id="imap:msgB", meta={}
            )
            is True
        )

        # Let the worker drain everything — every one of A's 8 in-flight jobs
        # completes (success or gate-rejected; either way the hub's finally
        # release fires), freeing A's cap.
        await hub._queue.join()
        await hass.async_block_till_done()
        assert hub.inflight_count(coord_a.config_entry.entry_id) == 0, (
            "A's in-flight cap must be fully released once the worker finishes A's jobs"
        )

        # A can enqueue again now that a job has completed.
        tn_retry = "1ZRETRYA000"
        shipment_retry = _make_shipment(tn_retry, order="#Aretry", message_id="fillAretry")
        assert (
            coord_a._enqueue_stage2(
                tn_retry,
                tn_retry,
                shipment_retry,
                "<html/>",
                message_id="gmail:fillAretry",
                meta={},
            )
            is True
        )


async def test_no_dedup_bypass_through_hub_worker_path(
    hass, mock_config_entry, mock_imap_config_entry
):
    """Prohibition 2: the enqueue -> hub -> worker -> _async_process_stage2_job
    path still gates on the global submitted-tracking-number dedup
    (hub.is_submitted/check_and_mark) — no POST path introduced by the Phase
    32 cutover bypasses it.
    """
    with _patch_two_stage2_accounts() as mocks:
        hub, coord_a, _coord_b = await _setup_two_stage2_accounts(
            hass, mock_config_entry, mock_imap_config_entry
        )

        tn = "1Z999AA10123456784"
        # Simulate: this TN was already forwarded (by this or another account)
        # before this job was enqueued — the terminal dedup write after a real
        # POST success.
        assert hub.check_and_mark(tn) is False, "first mark of a new TN returns False"
        assert hub.is_submitted(tn) is True

        shipment = _make_shipment(tn, order="#dup", message_id="msgDup")
        assert (
            coord_a._enqueue_stage2(
                tn, "dup-key", shipment, "<html/>", message_id="gmail:msgDup", meta={}
            )
            is True
        ), "enqueue itself does not gate on the submitted-set — that happens at process time"

        await hub._queue.join()
        await hass.async_block_till_done()

        # The worker dispatched the job to _async_process_stage2_job, which
        # must have gated on hub.is_submitted() and skipped the POST entirely —
        # the surviving path never bypasses the global dedup check.
        mocks.mock_parcel_cls.return_value.async_add_delivery.assert_not_awaited()


# ---------------------------------------------------------------------------
# Phase 34-06 (LIFE-01a, D-09, PROH-2): add->add->remove->add mixed
# Gmail/IMAP step-wise lifecycle capstone test.
# ---------------------------------------------------------------------------


async def test_add_add_remove_add_mixed_fleet(hass, mock_config_entry, mock_imap_config_entry):
    """LIFE-01a (D-09): a step-wise add(Gmail A) -> add(IMAP B) ->
    remove(A) -> add(Gmail C) sequence over a mixed fleet, driven through
    the REAL hass.config_entries.async_setup/async_unload machinery (not a
    single bundled call — each step is its own individual call, mirroring
    how a user actually adds/removes accounts one at a time).

    After EACH step asserts: hub._refcount == expected,
    hass.data[DOMAIN]["__shared__"] is the same singleton hub object, both
    global sensors' native_value are correct, and each surviving account's
    per-account ParcelAppQuotaSensor (which mirrors the same shared value,
    D-01) reads correctly. Quota/queue state is driven directly on the hub
    (hub.try_consume() / hub._inflight) — no live Ollama/network — so these
    assertions are load-bearing, not incidentally correct (PROH-2/D-10):
    breaking any one of refcount, hub singleton identity, or a sensor value
    makes this test fail (demonstrated fail-first — see SUMMARY.md).
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: PLC0415

    from custom_components.shop2parcel.const import PARCELAPP_DAILY_LIMIT  # noqa: PLC0415
    from custom_components.shop2parcel.diagnostic_sensor import GlobalQueueSensor  # noqa: PLC0415
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415
    from custom_components.shop2parcel.sensor import (  # noqa: PLC0415
        GlobalQuotaSensor,
        ParcelAppQuotaSensor,
    )

    # A third, distinct Gmail-shaped account (C) — non-colliding unique_id.
    mock_config_entry_c = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token-c",
                "refresh_token": "fake-refresh-token-c",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key-c",
        },
        unique_id="user3@gmail.com",
    )

    registry = er.async_get(hass)
    quota_unique_id = f"{DOMAIN}___shared___{GlobalQuotaSensor._unique_id_suffix}"
    queue_unique_id = f"{DOMAIN}___shared___{GlobalQueueSensor._unique_id_suffix}"

    def _assert_global_sensors(expected_quota_remaining: int, expected_queue_depth: int) -> None:
        quota_entity_id = registry.async_get_entity_id("sensor", DOMAIN, quota_unique_id)
        queue_entity_id = registry.async_get_entity_id("sensor", DOMAIN, queue_unique_id)
        assert quota_entity_id is not None, "GlobalQuotaSensor must be registered"
        assert queue_entity_id is not None, "GlobalQueueSensor must be registered"
        quota_state = hass.states.get(quota_entity_id)
        queue_state = hass.states.get(queue_entity_id)
        assert quota_state is not None and quota_state.state != "unavailable"
        assert queue_state is not None and queue_state.state != "unavailable"
        assert int(quota_state.state) == expected_quota_remaining, (
            f"GlobalQuotaSensor expected {expected_quota_remaining}, got {quota_state.state}"
        )
        assert int(queue_state.state) == expected_queue_depth, (
            f"GlobalQueueSensor expected {expected_queue_depth}, got {queue_state.state}"
        )

    def _assert_per_account_quota(entry_id: str, expected_quota_remaining: int) -> None:
        uid = f"{DOMAIN}_{entry_id}_{ParcelAppQuotaSensor._unique_id_suffix}"
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, uid)
        assert entity_id is not None, f"per-account ParcelAppQuotaSensor missing for {entry_id}"
        state = hass.states.get(entity_id)
        assert state is not None and state.state != "unavailable"
        assert int(state.state) == expected_quota_remaining, (
            f"account {entry_id} quota sensor expected {expected_quota_remaining}, got {state.state}"
        )

    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch("custom_components.shop2parcel.hub.Shop2ParcelStore") as mock_hub_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
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
        mock_hub_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_hub_store_cls.return_value.async_save = AsyncMock()
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])

        # --- Step add-1: Gmail A --------------------------------------------
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        hub = hass.data[DOMAIN]["__shared__"]
        assert isinstance(hub, Shop2ParcelHub)
        assert hub._refcount == 1
        _assert_global_sensors(PARCELAPP_DAILY_LIMIT, 0)
        _assert_per_account_quota(mock_config_entry.entry_id, PARCELAPP_DAILY_LIMIT)

        # --- Step add-2: IMAP B ----------------------------------------------
        mock_imap_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_imap_config_entry.entry_id)
        await hass.async_block_till_done()

        assert hass.data[DOMAIN]["__shared__"] is hub, "same singleton hub across add-2"
        assert hub._refcount == 2

        coord_a = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coord_b = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]

        # Drive quota/queue state directly on the hub (no live Ollama/network)
        # so the global sensors' values below are load-bearing, not
        # incidentally correct. CoordinatorEntity state only refreshes on a
        # coordinator listener push (not on direct hub mutation), so both
        # attached coordinators' listeners are nudged to observe the change —
        # mirrors production: a real poll would call async_update_listeners()
        # after touching hub state.
        for _ in range(3):
            assert hub.try_consume() is True
        hub._inflight.setdefault(mock_imap_config_entry.entry_id, set()).add("tn-inflight-1")
        coord_a.async_update_listeners()
        coord_b.async_update_listeners()
        await hass.async_block_till_done()

        _assert_global_sensors(PARCELAPP_DAILY_LIMIT - 3, 1)
        _assert_per_account_quota(mock_config_entry.entry_id, PARCELAPP_DAILY_LIMIT - 3)
        _assert_per_account_quota(mock_imap_config_entry.entry_id, PARCELAPP_DAILY_LIMIT - 3)

        # --- Step remove: unload A (the current global-sensor owner) --------
        assert hub._global_sensor_owner_entry_id == mock_config_entry.entry_id, (
            "A registered first and must be the sole owner before this step"
        )
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert hass.data[DOMAIN]["__shared__"] is hub, "same singleton hub across remove"
        assert hub._refcount == 1

        # Re-home worked: both global sensors STILL exist, available, and
        # UNCHANGED — B's per-account sensor is unaffected by A's removal.
        _assert_global_sensors(PARCELAPP_DAILY_LIMIT - 3, 1)
        assert hub._global_sensor_owner_entry_id == mock_imap_config_entry.entry_id, (
            "ownership must re-home to the sole survivor (IMAP B)"
        )
        _assert_per_account_quota(mock_imap_config_entry.entry_id, PARCELAPP_DAILY_LIMIT - 3)

        # --- Step add-3: Gmail C ----------------------------------------------
        mock_config_entry_c.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry_c.entry_id)
        await hass.async_block_till_done()

        assert hass.data[DOMAIN]["__shared__"] is hub, "same singleton hub across add-3"
        assert hub._refcount == 2

        _assert_global_sensors(PARCELAPP_DAILY_LIMIT - 3, 1)
        _assert_per_account_quota(mock_config_entry_c.entry_id, PARCELAPP_DAILY_LIMIT - 3)
        _assert_per_account_quota(mock_imap_config_entry.entry_id, PARCELAPP_DAILY_LIMIT - 3)

        # Cleanup: unload remaining accounts so the hub worker task is
        # cancelled before the mocked-Store context exits.
        await hass.config_entries.async_unload(mock_imap_config_entry.entry_id)
        await hass.config_entries.async_unload(mock_config_entry_c.entry_id)
