"""Gmail API client — async wrapper using executor for blocking google-api-python-client calls.

No HA imports. Caller (Phase 4 coordinator) passes hass.async_add_executor_job.
Token model: accepts pre-validated short-lived access_token per call.
Token refresh is the coordinator's responsibility (via OAuth2Session.async_ensure_token_valid).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from functools import partial
from typing import Any, NoReturn

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .exceptions import GmailAuthError, GmailTransientError


class GmailClient:
    """Wraps Gmail API for async use in HA. No HA imports — executor callable injected.

    In production: pass hass.async_add_executor_job as async_add_executor_job.
    In tests: pass an async callable that runs the sync function inline.
    Service object is cached per access_token to avoid repeated discovery doc fetches.
    """

    def __init__(self, async_add_executor_job: Callable) -> None:
        self._executor = async_add_executor_job
        self._service: Any = None
        self._service_token: str | None = None

    async def _get_service(self, access_token: str) -> Any:
        """Return cached Gmail service, rebuilding only when token rotates."""
        if self._service is None or self._service_token != access_token:
            creds = Credentials(token=access_token)
            self._service = await self._executor(partial(build, "gmail", "v1", credentials=creds))
            self._service_token = access_token
        return self._service

    async def async_list_messages(
        self,
        access_token: str,
        query: str,
        after_timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        """List Gmail messages matching query, optionally filtered by date.

        EMAIL-02: query is configurable (default: from:no-reply@shopify.com subject:shipped).
        EMAIL-08: after_timestamp appended as 'after:{ts}' for incremental polling.
        Paginates through all result pages — Gmail caps each page at 100 messages.
        """
        full_query = build_incremental_query(query, after_timestamp)
        try:
            service = await self._get_service(access_token)
            all_messages: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                kwargs: dict[str, Any] = {"userId": "me", "q": full_query}
                if page_token:
                    kwargs["pageToken"] = page_token
                request = service.users().messages().list(**kwargs)
                result = await self._executor(request.execute)
                all_messages.extend(result.get("messages", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            return all_messages
        except Exception as err:
            _classify_gmail_error(err)

    async def async_get_message(self, access_token: str, message_id: str) -> dict[str, Any]:
        """Fetch full message payload (format=full for MIME parts with body data)."""
        try:
            service = await self._get_service(access_token)
            request = service.users().messages().get(userId="me", id=message_id, format="full")
            return await self._executor(request.execute)
        except Exception as err:
            _classify_gmail_error(err)


def build_incremental_query(base_query: str, last_timestamp: int | None) -> str:
    """Append after: filter for incremental polling.

    Falls back to last 30 days on first run (no stored timestamp).
    EMAIL-08: stores epoch seconds; Gmail query accepts integer epoch seconds.
    """
    if last_timestamp is None:
        last_timestamp = int(time.time()) - 30 * 24 * 3600
    return f"{base_query} after:{last_timestamp}"


def extract_html_body(payload: dict) -> str | None:
    """Recursively extract HTML body from Gmail MIME payload.

    Gmail returns body data as base64url — always pad with '==' before decoding.
    Pitfall: base64.urlsafe_b64decode(data) raises binascii.Error if padding missing.
    Fix: always append '==' (extra padding is ignored by the decoder).
    """
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/html":
        body = payload.get("body") or {}  # guards body=None
        data = body.get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — binascii.Error or similar
                return None
    for part in payload.get("parts", []):
        result = extract_html_body(part)
        if result:
            return result
    return None


def extract_text_body(payload: dict) -> str | None:
    """Recursively extract text/plain body from Gmail MIME payload.

    Mirrors extract_html_body but matches text/plain MIME type.
    Used as fallback when no HTML body is present.
    """
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        body = payload.get("body") or {}
        data = body.get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return None
    for part in payload.get("parts", []):
        result = extract_text_body(part)
        if result:
            return result
    return None


def _classify_gmail_error(err: Exception) -> NoReturn:
    """Translate Google API exceptions to custom taxonomy. Never returns normally.

    EMAIL-06: HttpError 401/403 → GmailAuthError (coordinator raises ConfigEntryAuthFailed).
    EMAIL-07: All other failures → GmailTransientError (coordinator raises UpdateFailed).
    Security: never include access_token in exception message.
    """
    if isinstance(err, HttpError):
        if err.resp.status in (401, 403):
            raise GmailAuthError(str(err)) from err
        raise GmailTransientError(str(err)) from err
    raise GmailTransientError(str(err)) from err
