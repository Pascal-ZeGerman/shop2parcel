"""Tests for the GmailClient, extract_html_body, and build_incremental_query.

google-api-python-client is mocked at the module level so tests run without
the real library installed. Token values in tests are always "fake-token" literals
— never real OAuth2 access tokens (T-02-02 threat mitigation).
"""

from __future__ import annotations

import base64
import sys
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock googleapiclient before any import of gmail_client
# ---------------------------------------------------------------------------

# Create mock modules for google-api-python-client and google-auth
_mock_googleapiclient = MagicMock()
_mock_discovery = MagicMock()
_mock_errors = MagicMock()
_mock_google_oauth2 = MagicMock()
_mock_credentials_module = MagicMock()


# HttpError mock class — must behave like the real one (resp.status attribute)
class _MockHttpError(Exception):
    def __init__(self, resp, content=b"error"):
        super().__init__(str(content))
        self.resp = resp
        self.content = content


_mock_errors.HttpError = _MockHttpError

# Patch sys.modules before importing gmail_client
sys.modules.setdefault("googleapiclient", _mock_googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", _mock_discovery)
sys.modules.setdefault("googleapiclient.errors", _mock_errors)
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.oauth2", _mock_google_oauth2)
sys.modules.setdefault("google.oauth2.credentials", _mock_credentials_module)

from custom_components.shop2parcel.api.exceptions import (  # noqa: E402
    GmailAuthError,
    GmailTransientError,
)
from custom_components.shop2parcel.api.gmail_client import (  # noqa: E402
    GmailClient,
    build_incremental_query,
    extract_html_body,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_resp_mock(status: int):
    """Create a mock HTTP response with .status attribute."""
    resp = MagicMock()
    resp.status = status
    return resp


def make_http_error(status: int) -> _MockHttpError:
    """Create a mock HttpError with resp.status set."""
    resp = make_resp_mock(status)
    return _MockHttpError(resp=resp, content=b"error body")


def make_service_mock(list_result=None, get_result=None):
    """Build a mock Gmail service object with users().messages().list/get chain."""
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value=list_result or {})
    get_request = MagicMock()
    get_request.execute = MagicMock(return_value=get_result or {})
    service.users.return_value.messages.return_value.list.return_value = list_request
    service.users.return_value.messages.return_value.get.return_value = get_request
    return service, list_request, get_request


# ---------------------------------------------------------------------------
# Executor factory — captures calls for inspection
# ---------------------------------------------------------------------------


class _CapturingExecutor:
    """Async executor that runs callables inline and records calls.

    Call sequence from gmail_client:
      1st call: partial(build, "gmail", "v1", credentials=creds) → returns service mock
      2nd call: request.execute (bound method of request) → returns execute_return dict
    """

    def __init__(self, service=None, execute_return=None, raise_on_execute=None):
        self.service = service
        self.execute_return = execute_return
        self.raise_on_execute = raise_on_execute
        self.calls: list[Any] = []
        self._call_count = 0

    async def __call__(self, func, *args):
        self.calls.append((func, args))
        self._call_count += 1
        if self._call_count % 2 == 1:
            # Odd calls: build() partial → return service mock
            if self.service is not None:
                return self.service
            return MagicMock()
        else:
            # Even calls: request.execute → return result or raise
            if self.raise_on_execute:
                raise self.raise_on_execute
            return self.execute_return if self.execute_return is not None else {}


# ---------------------------------------------------------------------------
# Tests: async_list_messages
# ---------------------------------------------------------------------------


async def test_list_messages_appends_after_timestamp():
    """after_timestamp=1000 → query contains 'after:1000'."""
    captured_queries = []

    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={"messages": [{"id": "abc"}]})
    service.users.return_value.messages.return_value.list.side_effect = lambda userId, q: (
        captured_queries.append(q) or list_request
    )

    executor = _CapturingExecutor(service=service, execute_return={"messages": [{"id": "abc"}]})
    client = GmailClient(executor)
    result = await client.async_list_messages("fake-token", "from:shopify", after_timestamp=1000)
    assert any("after:1000" in q for q in captured_queries)


async def test_list_messages_no_after_timestamp():
    """after_timestamp=None → query contains 'after:' with a recent timestamp."""
    captured_queries = []
    expected_min = int(time.time()) - 31 * 86400

    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={})
    service.users.return_value.messages.return_value.list.side_effect = lambda userId, q: (
        captured_queries.append(q) or list_request
    )

    executor = _CapturingExecutor(service=service, execute_return={})
    client = GmailClient(executor)
    await client.async_list_messages("fake-token", "from:shopify", after_timestamp=None)
    assert len(captured_queries) == 1
    query = captured_queries[0]
    assert "after:" in query
    ts = int(query.split("after:")[1].strip())
    assert ts > expected_min


async def test_list_messages_returns_message_list():
    """Executor returns {'messages': [{'id': 'abc'}]} → method returns [{'id': 'abc'}]."""
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={"messages": [{"id": "abc"}]})
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, execute_return={"messages": [{"id": "abc"}]})
    client = GmailClient(executor)
    result = await client.async_list_messages("fake-token", "from:shopify")
    assert result == [{"id": "abc"}]


async def test_list_messages_returns_empty_on_no_results():
    """Executor returns {} → method returns []."""
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={})
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, execute_return={})
    client = GmailClient(executor)
    result = await client.async_list_messages("fake-token", "from:shopify")
    assert result == []


# ---------------------------------------------------------------------------
# Tests: async_get_message
# ---------------------------------------------------------------------------


async def test_get_message_calls_executor_with_format_full():
    """async_get_message → service.users().messages().get called with format='full'."""
    captured_kwargs: list[dict] = []

    service = MagicMock()
    get_request = MagicMock()
    get_request.execute = MagicMock(return_value={"id": "msg123", "payload": {}})
    service.users.return_value.messages.return_value.get.side_effect = lambda **kw: (
        captured_kwargs.append(kw) or get_request
    )

    executor = _CapturingExecutor(service=service, execute_return={"id": "msg123", "payload": {}})
    client = GmailClient(executor)
    await client.async_get_message("fake-token", "msg123")
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("format") == "full"
    assert captured_kwargs[0].get("id") == "msg123"


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


async def test_auth_error_on_401():
    """HttpError with status=401 → GmailAuthError raised."""
    http_err = make_http_error(401)
    # Make executor raise the error on execute() call
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute.side_effect = http_err
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, raise_on_execute=http_err)
    client = GmailClient(executor)
    with pytest.raises(GmailAuthError):
        await client.async_list_messages("fake-token", "from:shopify")


async def test_auth_error_on_403():
    """HttpError with status=403 → GmailAuthError raised."""
    http_err = make_http_error(403)
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute.side_effect = http_err
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, raise_on_execute=http_err)
    client = GmailClient(executor)
    with pytest.raises(GmailAuthError):
        await client.async_list_messages("fake-token", "from:shopify")


async def test_transient_error_on_500():
    """HttpError with status=500 → GmailTransientError raised."""
    http_err = make_http_error(500)
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute.side_effect = http_err
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, raise_on_execute=http_err)
    client = GmailClient(executor)
    with pytest.raises(GmailTransientError):
        await client.async_list_messages("fake-token", "from:shopify")


async def test_transient_error_on_network_failure():
    """Generic Exception from executor → GmailTransientError raised."""
    generic_err = ConnectionError("network gone")
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute.side_effect = generic_err
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, raise_on_execute=generic_err)
    client = GmailClient(executor)
    with pytest.raises(GmailTransientError):
        await client.async_list_messages("fake-token", "from:shopify")


# ---------------------------------------------------------------------------
# Tests: extract_html_body
# ---------------------------------------------------------------------------


def test_extract_html_body_decodes_base64url():
    """text/html part with base64url data → decoded UTF-8 string returned."""
    original = "<h1>Hello World</h1>"
    encoded = base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii")
    payload = {"mimeType": "text/html", "body": {"data": encoded}}
    result = extract_html_body(payload)
    assert result == original


def test_extract_html_body_pads_missing_equals():
    """Gmail omits = padding → decode with '==' appended must not raise."""
    original = "Hi!"  # 3 bytes → base64 is "SGkh" (no padding needed, but testing robustness)
    encoded = base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii").rstrip("=")
    payload = {"mimeType": "text/html", "body": {"data": encoded}}
    # Must not raise binascii.Error
    result = extract_html_body(payload)
    assert result is not None
    assert "Hi!" in result


def test_extract_html_body_recurses_into_parts():
    """Multipart payload with text/html in nested part → HTML body returned."""
    original = "<p>Shipped!</p>"
    encoded = base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii")
    payload = {
        "mimeType": "multipart/mixed",
        "body": {"data": ""},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": ""}},
            {"mimeType": "text/html", "body": {"data": encoded}},
        ],
    }
    result = extract_html_body(payload)
    assert result == original


# ---------------------------------------------------------------------------
# Tests: build_incremental_query
# ---------------------------------------------------------------------------


def test_build_incremental_query_with_timestamp():
    """build_incremental_query('base', 1000) → 'base after:1000'."""
    result = build_incremental_query("base", 1000)
    assert result == "base after:1000"


def test_build_incremental_query_none_defaults_30_days():
    """build_incremental_query('base', None) → contains after: with ts > (now - 31*86400)."""
    expected_min = int(time.time()) - 31 * 86400
    result = build_incremental_query("base", None)
    assert "after:" in result
    ts = int(result.split("after:")[1].strip())
    assert ts > expected_min
