"""Pure merge module for Stage-2 LLM output — no HA imports (D-02).

Maps to Phase 20 requirements:

- **MRG-02**: The caller (``coordinator._async_process_stage2_job``) replaces
  ``job.shipment`` with the returned merged ``ShipmentData`` before POSTing to
  parcelapp.net.

- **MRG-03**: Per-field conflict guard — LLM value overwrites Stage-1 ONLY if
  Stage-1 is missing (``None``, ``""``/whitespace, or the ``"Unknown"`` carrier
  sentinel — WR-02) OR values match after ``str.strip().upper()`` normalization.
  Any divergence between two REAL values keeps Stage-1 and records a conflict
  entry in the returned ``conflicts`` list.

- **MRG-04** (Phase 28 Plan 03): Before promoting a Stage-2-sourced ``tracking_number``
  (Stage-1 is ``None``), validate it against the strict carrier-format gate
  ``validate_carrier_format`` from ``api/email_parser.py``.  Fails → treat as
  ``None``; no conflict emitted; a gate-rejection entry is appended to the returned
  ``gate_rejections`` list for the HA-holding caller to count (D-02 seam — counter
  increment stays in coordinator.py, NOT here).  On pass, ``s2_val`` is replaced
  with the CLEANED canonical form (separator-stripped, uppercased) per D-03.
  The strict gate does NOT apply to the conflict path (Stage-1 non-``None``).

The function returns a ``tuple[ShipmentData, list[dict], list[dict]]`` so the
caller (the coordinator, which holds ``self.hass``) can:

  1. Emit the ``stage2_conflict`` activity event via ``_emit_scan_event``.
  2. Increment ``PollStats.carrier_format_rejected_total`` for each gate rejection.

This keeps the module HA-free per **D-02** and CONTEXT.md decisions
**D-03 / D-04 / D-05**.

``Stage2Result.custom`` is propagated unconditionally into ``ShipmentData.custom_attributes``
and surfaced as HA sensor attributes (FLD-03). Custom fields are never POSTed to parcelapp.net.

All four Ollama-derived POST paths now go through the strict carrier-format gate
(``validate_carrier_format``).  Phase 28 Plan 04 completed the fourth path —
the ``gmail_coordinator.py`` Stage-1-miss inline fallback.
"""

from __future__ import annotations

from dataclasses import replace

from .api.email_parser import ShipmentData, validate_carrier_format
from .const import LOCKED_OLLAMA_FIELDS
from .extractors.types import Stage2Result


def _stage1_missing(field_name: str, value: str | None) -> bool:
    """True when a Stage-1 value is "unknown" and eligible for Stage-2 promotion.

    WR-02 (MRG-03/MRG-04 intent): Stage-1 emits placeholder sentinels rather than
    ``None`` for missing fields — ``""`` for ``order_name`` (and any blank field)
    and ``"Unknown"`` for ``carrier_name`` (see ``api/email_parser.py``). Treating
    those sentinels as authoritative made the promotion path dead code for 3 of
    the 4 locked fields: the LLM could never fill a blank ``order_name`` or
    replace ``"Unknown"`` (which ``normalize_carrier`` maps to ``"pholder"``),
    and every such job emitted a spurious ``stage2_conflict`` event.
    """
    if value is None or not value.strip():
        return True
    return field_name == "carrier_name" and value.strip().upper() == "UNKNOWN"


def merge_llm_authoritative(
    stage1: ShipmentData,
    result: Stage2Result,
) -> tuple[ShipmentData, list[dict], list[dict]]:
    """Merge Stage-2 LLM result into Stage-1 ShipmentData.

    Iterates ``LOCKED_OLLAMA_FIELDS`` (``tracking_number``, ``carrier_name``,
    ``order_name``, ``order_summary``). For each field, applies **MRG-03** routing:

    1. **Stage-1 is missing** (``None``, ``""``/whitespace, or the ``"Unknown"``
       carrier sentinel — see ``_stage1_missing``, WR-02) — accept Stage-2 value
       (after **MRG-04** strict carrier-format gate for ``tracking_number``); when
       Stage-2 also declined, the original Stage-1 value (sentinel or ``None``) is
       kept so non-Optional ``str`` fields are never downgraded to ``None``.
    2. **Stage-2 is None** — keep Stage-1 value (Stage-2 declined to extract).
    3. **``str.strip().upper()`` match** — keep Stage-1 canonical casing; no
       conflict.  Normalization mirrors ``normalize_tracking_number()`` in
       ``const.py`` (``str.strip().upper()``).
    4. **Mismatch** — keep Stage-1, append
       ``{"field": ..., "stage1": ..., "stage2": ...}`` to ``conflicts``.

    **MRG-04 strict carrier-format gate** (Phase 28 Plan 03): applied ONLY to
    ``tracking_number`` on the Stage-1 ``None`` promotion path.  Calls
    ``validate_carrier_format`` from ``api/email_parser.py``.  On pass, ``s2_val``
    is replaced with the CLEAN canonical form (separator-stripped, uppercased) per
    D-03.  On fail, value is silently discarded (treated as ``None``); no conflict
    entry is emitted.  A gate-rejection entry is appended to ``gate_rejections``
    so the HA-holding caller can count it (D-02 seam — the counter increment stays
    in ``coordinator.py``).  The strict gate does NOT apply to the conflict path
    (Stage-1 non-``None``).

    Returns:
        A ``(merged, conflicts, gate_rejections)`` tuple where:

        - ``merged`` is a **new** ``ShipmentData`` produced via
          ``dataclasses.replace``; ``stage1`` is never mutated.
          ``message_id`` and ``email_date`` are preserved from ``stage1``.
        - ``conflicts`` is a ``list[dict]`` (may be empty).  The caller emits
          exactly one ``stage2_conflict`` activity event per job when
          ``len(conflicts) > 0``, via
          ``_emit_scan_event(outcome="stage2_conflict", extra={"conflicts": conflicts})``
          (CONTEXT.md **D-04 / D-05**).
        - ``gate_rejections`` is a ``list[dict]`` of carrier-format rejections on
          the MRG-04 promotion path.  Each entry is
          ``{"field": str, "clean": str, "reason": str}``.  The HA-holding caller
          calls ``record_carrier_format_rejection(entry["clean"], entry["reason"])``
          for each entry (D-02 seam).  Empty when no MRG-04 gate fired.

    ``Stage2Result.custom`` is unconditionally propagated into the merged
    ``ShipmentData.custom_attributes`` (FLD-03) — user-added fields surface as
    HA sensor attributes and are never POSTed to parcelapp.net.
    """
    overrides: dict[str, str | None] = {}
    conflicts: list[dict] = []
    gate_rejections: list[dict] = []

    for field_name in LOCKED_OLLAMA_FIELDS:
        s1_raw: str | None = getattr(stage1, field_name, None)
        # WR-02: placeholder sentinels ("", whitespace-only, carrier "Unknown")
        # route to the promotion path exactly like None — conflicts only fire when
        # Stage-1 had a REAL value that differs.
        s1_val: str | None = None if _stage1_missing(field_name, s1_raw) else s1_raw
        s2_val: str | None = result.locked.get(field_name)

        # MRG-04: apply strict carrier-format gate to tracking_number on the Stage-1-None
        # promotion path.  The conflict path (s1_val non-None) is exempt — a carrier-format-
        # invalid Stage-2 value is still a conflict against Stage-1, not silently discarded.
        if field_name == "tracking_number" and s1_val is None and s2_val is not None:
            clean, ok, reason = validate_carrier_format(s2_val)
            if ok:
                # Gate passed: use the CLEAN canonical form (D-03).
                s2_val = clean
            else:
                # Gate failed: silent discard (no conflict entry); signal to caller via
                # gate_rejections (D-02 seam — counter increment stays in coordinator.py).
                gate_rejections.append(
                    {"field": field_name, "clean": clean, "reason": reason or "no_carrier_match"}
                )
                s2_val = None

        if s1_val is None:
            # Stage-2 wins (promotion path). When Stage-2 also declined (or MRG-04
            # discarded its value), keep the ORIGINAL Stage-1 value — never downgrade
            # a "" / "Unknown" sentinel to None, since downstream consumers type
            # these fields as non-Optional str (WR-02).
            overrides[field_name] = s2_val if s2_val is not None else s1_raw
        elif s2_val is None:
            # Stage-2 declined to extract; keep Stage-1 value.
            overrides[field_name] = s1_val
        elif s1_val.strip().upper() == s2_val.strip().upper():
            # Normalized match — preserve Stage-1 canonical casing.
            overrides[field_name] = s1_val
        else:
            # Conflict: Stage-1 wins; record for caller to emit stage2_conflict event.
            overrides[field_name] = s1_val
            conflicts.append({"field": field_name, "stage1": s1_val, "stage2": s2_val})

    # dataclasses.replace() produces a new ShipmentData; email_date (int) and
    # message_id (str) pass through unchanged from stage1.
    # overrides contains str | None values for all four locked fields.
    # mypy cannot resolve the **dict[str, str | None] spread against ShipmentData's
    # typed keyword-only replace() overload — the runtime values are always valid.
    merged: ShipmentData = replace(stage1, **overrides)  # type: ignore[arg-type]
    # FLD-03 / D-12: unconditionally propagate Stage2Result.custom into custom_attributes.
    # Stage2Result.custom is always a dict per Phase 16 extractor contract — empty-over-empty is harmless.
    merged = replace(merged, custom_attributes=result.custom)
    return merged, conflicts, gate_rejections
