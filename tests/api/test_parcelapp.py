"""Tests for ParcelAppClient — parcelapp.net HTTP client.

Uses aioresponses to mock HTTP responses. All 18 behavioral scenarios
from the plan are covered.
"""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.shop2parcel.api.exceptions import (
    ParcelAppAuthError,
    ParcelAppInvalidTrackingError,
    ParcelAppQuotaError,
    ParcelAppTransientError,
)
from custom_components.shop2parcel.api.parcelapp import (
    ADD_DELIVERY_URL,
    VIEW_DELIVERIES_URL,
    ParcelAppClient,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    """Create a real aiohttp ClientSession for tests."""
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
async def client(session):
    """Create a ParcelAppClient with a test api_key."""
    return ParcelAppClient(session=session, api_key="test-key-123")


# ---------------------------------------------------------------------------
# async_add_delivery — success
# ---------------------------------------------------------------------------


async def test_add_delivery_success(client):
    """POST returns 200 → method returns without raising."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, payload={"success": True}, status=200)
        await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


# ---------------------------------------------------------------------------
# async_add_delivery — auth errors
# ---------------------------------------------------------------------------


async def test_add_delivery_auth_error_on_401(client):
    """POST returns 401 → ParcelAppAuthError raised."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, status=401)
        with pytest.raises(ParcelAppAuthError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


async def test_add_delivery_auth_error_on_403(client):
    """POST returns 403 → ParcelAppAuthError raised."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, status=403)
        with pytest.raises(ParcelAppAuthError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


# ---------------------------------------------------------------------------
# async_add_delivery — quota errors (429)
# ---------------------------------------------------------------------------


async def test_add_delivery_quota_error_on_429(client):
    """POST returns 429 → ParcelAppQuotaError raised."""
    with aioresponses() as mock:
        mock.post(
            ADD_DELIVERY_URL,
            status=429,
            payload={"success": False, "error_message": "Rate limit exceeded"},
        )
        with pytest.raises(ParcelAppQuotaError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


async def test_add_delivery_quota_error_reset_at_from_body(client):
    """POST returns 429 with reset_at body → ParcelAppQuotaError.reset_at populated."""
    with aioresponses() as mock:
        mock.post(
            ADD_DELIVERY_URL,
            status=429,
            payload={"reset_at": 1745452800},
        )
        with pytest.raises(ParcelAppQuotaError) as exc_info:
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")
        assert exc_info.value.reset_at == 1745452800


async def test_add_delivery_quota_error_reset_at_none_when_no_body(client):
    """POST returns 429 with empty body {} → ParcelAppQuotaError.reset_at is None."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, status=429, payload={})
        with pytest.raises(ParcelAppQuotaError) as exc_info:
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")
        assert exc_info.value.reset_at is None


# ---------------------------------------------------------------------------
# async_add_delivery — invalid tracking (400)
# ---------------------------------------------------------------------------


async def test_add_delivery_invalid_tracking_on_400(client):
    """POST returns 400 → ParcelAppInvalidTrackingError raised with error_message."""
    with aioresponses() as mock:
        mock.post(
            ADD_DELIVERY_URL,
            status=400,
            payload={"success": False, "error_message": "Invalid tracking"},
        )
        with pytest.raises(ParcelAppInvalidTrackingError) as exc_info:
            await client.async_add_delivery("BADTRACK", "ups", "Order #1234")
        assert "Invalid tracking" in str(exc_info.value)


# ---------------------------------------------------------------------------
# async_add_delivery — transient errors (5xx + network)
# ---------------------------------------------------------------------------


async def test_add_delivery_transient_error_on_500(client):
    """POST returns 500 → ParcelAppTransientError raised."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, status=500)
        with pytest.raises(ParcelAppTransientError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


async def test_add_delivery_transient_error_on_503(client):
    """POST returns 503 → ParcelAppTransientError raised."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, status=503)
        with pytest.raises(ParcelAppTransientError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


async def test_add_delivery_transient_error_on_network_failure(client):
    """aioresponses raises ClientConnectionError → ParcelAppTransientError raised."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, exception=aiohttp.ClientConnectionError("Connection refused"))
        with pytest.raises(ParcelAppTransientError):
            await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")


# ---------------------------------------------------------------------------
# async_add_delivery — request shape verification
# ---------------------------------------------------------------------------


async def test_add_delivery_request_uses_api_key_header(client):
    """POST succeeds → request has header api-key: test-key-123 (lowercase hyphen)."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, payload={"success": True}, status=200)
        await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")
        import yarl

        requests = mock.requests[("POST", yarl.URL(ADD_DELIVERY_URL))]
        assert len(requests) == 1
        assert requests[0].kwargs["headers"]["api-key"] == "test-key-123"


async def test_add_delivery_request_body_shape(client):
    """POST succeeds → body has expected fields including send_push_confirmation=False."""
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, payload={"success": True}, status=200)
        await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")
        import yarl

        requests = mock.requests[("POST", yarl.URL(ADD_DELIVERY_URL))]
        # aioresponses captures json= kwarg in requests[0].kwargs["json"]
        body = requests[0].kwargs["json"]
        assert body["tracking_number"] == "1Z999AA10123456784"
        assert body["carrier_code"] == "ups"
        assert body["description"] == "Order #1234"
        assert body["send_push_confirmation"] is False


async def test_add_delivery_api_key_not_in_url(client):
    """POST succeeds → request URL does not contain api-key value (header only, never query param).

    The api-key is passed as a header; verifies it is absent from every captured
    request URL (both the URL key and any URL-encoded query params in kwargs).
    """
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, payload={"success": True}, status=200)
        await client.async_add_delivery("1Z999AA10123456784", "ups", "Order #1234")
        import yarl

        # aioresponses keyed requests by (method, yarl.URL); verify no captured
        # URL contains the api-key value (guards against future query-param leak).
        for (method, url), _calls in mock.requests.items():
            assert "test-key-123" not in str(url)


# ---------------------------------------------------------------------------
# async_get_deliveries
# ---------------------------------------------------------------------------


async def test_get_deliveries_success(client):
    """GET returns 200 with deliveries list → method returns that list."""
    deliveries = [{"tracking_number": "1Z999AA10123456784", "carrier_code": "ups"}]
    with aioresponses() as mock:
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=recent",
            payload={"success": True, "deliveries": deliveries},
            status=200,
        )
        result = await client.async_get_deliveries()
    assert result == deliveries


async def test_get_deliveries_auth_error(client):
    """GET returns 401 → ParcelAppAuthError raised."""
    with aioresponses() as mock:
        mock.get(VIEW_DELIVERIES_URL + "?filter_mode=recent", status=401)
        with pytest.raises(ParcelAppAuthError):
            await client.async_get_deliveries()


async def test_get_deliveries_transient_error(client):
    """GET returns 500 → ParcelAppTransientError raised."""
    with aioresponses() as mock:
        mock.get(VIEW_DELIVERIES_URL + "?filter_mode=recent", status=500)
        with pytest.raises(ParcelAppTransientError):
            await client.async_get_deliveries()


async def test_get_deliveries_uses_filter_mode_param(client):
    """GET with filter_mode=active → URL includes filter_mode=active."""
    with aioresponses() as mock:
        mock.get(
            VIEW_DELIVERIES_URL + "?filter_mode=active",
            payload={"success": True, "deliveries": []},
            status=200,
        )
        result = await client.async_get_deliveries(filter_mode="active")
    assert result == []


# ---------------------------------------------------------------------------
# Static / no-HA-import check
# ---------------------------------------------------------------------------


def test_no_ha_imports():
    """Inspect parcelapp.py source — 'homeassistant' must not appear anywhere."""
    from pathlib import Path

    parcelapp_path = (
        Path(__file__).parent.parent.parent
        / "custom_components"
        / "shop2parcel"
        / "api"
        / "parcelapp.py"
    )
    contents = parcelapp_path.read_text(encoding="utf-8")
    assert "homeassistant" not in contents
