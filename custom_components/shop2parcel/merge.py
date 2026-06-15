"""Pure merge module for Stage-2 LLM output — no HA imports (D-02).

Maps to Phase 20 requirements:

- **MRG-02**: The caller (``coordinator._async_process_stage2_job``) replaces
  ``job.shipment`` with the returned merged ``ShipmentData`` before POSTing to
  parcelapp.net.

- **MRG-03**: Per-field conflict guard — LLM value overwrites Stage-1 ONLY if
  Stage-1 is ``None`` OR values match after ``str.strip().upper()`` normalization
  (mirrors ``normalize_tracking_number()`` in ``const.py``). Any divergence keeps
  Stage-1 and records a conflict entry in the returned ``conflicts`` list.

- **MRG-04**: Before promoting a Stage-2-sourced ``tracking_number`` (Stage-1 is
  ``None``), validate it against ``_SANITY_RE`` (non-empty, 6–40 chars,
  alphanumeric/dash/space). Fails → treat as ``None``; no conflict emitted.
  The sanity gate does NOT apply to the conflict path (Stage-1 non-``None``).

The function returns a ``tuple[ShipmentData, list[dict]]`` so the caller (the
coordinator, which holds ``self.hass``) can emit the ``stage2_conflict`` activity
event via ``_emit_scan_event`` — keeping this module HA-free per **D-02** and
CONTEXT.md decisions **D-03 / D-04 / D-05**.

``Stage2Result.custom`` is never read or written here; custom-field surfacing is
deferred to Phase 21.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .api.email_parser import ShipmentData
from .const import LOCKED_OLLAMA_FIELDS
from .extractors.types import Stage2Result

# MRG-04: loose sanity regex for Stage-2-sourced tracking numbers.
# Accepts 6–40 characters of alphanumeric, hyphen, or space.
# Applied ONLY when Stage-1 tracking_number is None (promotion path).
# The conflict path (Stage-1 non-None) is exempt — a short or invalid
# Stage-2 value is still a conflict, not silently discarded.
_SANITY_RE = re.compile(r"^[A-Za-z0-9\- ]{6,40}$")


def merge_llm_authoritative(
    stage1: ShipmentData,
    result: Stage2Result,
) -> tuple[ShipmentData, list[dict]]:
    """Merge Stage-2 LLM result into Stage-1 ShipmentData.

    Iterates ``LOCKED_OLLAMA_FIELDS`` (``tracking_number``, ``carrier_name``,
    ``order_name``). For each field, applies **MRG-03** routing:

    1. **Stage-1 is None** — accept Stage-2 value (after **MRG-04** sanity gate
       for ``tracking_number``; value may still be ``None`` if Stage-2 also
       declined or failed sanity).
    2. **Stage-2 is None** — keep Stage-1 value (Stage-2 declined to extract).
    3. **``str.strip().upper()`` match** — keep Stage-1 canonical casing; no
       conflict.  Normalization mirrors ``normalize_tracking_number()`` in
       ``const.py`` (``str.strip().upper()``).
    4. **Mismatch** — keep Stage-1, append
       ``{"field": ..., "stage1": ..., "stage2": ...}`` to ``conflicts``.

    **MRG-04 sanity gate**: applied ONLY to ``tracking_number`` on the Stage-1
    ``None`` promotion path.  Regex ``^[A-Za-z0-9\\- ]{6,40}$`` (see
    ``_SANITY_RE``).  Invalid values are silently discarded (treated as
    ``None``); no conflict entry is emitted.

    Returns:
        A ``(merged, conflicts)`` tuple where:

        - ``merged`` is a **new** ``ShipmentData`` produced via
          ``dataclasses.replace``; ``stage1`` is never mutated.
          ``message_id`` and ``email_date`` are preserved from ``stage1``.
        - ``conflicts`` is a ``list[dict]`` (may be empty).  The caller emits
          exactly one ``stage2_conflict`` activity event per job when
          ``len(conflicts) > 0``, via
          ``_emit_scan_event(outcome="stage2_conflict", extra={"conflicts": conflicts})``
          (CONTEXT.md **D-04 / D-05**).

    ``Stage2Result.custom`` is never read or written here (out of scope for
    Phase 20; custom-field surfacing is Phase 21).
    """
    overrides: dict[str, str | None] = {}
    conflicts: list[dict] = []

    for field_name in LOCKED_OLLAMA_FIELDS:
        s1_val: str | None = getattr(stage1, field_name, None)
        s2_val: str | None = result.locked.get(field_name)

        # MRG-04: validate Stage-2 tracking_number before promoting when Stage-1 is None.
        if field_name == "tracking_number" and s1_val is None and s2_val is not None:
            if not _SANITY_RE.match(s2_val):
                s2_val = None  # silent discard; no conflict entry

        if s1_val is None:
            # Stage-2 wins (promotion path); value may be None if Stage-2 declined
            # or if MRG-04 discarded it above.
            overrides[field_name] = s2_val
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
    # overrides contains only str | None values for the three locked string fields.
    # mypy cannot resolve the **dict[str, str | None] spread against ShipmentData's
    # typed keyword-only replace() overload — the runtime values are always valid.
    merged: ShipmentData = replace(stage1, **overrides)  # type: ignore[arg-type]
    return merged, conflicts
