"""Tests for Shop2Parcel coordinator — covers EMAIL-05, FWRD-01..FWRD-05.

Wave 0 stubs per VALIDATION.md. Plan 02 implements coordinator.py and removes
the xfail decorators to flip these tests green.

Test fixtures (`hass`, `mock_config_entry`) come from tests/conftest.py.
"""
from __future__ import annotations

import pytest

from custom_components.shop2parcel.const import DOMAIN  # noqa: F401


# -------- EMAIL-05: poll interval driven by entry.options ----------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_coordinator_uses_poll_interval(hass, mock_config_entry):
    """EMAIL-05: Coordinator update_interval reads from entry.options[CONF_POLL_INTERVAL]."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_coordinator_uses_poll_interval")


# -------- FWRD-01: new shipments POSTed to parcelapp ---------------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_new_shipment_is_posted(hass, mock_config_entry):
    """FWRD-01: New parsed shipment triggers ParcelAppClient.async_add_delivery."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_new_shipment_is_posted")


# -------- FWRD-02: deduplication via Store ------------------------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_no_duplicate_post(hass, mock_config_entry):
    """FWRD-02: message_id already in forwarded_ids set is not POSTed again."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_no_duplicate_post")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_dedup_survives_restart(hass, mock_config_entry):
    """FWRD-02: forwarded_ids persisted in Store survive coordinator re-init."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_dedup_survives_restart")


# -------- FWRD-03: Store load/save semantics ----------------------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_store_loaded_before_first_poll(hass, mock_config_entry):
    """FWRD-03: _async_load_store called before _async_update_data on setup."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_store_loaded_before_first_poll")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_store_saved_after_post(hass, mock_config_entry):
    """FWRD-03: Store.async_save called immediately after each successful POST."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_store_saved_after_post")


# -------- FWRD-04: quota handling ---------------------------------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_quota_exhaustion(hass, mock_config_entry):
    """FWRD-04: ParcelAppQuotaError sets quota_exhausted_until, logs warning, NOT raised."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_quota_exhaustion")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_quota_exhausted_until_midnight(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until = next midnight UTC when reset_at is None."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_quota_exhausted_until_midnight")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_quota_exhausted_until_reset_at(hass, mock_config_entry):
    """FWRD-04 / D-06: quota_exhausted_until uses err.reset_at when provided."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_quota_exhausted_until_reset_at")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_gmail_polling_continues_during_quota(hass, mock_config_entry):
    """FWRD-04 / D-05: while quota_exhausted_until > now, Gmail still polled, POST skipped."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_gmail_polling_continues_during_quota")


# -------- FWRD-05: error translation taxonomy ---------------------------

@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_parcelapp_transient_error_skipped(hass, mock_config_entry):
    """FWRD-05: ParcelAppTransientError is logged + skipped — NOT UpdateFailed, NOT in forwarded_ids."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_parcelapp_transient_error_skipped")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_gmail_transient_raises_update_failed(hass, mock_config_entry):
    """FWRD-05: GmailTransientError -> UpdateFailed (keeps last data, retries)."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_gmail_transient_raises_update_failed")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_gmail_auth_raises_config_entry_auth_failed(hass, mock_config_entry):
    """FWRD-05: GmailAuthError -> ConfigEntryAuthFailed (triggers reauth)."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_gmail_auth_raises_config_entry_auth_failed")


@pytest.mark.xfail(strict=True, reason="Plan 02 implements Shop2ParcelCoordinator")
async def test_invalid_tracking_not_deduped(hass, mock_config_entry):
    """FWRD-05: ParcelAppInvalidTrackingError logged; message_id NOT added to forwarded_ids."""
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator  # noqa: F401
    raise AssertionError("Plan 02 implements test_invalid_tracking_not_deduped")
