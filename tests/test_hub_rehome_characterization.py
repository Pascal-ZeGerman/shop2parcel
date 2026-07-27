"""Phase 34 Plan 01: Wave-0 R-01 characterization test + Phase 32 preflight.

These two tests de-risk Phase 34 BEFORE any re-home/notification code is
built on top of them (RESEARCH.md Open Question #1 / Assumption A1, and
Pitfall 5's dependency-ordering concern):

1. ``test_entity_registry_reparents_config_entry_id_on_cross_entry_reregister``
   proves the load-bearing R-01 mechanism the later 34-05 re-home plan
   depends on: HA's EntityRegistry re-parents ``config_entry_id`` when the
   SAME ``(domain, platform, unique_id)`` triple is re-registered from a
   DIFFERENT config entry's ``async_add_entities``. If this fails on the
   pinned HA version, 34-05's approach must be revisited BEFORE it is built.

2. ``test_phase32_hub_inflight_and_worker_present`` is a safety-net gate
   (D-01): Phase 34 CONSUMES Phase 32's shared ``hub._inflight`` dict and
   real (non-stub) worker/enqueue surface. If either is absent at execute
   time, Phase 32 is not actually merged into this branch and the executor
   must stop loudly rather than hand-roll a shadow queue downstream.

No live network / Ollama in either test.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.shop2parcel.const import DOMAIN
from custom_components.shop2parcel.hub import Shop2ParcelHub


async def test_entity_registry_reparents_config_entry_id_on_cross_entry_reregister(
    hass, mock_config_entry, mock_imap_config_entry
):
    """R-01 / Assumption A1 spike: cross-entry re-registration re-parents.

    Registers the SAME (domain, platform, unique_id) triple twice — once
    under a Gmail-shaped MockConfigEntry (entry_a), once under an
    IMAP-shaped MockConfigEntry (entry_b) — via two separate
    ``async_get_or_create`` calls (mirroring what two distinct platforms'
    ``async_add_entities`` would do). This proves the entity_id is stable
    across the re-register AND that ``config_entry_id`` flips to the new
    owner — not merely that a second, distinct entity was created.
    """
    mock_config_entry.add_to_hass(hass)
    mock_imap_config_entry.add_to_hass(hass)

    registry = er.async_get(hass)

    first = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="s2p_rehome_probe",
        config_entry=mock_config_entry,
    )
    assert first.config_entry_id == mock_config_entry.entry_id, (
        "Pre-condition: entity must be owned by entry_a immediately after first registration"
    )

    second = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="s2p_rehome_probe",
        config_entry=mock_imap_config_entry,
    )

    # The load-bearing R-01 assertion: same entity_id, re-parented owner.
    assert second.entity_id == first.entity_id, (
        "Re-registering the same (domain, platform, unique_id) from a different "
        "config entry must return the SAME entity_id (A1) — if this fails, the "
        "34-05 re-home mechanism's core assumption does not hold for the pinned "
        "HA version and must be revisited before that plan is built."
    )
    assert registry.async_get(first.entity_id).config_entry_id == mock_imap_config_entry.entry_id, (
        "config_entry_id must flip to entry_b's entry_id after cross-entry "
        "re-registration — a second distinct entity being created instead is "
        "NOT sufficient evidence for R-01."
    )


async def test_phase32_hub_inflight_and_worker_present(hass):
    """D-01 safety-net gate: Phase 32's _inflight dict + real worker surface
    must already be merged before Phase 34 is executed.

    Phase 34 CONSUMES Phase 32's shared in-flight tracking and worker/enqueue
    plumbing — it must never reimplement enqueue/backpressure logic itself
    (only thin read accessors are permitted downstream, per RESEARCH.md
    Pitfall 5). Execution order is 31 (quota) -> 32 (shared queue+worker) ->
    33 (IMAP parity) -> 34 (this phase). If this test fails, Phase 32 is not
    actually present on this branch and the executor must stop loudly here
    rather than hand-roll a shadow queue in a later Phase 34 plan.
    """
    hub = Shop2ParcelHub(hass)

    assert hasattr(hub, "_inflight"), (
        "Shop2ParcelHub must expose _inflight (Phase 32 WORK-01..04) — "
        "31 -> 32 -> 33 -> 34 dependency order violated if absent"
    )
    assert isinstance(hub._inflight, dict), "_inflight must be a dict (entry_id -> set[tn])"

    # Phase 32-05 fully deleted the Phase 29 _stub_worker/async_start_stage2
    # stub path (D-04) — its absence is itself evidence the real worker
    # replaced it, not a gap.
    assert not hasattr(hub, "_stub_worker"), (
        "Phase 32-05 (D-04) deleted the stub worker entirely — its presence "
        "here would mean an unexpectedly older hub.py is on this branch"
    )
    assert hasattr(hub, "enqueue") and callable(hub.enqueue), (
        "Shop2ParcelHub.enqueue() (Phase 32 WORK-01..04) is the real "
        "worker/enqueue surface Phase 34 reads from — 31 -> 32 -> 33 -> 34 "
        "dependency order violated if absent"
    )
