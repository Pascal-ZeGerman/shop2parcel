"""Custom exception taxonomy for Shop2Parcel API clients.

No HA imports. The coordinator (Phase 4) is the only layer
that translates these to ConfigEntryAuthFailed / UpdateFailed.
"""

from __future__ import annotations


class GmailAuthError(Exception):
    """OAuth2 token expired or revoked — coordinator raises ConfigEntryAuthFailed."""


class GmailTransientError(Exception):
    """Network failure or Gmail 5xx — coordinator raises UpdateFailed, retries next poll."""


class ParcelAppAuthError(Exception):
    """Invalid api-key — coordinator raises ConfigEntryAuthFailed."""


class ParcelAppQuotaError(Exception):
    """HTTP 429 — 20/day add-delivery quota exhausted.

    reset_at is None unless the API provides a timestamp in the 429 body.
    Coordinator uses this to skip forwarding attempts for the rest of the day.
    """

    def __init__(self, message: str, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class ParcelAppTransientError(Exception):
    """Network failure or parcelapp 5xx — coordinator logs and retries next poll."""


class ParcelAppInvalidTrackingError(Exception):
    """HTTP 400 — bad tracking number or carrier code.

    Coordinator logs and skips. Does NOT count as a forwarding success.
    NOTE: Still consumes one of the 20/day quota slots — minimize these.
    """


class ParcelAppAlreadyAddedError(Exception):
    """HTTP 400 with error_message 'You have already added this delivery to the app'.

    Coordinator treats this as an idempotent success: the tracking number is already
    in parcelapp.net and is written to the dedup store to permanently suppress retries.
    NOT a subclass of ParcelAppInvalidTrackingError (D-01).
    """


class ImapAuthError(Exception):
    """IMAP login failure (bad credentials, account locked) — coordinator raises ConfigEntryAuthFailed."""


class ImapTransientError(Exception):
    """IMAP connection failure, timeout, socket error — coordinator raises UpdateFailed."""


class OllamaTransientError(Exception):
    """Network failure or Ollama 5xx — coordinator/extractor logs and retries next poll.

    Phase 15 OLLM-04: raised by OllamaClient on connection errors, timeouts, and
    HTTP >= 500 responses from the local Ollama /api/generate endpoint.
    """


class OllamaSchemaError(Exception):
    """Ollama response cannot be parsed as JSON object after normalize + fence-strip fallback.

    Phase 15 OLLM-05/OLLM-06: raised by normalize_llm_payload when no `{` or `}` is
    present in the LLM output (D-06), and by OllamaClient.async_generate when the
    second-pass (fence-strip) json.loads also fails (D-04).

    Security (D-07/D-08/D-09): error message must NEVER include the raw response —
    only its length is a safe diagnostic.
    """
