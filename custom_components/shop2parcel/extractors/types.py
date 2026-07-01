"""Stage 2 result types — shared across extractor, merge, queue, sensor.

Frozen dataclass to prevent in-flight mutation. Lives in this standalone
module so Phases 18/20/21 can import without circular dependencies on
the OllamaExtractor class itself.

The locked/custom split mirrors Phase 21's sensor surfacing: ``locked``
holds the four locked fields (``tracking_number``, ``carrier_name``,
``order_name``, ``order_summary``). The first three are POSTed to
parcelapp.net; ``order_summary`` is the description source only and is
never POSTed. ``custom`` holds user-extensible fields that surface as
``extra_state_attributes`` on the Stage-2 sensor — never POSTed.

No HA imports (D-01/D-03 extended to extractors/).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stage2Result:
    """Structured output from OllamaExtractor.async_extract (D-04).

    locked: the 4 locked fields keyed by LOCKED_OLLAMA_FIELDS —
        ``tracking_number``, ``carrier_name``, ``order_name``,
        ``order_summary``. The first three are POSTed to parcelapp.net;
        ``order_summary`` is the description source only — never POSTed.
        Values are strings or ``None`` (D-05: ``None`` is the canonical
        "model declined to extract" signal — empty string is coerced to
        ``None`` upstream).
    custom: user-extensible fields keyed by the names provided in
        Phase-17's options-flow textarea. Same string-or-None value
        contract. Surfaced as sensor attributes by Phase 21, not POSTed.
    passes_used: number of parse passes the underlying OllamaClient
        consumed (1 = clean first-pass parse; 2 = markdown-fence retry).
        Fed into Phase-21 diagnostics.
    latency_ms: round-trip time of the /api/generate call in
        milliseconds. Fed into Phase-21's rolling-average latency sensor.

    frozen=True forbids attribute reassignment. Callers must treat the
    ``locked`` and ``custom`` dicts as read-only as well — Python does
    NOT prevent in-place mutation of mutable fields on a frozen
    dataclass, so the per-phase consumers (18/20/21) own that
    discipline (T-16.01-01).
    """

    locked: dict[str, str | None]
    custom: dict[str, str | None]
    passes_used: int
    latency_ms: float
