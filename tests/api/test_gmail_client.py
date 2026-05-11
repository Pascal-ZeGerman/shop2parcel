"""Tests for the GmailClient, extract_html_body, and build_incremental_query.

google-api-python-client is mocked at the module level so tests run without
the real library installed. Token values in tests are always "fake-token" literals
— never real OAuth2 access tokens (T-02-02 threat mitigation).
"""

from __future__ import annotations

import base64
import sys
import time
from functools import partial
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

# Patch sys.modules before importing gmail_client.
# Use direct assignment (not setdefault) for googleapiclient.errors so that
# _MockHttpError is always the HttpError class used in gmail_client.py —
# even when conftest.py has already registered a stub HttpError for coordinator
# test isolation. Direct assignment re-registers the module and the already-imported
# gmail_client module will have HttpError patched at the namespace level below.
sys.modules.setdefault("googleapiclient", _mock_googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", _mock_discovery)
sys.modules["googleapiclient.errors"] = _mock_errors  # direct assignment — see comment above
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.oauth2", _mock_google_oauth2)
sys.modules.setdefault("google.oauth2.credentials", _mock_credentials_module)

import custom_components.shop2parcel.api.gmail_client as _gmail_client_module  # noqa: E402
from custom_components.shop2parcel.api.exceptions import (  # noqa: E402
    GmailAuthError,
    GmailTransientError,
)
from custom_components.shop2parcel.api.gmail_client import (  # noqa: E402
    GmailClient,
    build_incremental_query,
    extract_html_body,
)

# Re-bind HttpError in gmail_client's module namespace so that isinstance() checks
# in _classify_gmail_error use _MockHttpError (not whatever conftest registered).
_gmail_client_module.HttpError = _MockHttpError

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
      build call:  partial(build, "gmail", "v1", credentials=creds) → returns service mock
      execute call: request.execute (bound method of request) → returns execute_return dict

    Distinguishes the two call types by checking isinstance(func, partial) — the
    gmail_client always wraps build() in functools.partial, while request.execute is
    a plain bound method. This avoids the fragile parity-based (call_count % 2)
    approach which breaks if the production code adds or removes executor calls.
    """

    def __init__(self, service=None, execute_return=None, raise_on_execute=None):
        self.service = service
        self.execute_return = execute_return
        self.raise_on_execute = raise_on_execute
        self.calls: list[Any] = []

    async def __call__(self, func, *args):
        self.calls.append((func, args))
        if isinstance(func, partial):
            # build() call — return service mock
            return self.service if self.service is not None else MagicMock()
        else:
            # request.execute call — return result or raise
            if self.raise_on_execute:
                raise self.raise_on_execute
            return self.execute_return if self.execute_return is not None else {}


# ---------------------------------------------------------------------------
# Tests: async_list_messages
# ---------------------------------------------------------------------------


async def test_list_messages_appends_rescan_window_after_filter():
    """rescan_window_days=7 → query contains 'after:' with a timestamp ~7 days ago."""
    captured_queries = []
    expected_min = int(time.time()) - 8 * 86400

    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={"messages": [{"id": "abc"}]})
    service.users.return_value.messages.return_value.list.side_effect = lambda userId, q: (
        captured_queries.append(q) or list_request
    )

    executor = _CapturingExecutor(service=service, execute_return={"messages": [{"id": "abc"}]})
    client = GmailClient(executor)
    messages, effective_query = await client.async_list_messages(
        "fake-token", "from:shopify", rescan_window_days=7
    )
    assert len(captured_queries) == 1
    assert "after:" in captured_queries[0]
    ts = int(captured_queries[0].split("after:")[1].strip())
    assert ts > expected_min
    assert "after:" in effective_query


async def test_list_messages_no_after_timestamp():
    """Default rescan_window_days=30 → query contains 'after:' with a recent timestamp."""
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
    await client.async_list_messages("fake-token", "from:shopify")
    assert len(captured_queries) == 1
    query = captured_queries[0]
    assert "after:" in query
    ts = int(query.split("after:")[1].strip())
    assert ts > expected_min


async def test_async_list_messages_rejects_after_timestamp_kwarg():
    """Regression guard: after_timestamp kwarg is no longer accepted → TypeError raised.

    Phase 10 D-06: after_timestamp parameter dropped from async_list_messages.
    """
    executor = _CapturingExecutor(service=MagicMock(), execute_return={})
    client = GmailClient(executor)
    with pytest.raises(TypeError):
        await client.async_list_messages("fake-token", "from:shopify", after_timestamp=123)


async def test_list_messages_returns_message_list():
    """Executor returns {'messages': [{'id': 'abc'}]} → method returns ([{'id': 'abc'}], query)."""
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={"messages": [{"id": "abc"}]})
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, execute_return={"messages": [{"id": "abc"}]})
    client = GmailClient(executor)
    messages, effective_query = await client.async_list_messages("fake-token", "from:shopify")
    assert messages == [{"id": "abc"}]
    assert "after:" in effective_query


async def test_list_messages_returns_empty_on_no_results():
    """Executor returns {} → method returns ([], query)."""
    service = MagicMock()
    list_request = MagicMock()
    list_request.execute = MagicMock(return_value={})
    service.users.return_value.messages.return_value.list.return_value = list_request

    executor = _CapturingExecutor(service=service, execute_return={})
    client = GmailClient(executor)
    messages, effective_query = await client.async_list_messages("fake-token", "from:shopify")
    assert messages == []
    assert "after:" in effective_query


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


def test_build_incremental_query_with_window_only():
    """build_incremental_query('base', 30) → 'base after:' followed by ts ≈ now-30d.

    Phase 10 D-06: last_timestamp parameter dropped. Only rescan_window_days controls
    the query boundary.
    """
    expected_min = int(time.time()) - 31 * 86400
    result = build_incremental_query("base", 30)
    assert result.startswith("base after:")
    ts = int(result.split("after:")[1].strip())
    assert ts > expected_min
    # Within 2 seconds of expected
    expected = int(time.time()) - 30 * 86400
    assert abs(ts - expected) <= 2


def test_build_incremental_query_none_defaults_30_days():
    """build_incremental_query('base') with no args → after: with ts ≈ now - 30*86400."""
    expected_min = int(time.time()) - 31 * 86400
    result = build_incremental_query("base")
    assert "after:" in result
    ts = int(result.split("after:")[1].strip())
    assert ts > expected_min


# ---------------------------------------------------------------------------
# New tests: QF-01 + QF-02 semantics
# ---------------------------------------------------------------------------


def test_default_gmail_query_has_no_label_inbox():
    """QF-01: DEFAULT_GMAIL_QUERY must not contain 'label:inbox'.

    Auto-archived shipping emails never appear in the inbox, so the old
    'label:inbox' token excluded them from every query result.
    """
    from custom_components.shop2parcel.const import DEFAULT_GMAIL_QUERY  # noqa: PLC0415

    assert "label:inbox" not in DEFAULT_GMAIL_QUERY, (
        "DEFAULT_GMAIL_QUERY must not contain 'label:inbox' — archived emails "
        "would be silently excluded (QF-01 fix)"
    )


def test_default_gmail_query_keeps_spam_exclusion():
    """QF-01: DEFAULT_GMAIL_QUERY must still contain '-label:spam' for the spam guard."""
    from custom_components.shop2parcel.const import DEFAULT_GMAIL_QUERY  # noqa: PLC0415

    assert "-label:spam" in DEFAULT_GMAIL_QUERY, (
        "DEFAULT_GMAIL_QUERY must retain '-label:spam' to exclude spam results"
    )


def test_build_incremental_query_7_day_window():
    """build_incremental_query('base', 7) → after: uses 7-day window.

    Phase 10 D-06: rescan_window_days is the only parameter beyond base_query.
    """
    now = int(time.time())
    result = build_incremental_query("base", 7)
    assert "after:" in result
    ts = int(result.split("after:")[1].strip())
    expected_window_start = now - 7 * 86400
    # Within ±5s tolerance for test execution time
    assert abs(ts - expected_window_start) <= 5, (
        f"Expected after: near {expected_window_start} (now - 7d), got {ts}."
    )


def test_build_incremental_query_rejects_three_positional_args():
    """Regression guard: calling with 3 positional args raises TypeError.

    Phase 10 D-06: last_timestamp parameter dropped — legacy callers with
    3 positional args must fail loudly rather than silently using the wrong value.
    """
    with pytest.raises(TypeError):
        build_incremental_query("base", 0, 30)  # type: ignore[call-arg]
