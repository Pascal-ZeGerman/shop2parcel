"""parcelapp.net external API client.

Implements the official add-delivery and view-deliveries endpoints.
See: .planning/parcelapp-api.md for full API documentation.

Auth: api-key header (lowercase, hyphenated — NOT Authorization: Bearer).
Rate limits: add-delivery 20/day (ALL-IN including failures), view-deliveries 20/hour.

session must be the HA shared session (injected via Phase 4 coordinator).
Never create a new aiohttp.ClientSession inside this class.

No HA imports (D-01/D-03).
"""

from __future__ import annotations

import aiohttp

from .exceptions import (
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)

ADD_DELIVERY_URL = "https://api.parcel.app/external/add-delivery/"
VIEW_DELIVERIES_URL = "https://api.parcel.app/external/deliveries/"


class ParcelAppClient:
    """Async client for parcelapp.net external API.

    session: injected aiohttp.ClientSession (Phase 4 passes the shared HA session).
    api_key: stored at construction time; passed as api-key header, never in URL.

    EMAIL-05: This client does not schedule itself. Poll interval (default 30 min)
    is configured in Phase 4's DataUpdateCoordinator via update_interval.
    """

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key

    async def async_add_delivery(
        self,
        tracking_number: str,
        carrier_code: str,
        description: str,
    ) -> None:
        """POST a new delivery to parcelapp.net. Raises on all error conditions.

        carrier_code must already be normalized via carrier_codes.normalize_carrier().
        Using an invalid carrier_code returns HTTP 400 and still consumes one quota slot.
        send_push_confirmation is always False for automated submissions.

        Security: api_key is passed as header only — never in URL params, never logged.
        """
        headers = {"api-key": self._api_key}
        body = {
            "tracking_number": tracking_number,
            "carrier_code": carrier_code,
            "description": description,
            "send_push_confirmation": False,
        }
        try:
            async with self._session.post(
                ADD_DELIVERY_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (401, 403):
                    raise ParcelAppAuthError(f"Auth failed: HTTP {resp.status}")
                if resp.status == 429:
                    reset_at: int | None = None
                    try:
                        data = await resp.json(content_type=None)
                        reset_at = data.get("reset_at")
                    except ValueError, aiohttp.ContentTypeError:
                        # Non-JSON or wrong content-type body — reset_at stays None.
                        pass
                    raise ParcelAppQuotaError("Daily quota (20/day) exhausted", reset_at=reset_at)
                if resp.status == 400:
                    try:
                        data = await resp.json(content_type=None)
                        msg = data.get("error_message", "Bad request")
                    except ValueError, aiohttp.ContentTypeError:
                        # Non-JSON body; do not swallow unexpected exceptions such as
                        # ServerDisconnectedError which would mis-classify a transient
                        # network failure as a permanent bad-tracking error.
                        msg = "Bad request (non-JSON body)"
                    raise ParcelAppInvalidTrackingError(msg)
                if resp.status >= 500:
                    raise ParcelAppTransientError(f"Server error: HTTP {resp.status}")
                if 400 <= resp.status < 500:
                    raise ParcelAppTransientError(f"Unexpected client error: HTTP {resp.status}")
                resp.raise_for_status()
        except (
            TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ServerTimeoutError,
        ) as err:
            raise ParcelAppTransientError(f"Network error: {err}") from err

    async def async_get_deliveries(self, filter_mode: str = "recent") -> list[dict]:
        """GET current deliveries from parcelapp.net.

        Used by Phase 4 coordinator for deduplication at startup.
        Rate limit: 20/hour (separate from add-delivery quota).

        filter_mode: "recent" (default) or "active"
        """
        headers = {"api-key": self._api_key}
        try:
            async with self._session.get(
                VIEW_DELIVERIES_URL,
                headers=headers,
                params={"filter_mode": filter_mode},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (401, 403):
                    raise ParcelAppAuthError(f"Auth failed: HTTP {resp.status}")
                if resp.status == 429:
                    raise ParcelAppTransientError("View-deliveries rate limit (20/hr) exceeded")
                if resp.status >= 500:
                    raise ParcelAppTransientError(f"Server error: HTTP {resp.status}")
                if 400 <= resp.status < 500:
                    raise ParcelAppTransientError(f"Unexpected client error: HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return data.get("deliveries", [])
        except (
            TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ServerDisconnectedError,
            aiohttp.ServerTimeoutError,
        ) as err:
            raise ParcelAppTransientError(f"Network error: {err}") from err
