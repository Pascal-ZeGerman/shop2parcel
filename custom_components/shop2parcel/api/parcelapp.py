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

import logging
import re
import time

import aiohttp

from .exceptions import (
    ParcelAppAlreadyAddedError,
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)

_LOGGER = logging.getLogger(__name__)

ADD_DELIVERY_URL = "https://api.parcel.app/external/add-delivery/"
VIEW_DELIVERIES_URL = "https://api.parcel.app/external/deliveries/"

_ALREADY_ADDED_MSG = "You have already added this delivery to the app"
_NO_JSON = object()  # Sentinel: body could not be decoded as JSON.

# WR-09: description sanitization at the POST boundary. The description can be
# LLM-generated (order_summary) from attacker-controlled email content — the
# only field of the four POSTed that had no validation gate. Cap length and
# strip control characters here so EVERY POST path (inline Stage-1, Stage-2
# worker, deferred drain) is covered by the single choke point.
MAX_DESCRIPTION_CHARS = 200
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# WR-05/WR-07: the 429 body's reset_at is untrusted external input. A non-int
# value TypeError'd every subsequent quota comparison until restart; an absurd
# far-future int silently paused forwarding indefinitely AND persisted across
# restarts. Clamp the pause to at most 24 hours from now.
MAX_RESET_AT_WINDOW_S = 86400  # 24 hours


def _validate_reset_at(raw: object) -> int | None:
    """Validate and clamp an untrusted reset_at value from the 429 body (WR-05/WR-07).

    Accepts ints and int-parseable strings (bool excluded — it is an int subclass).
    Returns an epoch int clamped to at most MAX_RESET_AT_WINDOW_S in the future,
    or None for invalid, past, or absent values — callers treat None as "use the
    default quota window" (the coordinators fall back to _next_midnight_utc()).
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except ValueError:
            return None
    else:
        return None  # None, float, list, dict, ... — not an epoch int
    now = int(time.time())
    if value <= now:
        return None
    return min(value, now + MAX_RESET_AT_WINDOW_S)


async def _read_error_body(resp: aiohttp.ClientResponse) -> object:
    """Decode the response body for error extraction.

    Returns the parsed JSON value on success, or the _NO_JSON sentinel when the
    body is not valid JSON (parse error or wrong content-type).

    Privacy: raw body is never logged — callers only log resp.status.
    """
    try:
        return await resp.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):  # fmt: skip
        return _NO_JSON


def _raise_from_error_body(data: object) -> None:
    """Resolve error_message from a decoded body and raise the appropriate error.

    Both the 400 branch and the new 2xx success:false branch call this helper so
    there is a single source of truth for message extraction and exception routing.

    Raises ParcelAppAlreadyAddedError for the documented already-added message;
    raises ParcelAppInvalidTrackingError for all other cases (including malformed bodies).

    Privacy: data is never logged — callers must preserve status-only logging.
    """
    if data is _NO_JSON:
        raise ParcelAppInvalidTrackingError("Bad request (non-JSON body)")
    msg = "Bad request"
    if isinstance(data, dict):
        msg_value = data.get("error_message")
        if isinstance(msg_value, str) and msg_value.strip():
            msg = msg_value
        # else: keep default — covers None, non-string, empty/whitespace string
    elif data is not None:
        # Parsed but not a dict (e.g. a JSON list)
        msg = "Bad request (unexpected JSON shape)"
    if msg == _ALREADY_ADDED_MSG:
        raise ParcelAppAlreadyAddedError(msg)
    raise ParcelAppInvalidTrackingError(msg)


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
        WR-09: description is sanitized here (control chars stripped, capped at
        MAX_DESCRIPTION_CHARS) — it can carry LLM-generated text derived from
        attacker-controlled email content, and this method is the single choke
        point every POST path goes through.
        """
        description = _CTRL_CHARS_RE.sub(" ", description)[:MAX_DESCRIPTION_CHARS].strip()
        headers = {"api-key": self._api_key}
        body = {
            "tracking_number": tracking_number,
            "carrier_code": carrier_code,
            "description": description,
            "send_push_confirmation": False,
        }
        _LOGGER.debug(
            "Submitting TN %s (carrier=%s) to parcelapp.net", tracking_number, carrier_code
        )
        try:
            async with self._session.post(
                ADD_DELIVERY_URL,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                _LOGGER.debug(
                    "parcelapp.net responded HTTP %d for TN %s", resp.status, tracking_number
                )
                if resp.status in (401, 403):
                    raise ParcelAppAuthError(f"Auth failed: HTTP {resp.status}")
                if resp.status == 429:
                    reset_at: int | None = None
                    try:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict):
                            # WR-05/WR-07: validate + clamp the untrusted value at the
                            # source so a malformed body can never poison quota state.
                            reset_at = _validate_reset_at(data.get("reset_at"))
                    except (ValueError, aiohttp.ContentTypeError):  # fmt: skip
                        pass  # Non-JSON or wrong content-type body — reset_at stays None.
                    raise ParcelAppQuotaError("Daily quota (20/day) exhausted", reset_at=reset_at)
                if resp.status == 400:
                    _raise_from_error_body(await _read_error_body(resp))
                if resp.status >= 500:
                    raise ParcelAppTransientError(f"Server error: HTTP {resp.status}")
                if 400 <= resp.status < 500:
                    raise ParcelAppTransientError(f"Unexpected client error: HTTP {resp.status}")
                # 2xx path: assert success field — a success:false body is a rejected add.
                # Read body once; if success is not True, route via the shared error helper.
                raw_2xx = await _read_error_body(resp)
                # IN-04: a 2xx with a non-JSON or non-dict body is NOT proof of a
                # successful add — treating it as success wrote a permanent dedup
                # entry (and bumped forward counters) for a shipment that may never
                # have been added, e.g. a proxy serving an HTML error page with
                # status 200. Classify as transient so the POST retries next poll.
                if raw_2xx is _NO_JSON or not isinstance(raw_2xx, dict):
                    raise ParcelAppTransientError(
                        "Add-delivery returned 2xx with a non-JSON or non-dict body"
                    )
                if raw_2xx.get("success") is not True:
                    _raise_from_error_body(raw_2xx)
        except (TimeoutError, aiohttp.ClientError) as err:
            # WR-04: catch the aiohttp base class — ClientPayloadError (connection
            # lost mid-body) subclasses ClientError but NOT ClientConnectionError,
            # and previously escaped the documented ParcelApp exception taxonomy,
            # aborting the whole poll cycle instead of a per-message transient skip.
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
                # WR-06: the raise_for_status() that used to sit here was dead code —
                # every >= 400 status is already converted to an exception above.
                # Guard the 2xx body parse + shape so a non-JSON or non-dict body maps
                # into the documented taxonomy instead of escaping as a raw
                # ValueError/AttributeError coordinator error.
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise ParcelAppTransientError("View-deliveries returned non-JSON body") from err
                deliveries = data.get("deliveries", []) if isinstance(data, dict) else None
                if not isinstance(deliveries, list):
                    raise ParcelAppTransientError("View-deliveries returned unexpected JSON shape")
                return deliveries
        except (TimeoutError, aiohttp.ClientError) as err:
            # WR-04: catch the aiohttp base class (see async_add_delivery note).
            raise ParcelAppTransientError(f"Network error: {err}") from err
