"""Shared test fixtures for Shop2Parcel integration tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock google/googleapiclient before any shop2parcel import. The lazy import
# inside async_setup_entry fires when the hass fixture runs async_setup_entry,
# which imports coordinator.py, which imports gmail_client.py. The mocks must
# be in sys.modules before `from custom_components.shop2parcel.const import DOMAIN`
# (below) triggers the package __init__.py on first access.
# gmail_client.py imports `from google.auth.exceptions import RefreshError, TransportError` at
# module level and uses those classes in isinstance() checks inside _classify_gmail_error. When
# `google` below is a MagicMock, `from google.auth.exceptions import ...` raises ModuleNotFoundError
# ('google' is not a package). Import the REAL google.auth / google.auth.exceptions modules FIRST
# (before the MagicMock setdefault of `google`) and pin them in sys.modules so the import resolves
# the genuine exception classes (required for the stale-token 401 → GmailStaleTokenError
# reclassification and its regression tests). google-auth ships with HA, so these modules always exist.
try:  # pragma: no cover - defensive; google-auth is always present in the HA test env
    import google.auth as _real_google_auth
    import google.auth.exceptions as _real_google_auth_exceptions
except ImportError:  # pragma: no cover
    _real_google_auth = None
    _real_google_auth_exceptions = None

_GOOGLE_MOCK = MagicMock()
sys.modules.setdefault("google", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2", _GOOGLE_MOCK)
sys.modules.setdefault("google.oauth2.credentials", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient", _GOOGLE_MOCK)
sys.modules.setdefault("googleapiclient.discovery", _GOOGLE_MOCK)

# Pin the real google.auth submodules AFTER the MagicMock overwrites `google`, so
# `from google.auth.exceptions import ...` resolves the genuine classes even though the
# `google` package object in sys.modules is a MagicMock. Direct assignment (not setdefault)
# because the MagicMock may have shadowed any prior google.auth entry.
if _real_google_auth is not None and _real_google_auth_exceptions is not None:
    sys.modules["google.auth"] = _real_google_auth
    sys.modules["google.auth.exceptions"] = _real_google_auth_exceptions
# Phase 7 fix: tests/test_coordinator.py must be collectable standalone (without
# tests/api/test_gmail_client.py running first).  The gmail_client module-level
# `from googleapiclient.errors import HttpError` fails when googleapiclient is a
# MagicMock and googleapiclient.errors is NOT in sys.modules.
#
# Solution: register a minimal errors-module mock with HttpError as a real exception
# class (required for isinstance() checks in _classify_gmail_error).
# tests/api/test_gmail_client.py uses setdefault — since conftest runs first this
# setdefault is now a no-op.  That test file is updated to use direct assignment
# so its _MockHttpError class takes effect for gmail_client's already-cached import.
#
# NOTE: gmail_client.py caches the HttpError class on first import.  When the full
# suite runs (gmail_client tests first), the class in play is from test_gmail_client.py.
# When test_coordinator.py runs alone, the class below is used — coordinator tests
# mock GmailClient entirely so HttpError is never exercised in those tests.


class _StubHttpError(Exception):
    """Stub HttpError for coordinator-test isolation — not used in isinstance() path."""

    def __init__(self, resp=None, content=b""):
        self.resp = resp
        self.content = content


_ERRORS_MOCK = MagicMock()
_ERRORS_MOCK.HttpError = _StubHttpError
sys.modules.setdefault("googleapiclient.errors", _ERRORS_MOCK)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shop2parcel.api.email_parser import ShipmentData
from custom_components.shop2parcel.const import DOMAIN

# NOTE: `hass` fixture is provided automatically by pytest-homeassistant-custom-component


async def setup_imap_coordinator_with_data(
    hass, mock_imap_config_entry, data: dict[str, ShipmentData]
):
    """Shared helper: set up the IMAP coordinator with pre-seeded data.

    W12/P12-WR-02: mirrors the Gmail setup_coordinator_with_data pattern for IMAP tests.
    Patches ImapClient, ParcelAppClient, EmailParser, and Shop2ParcelStore so no real
    I/O occurs.  After async_setup the coordinator.data is replaced with the supplied
    ``data`` dict and hass.async_block_till_done() drains listener callbacks.

    Returns the configured ImapCoordinator instance.
    """
    mock_imap_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.imap_coordinator.ImapClient") as mock_imap_cls,
        patch("custom_components.shop2parcel.imap_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.imap_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()  # finding 12: immediate forward save
        mock_imap_cls.return_value.fetch_shipping_emails = AsyncMock(return_value=[])
        await hass.config_entries.async_setup(mock_imap_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_imap_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data(data)
        await hass.async_block_till_done()
        return coordinator


async def setup_coordinator_with_data(hass, mock_config_entry, data: dict[str, ShipmentData]):
    """Shared helper: set up the coordinator with pre-seeded data and forward to platforms.

    Patches all coordinator dependencies (GmailClient, ParcelAppClient, EmailParser,
    Store, config_entry_oauth2_flow) so no real I/O occurs during setup.  After
    async_setup, coordinator.data is replaced with the supplied ``data`` dict and
    hass.async_block_till_done() drains any resulting listener callbacks.

    Returns the configured coordinator instance.
    """
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
        patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient"),
        patch("custom_components.shop2parcel.gmail_coordinator.EmailParser"),
        patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
        patch(
            "custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow"
        ) as mock_oauth,
    ):
        mock_oauth.OAuth2Session.return_value.async_ensure_token_valid = AsyncMock()
        mock_oauth.OAuth2Session.return_value.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": 9999999999.0,
        }
        mock_oauth.async_get_config_entry_implementation = AsyncMock(return_value=MagicMock())
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_delay_save = MagicMock()
        mock_store_cls.return_value.async_save = AsyncMock()  # finding 12: immediate forward save
        mock_gmail_cls.return_value.async_list_messages = AsyncMock(return_value=([], "q after:0"))
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.async_set_updated_data(data)
        await hass.async_block_till_done()
        return coordinator


def _make_test_hub(hass):
    """Construct a Shop2ParcelHub with a mocked, I/O-free shared Store.

    Phase 30-03: does NOT call hub.async_setup() — that spawns the hass-scoped
    background worker task (hub.py's _stub_worker/real worker), which is never
    needed by dedup-focused unit tests and would otherwise leak a lingering
    task at test teardown. Only the synchronous dedup core (check_and_mark,
    is_submitted, submitted_count, seed_from_list) and async_save/async_load
    (which only touch hub._store, set here directly) are exercised.
    """
    from custom_components.shop2parcel.hub import Shop2ParcelHub  # noqa: PLC0415

    hub = Shop2ParcelHub(hass)
    hub._store = MagicMock()
    hub._store.async_load = AsyncMock(return_value=None)
    hub._store.async_save = AsyncMock()
    return hub


def attach_hub(hass, coordinator):
    """Attach a fresh, I/O-free test hub to a directly-constructed coordinator.

    Phase 30-03 (DEDUP-01..03): mirrors Shop2ParcelHub.attach() in production
    (sets coordinator._hub) minus the real Store I/O and the background worker
    task. Every coordinator constructed during a test already gets a shared
    hub automatically via the autouse `_auto_attach_test_hub` fixture below —
    call this explicitly only when a test needs its OWN hub instance distinct
    from that per-test shared one (e.g. proving two hubs do NOT share state).
    Returns the hub so the caller can assert on its dedup state directly.
    """
    hub = _make_test_hub(hass)
    hub.attach(coordinator)
    return hub


@pytest.fixture(autouse=True)
def _auto_attach_test_hub(hass):
    """Auto-attach a shared, I/O-free Shop2ParcelHub to every coordinator
    constructed directly during a test (bypassing hass.config_entries.async_setup).

    Phase 30-03 (DEDUP-01..03): every dedup read/write in coordinator.py,
    gmail_coordinator.py, and imap_coordinator.py now routes through
    coordinator._hub, which production code sets via Shop2ParcelHub.attach()
    inside async_setup_entry. Tests across this suite construct
    GmailCoordinator/ImapCoordinator/Shop2ParcelCoordinator directly to unit
    test the coordinator in isolation — without this fixture, _hub stays None
    and every dedup call site raises AssertionError.

    Implementation: monkeypatches Shop2ParcelCoordinator.__init__ (the base
    class every subclass's __init__ calls via super().__init__()) to set
    self._hub directly to a single per-hass-instance test hub (cached under
    hass.data[DOMAIN]["_test_hub"] — a key distinct from the production
    "__shared__" key, so it can never collide with or be mistaken for a real
    hub). Setting _hub directly (not via hub.attach()) means this fixture
    never touches hub._refcount.

    Tests that go through the REAL async_setup_entry (via
    hass.config_entries.async_setup(...), e.g. setup_coordinator_with_data in
    this file) are UNAFFECTED: production code's explicit
    hub.attach(coordinator) call runs immediately after construction and
    overwrites self._hub with the real hub, incrementing refcount exactly
    once — the LIFE-01..05 hub lifecycle tests in test_hub.py never observe
    this fixture's test hub.
    """
    from custom_components.shop2parcel.coordinator import Shop2ParcelCoordinator

    original_init = Shop2ParcelCoordinator.__init__

    def patched_init(self, hass_arg, entry):
        original_init(self, hass_arg, entry)
        hass_arg.data.setdefault(DOMAIN, {})
        test_hub = hass_arg.data[DOMAIN].get("_test_hub")
        if test_hub is None:
            test_hub = _make_test_hub(hass_arg)
            hass_arg.data[DOMAIN]["_test_hub"] = test_hub
        self._hub = test_hub

    with patch.object(Shop2ParcelCoordinator, "__init__", patched_init):
        yield


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):  # noqa: F811
    """Allow HA's component loader to find custom_components/ during tests."""
    return enable_custom_integrations


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a MockConfigEntry with minimal valid data for Shop2Parcel."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": 9999999999.0,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
            "api_key": "test-parcelapp-key",
        },
        unique_id="user@gmail.com",
    )


@pytest.fixture
def mock_imap_config_entry() -> MockConfigEntry:
    """Return a MockConfigEntry with minimal valid IMAP data for Shop2Parcel."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": "imap",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_username": "user@example.com",
            "imap_password": "app-password-here",
            "imap_tls": "ssl",
            "api_key": "test-parcelapp-key",
        },
        options={
            "imap_search": 'SUBJECT "shipped"',
            "poll_interval": 30,
        },
        unique_id="user@example.com@imap.example.com",
    )
