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
