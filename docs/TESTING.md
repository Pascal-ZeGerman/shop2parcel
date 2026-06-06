<!-- generated-by: gsd-doc-writer -->
# Testing

Shop2Parcel uses [pytest](https://pytest.org) with `pytest-homeassistant-custom-component` to run the test suite across 19 test files. Tests cover the full integration stack: email parsing, HTTP clients, coordinator logic, entity registration, options flows, diagnostics, OAuth2 application credentials, and debug mode.

---

## Test Framework and Setup

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | `async def test_*` support (`asyncio_mode = "auto"` in `pyproject.toml`) |
| `pytest-homeassistant-custom-component` | `hass` fixture, `MockConfigEntry`, HA async test harness |
| `aioresponses` | Intercept outbound `aiohttp` HTTP calls without network access |

All four are declared in `pyproject.toml` under `[project.optional-dependencies] dev`:

```toml
[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "aioresponses",
    "pytest-homeassistant-custom-component",
]
```

No additional global setup is required beyond installing the dev dependencies.

---

## Running Tests

Install the package and its dev dependencies first:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

Run the full test suite:

```bash
.venv/bin/pytest tests/ -v --tb=short
```

Run a single test file:

```bash
.venv/bin/pytest tests/test_coordinator.py -v
```

Run a single test by name:

```bash
.venv/bin/pytest tests/test_coordinator.py::test_new_shipment_is_posted -v
```

Run only the API-layer tests:

```bash
.venv/bin/pytest tests/api/ -v
```

Run tests matching a keyword:

```bash
.venv/bin/pytest tests/ -k "imap" -v
```

---

## Test Structure

The test suite is split between top-level integration tests (require the `hass` fixture and full HA component loading) and unit tests in `tests/api/` (no HA fixture needed).

```
tests/
├── conftest.py                   # Shared fixtures and sys.modules mocks
├── fixtures/                     # HTML/text email fixtures for parser tests
│   ├── shopify_shipping_email.html
│   ├── ups_shipping.html
│   ├── usps_shipping.html
│   ├── fedex_shipping.html
│   ├── href_tracking.html
│   └── plain_text_tracking.txt
├── api/
│   ├── test_carrier_codes.py     #  2 tests — normalize_carrier lookup table
│   ├── test_email_parser.py      # 60 tests — EmailParser strategies and ShipmentData
│   ├── test_exceptions.py        # 10 tests — exception taxonomy
│   ├── test_gmail_client.py      # 19 tests — GmailClient + build_incremental_query
│   ├── test_imap_client.py       # 11 tests — ImapClient, EXAMINE, error mapping
│   └── test_parcelapp.py         # 20 tests — ParcelAppClient HTTP scenarios
├── test_application_credentials.py  # OAuth2 authorization server + description placeholders
├── test_binary_sensor.py         #  2 tests — HasActiveShipmentsBinarySensor
├── test_config_flow.py           # 26 tests — OAuth2 + IMAP config flow steps
├── test_const.py                 #  9 tests — normalize_tracking_number
├── test_coordinator.py           # 65 tests — coordinator polling, dedup, error handling, scan events
├── test_debug_mode.py            # 12 tests — DBG-01..DBG-06 dry-run mode
├── test_diagnostic_sensor.py     # 12 tests — 6 diagnostic sensor entities
├── test_diagnostics.py           # 13 tests — HA diagnostics platform output
├── test_init.py                  #  8 tests — async_setup_entry / async_unload_entry
├── test_multi_account.py         #  4 tests — two concurrent config entries
├── test_options_flow.py          # 10 tests — options form validation and persistence
├── test_sensor.py                #  7 tests — ShipmentSensor entity lifecycle
└── test_store_migration.py       #  4 tests — v1→v2 Store migration
```

**Total: 19 test files.**

### Top-level test files

| File | Tests | What it covers |
|------|------:|----------------|
| `test_application_credentials.py` | 3 | `async_get_authorization_server` returns Google's OAuth2 v2 authorize/token URLs; `async_get_description_placeholders` returns the expected keys and console/repo URLs |
| `test_binary_sensor.py` | 2 | `HasActiveShipmentsBinarySensor` is `on` when data is non-empty, `off` when empty |
| `test_config_flow.py` | 26 | OAuth2 flow handler, IMAP flow steps, reauth paths, error mapping for `ImapAuthError` / `ImapTransientError` / `ParcelAppAuthError` |
| `test_const.py` | 9 | `normalize_tracking_number`: whitespace stripping, uppercasing, idempotence |
| `test_coordinator.py` | — | Full coordinator cycle (Gmail and IMAP), dedup persistence, quota exhaustion, error handling (`ConfigEntryAuthFailed`, `UpdateFailed`), cleanup logic (incl. multi-shipment composite keys), scan event ring buffer, LRU eviction, live-shipment FIFO trim, multi-shipment digest forwarding, plain-text body escape/`<pre>` wrap, `already_added` handling |
| `test_debug_mode.py` | 12 | Debug/dry-run mode: options toggle (DBG-01), 365-day window override (DBG-02), dedup bypass (DBG-03), no POST (DBG-04), `[Shop2Parcel DEBUG]` INFO logs (DBG-05), persistent notifications (DBG-06) — all for Gmail and IMAP variants |
| `test_diagnostic_sensor.py` | 12 | All 6 diagnostic sensor entities registered at setup; state values and attributes after a poll cycle |
| `test_diagnostics.py` | 13 | HA diagnostics platform: output shape, credential redaction (Gmail and IMAP), `recent_shipments` cap, JSON-safe `scan_events`, `activity_log` key |
| `test_init.py` | 8 | `async_setup_entry` wires the correct coordinator subclass, Store is loaded before first poll, auth failure sets `SETUP_ERROR` state, `async_unload_entry` cancels the cleanup timer |
| `test_multi_account.py` | 4 | Two config entries coexist without collision, separate `Store` keys per entry, `ImapCoordinator` instantiates `ImapClient`, entity unique IDs are non-colliding |
| `test_options_flow.py` | 10 | Options form defaults, validation ranges (`poll_interval`, `rescan_window_days`), IMAP vs Gmail schema branching, `rescan_window_days` persisted |
| `test_sensor.py` | 7 | `ShipmentSensor` created per shipment, attributes, `in_transit` state, stable `unique_id`, device grouping, entity cleanup, entity appears when coordinator data gains a new key |
| `test_store_migration.py` | 4 | Store `_async_migrate_func` v1→v2: old keys dropped, `submitted_tracking_numbers` seeded, `quota_exhausted_until` preserved, future versions pass through unchanged |

### `tests/api/` unit tests

These tests have no dependency on the `hass` fixture and run without a Home Assistant instance.

| File | Tests | What it covers |
|------|------:|----------------|
| `test_carrier_codes.py` | 2 | `normalize_carrier` maps Shopify carrier names to parcelapp codes (12 parametrized cases), fallback to `pholder` |
| `test_email_parser.py` | 60 | `EmailParser` HTML strategy, regex fallback, broad-regex tier-2 strategy, carrier-specific templates (UPS/USPS/FedEx), href tracking extraction, `ParseResult` shape, `ShipmentData` dataclass, strategy constants, `infer_carrier` logic |
| `test_exceptions.py` | 10 | Exception taxonomy: `GmailAuthError`, `GmailTransientError`, `ImapAuthError`, `ImapTransientError`, `ParcelAppAuthError`, `ParcelAppTransientError`, `ParcelAppQuotaError`, `ParcelAppInvalidTrackingError`, no HA imports |
| `test_gmail_client.py` | 19 | `GmailClient` using a mocked `googleapiclient`, `build_incremental_query`, `extract_html_body`, auth error classification |
| `test_imap_client.py` | 11 | `ImapClient` constructor, EXAMINE (read-only select), no mutating IMAP commands, `ImapAuthError` on login failure, `ImapTransientError` on non-OK select, `since_date` search criteria, socket leak regressions (CR-02) |
| `test_parcelapp.py` | 20 | `ParcelAppClient.async_add_delivery` and `async_get_deliveries`: 200 success, 401 → `ParcelAppAuthError`, 429 → `ParcelAppQuotaError` with `reset_at`, 400 → `ParcelAppInvalidTrackingError`, 400 + error body → `ParcelAppAlreadyAddedError`, 5xx → `ParcelAppTransientError`, network error, `VIEW_DELIVERIES_URL` request |

---

## Fixtures (`tests/conftest.py`)

`conftest.py` provides two `MockConfigEntry` fixtures and a shared coordinator setup helper.

### `mock_config_entry`

A `MockConfigEntry` configured for the Gmail/OAuth2 connection type. Used by most integration tests.

```python
MockConfigEntry(
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
```

### `mock_imap_config_entry`

A `MockConfigEntry` for the IMAP connection type:

```python
MockConfigEntry(
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
```

### `setup_coordinator_with_data(hass, mock_config_entry, data)`

A shared async helper that sets up the full integration with pre-seeded coordinator data. All external dependencies are patched (GmailClient, ParcelAppClient, EmailParser, Store, OAuth2 flow) so no real I/O occurs. Returns the configured coordinator instance:

```python
from tests.conftest import setup_coordinator_with_data
from custom_components.shop2parcel.api.email_parser import ShipmentData

async def test_my_feature(hass, mock_config_entry):
    data = {
        "msg_a": ShipmentData(
            tracking_number="1Z999AA10123456784",
            carrier_name="UPS",
            order_name="#1234",
            message_id="msg_a",
            email_date=1745452800,
        )
    }
    coordinator = await setup_coordinator_with_data(hass, mock_config_entry, data)
    # assert entity state, coordinator.data, etc.
```

### Google API mocking

`conftest.py` registers mocks for `google`, `google.oauth2`, `google.oauth2.credentials`, `googleapiclient`, and `googleapiclient.discovery` in `sys.modules` before any shop2parcel import. A minimal `_StubHttpError` is also registered so `googleapiclient.errors.HttpError` is resolvable during coordinator tests without the real library installed.

---

## Writing New Tests

### Integration tests (with `hass` fixture)

Use `setup_coordinator_with_data` for entity and coordinator state tests. Patch only the specific collaborator you want to exercise; the helper patches everything else.

For tests that exercise the coordinator's `_async_update_data` path directly (error handling, scan events, dedup logic), construct the coordinator manually and call `_async_load_store` then `_async_update_data` under a full patch context. See `test_coordinator.py::test_new_shipment_is_posted` for the canonical Gmail patch set, or `test_coordinator.py::test_imap_basic_poll_cycle` for the IMAP variant.

### `aioresponses` for HTTP mocking

For `ParcelAppClient` unit tests, use `aioresponses` as a context manager to intercept `aiohttp` calls:

```python
import aiohttp
import pytest
from aioresponses import aioresponses
from custom_components.shop2parcel.api.parcelapp import ADD_DELIVERY_URL, ParcelAppClient
from custom_components.shop2parcel.api.exceptions import ParcelAppQuotaError


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield ParcelAppClient(session=session, api_key="test-key-123")


async def test_quota_error(client):
    with aioresponses() as mock:
        mock.post(ADD_DELIVERY_URL, payload={"error": "quota exceeded"}, status=429)
        with pytest.raises(ParcelAppQuotaError):
            await client.async_add_delivery("TRACK123", "ups", "Order #1")
```

### IMAP mocking

For `ImapClient` unit tests, mock `imaplib.IMAP4_SSL` with `spec=imaplib.IMAP4_SSL`. The `spec` argument ensures calls to undeclared IMAP4 methods raise `AttributeError`, enforcing the read-only contract:

```python
from unittest.mock import MagicMock, patch
import imaplib
from custom_components.shop2parcel.api.imap_client import ImapClient


async def _inline_executor(func, *args):
    return func(*args)


def test_my_imap_scenario():
    mock_conn = MagicMock(spec=imaplib.IMAP4_SSL)
    mock_conn.login.return_value = ("OK", [b"logged in"])
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])
    mock_conn.logout.return_value = ("BYE", [b"bye"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        client = ImapClient(_inline_executor)
        client._fetch_sync(
            "imap.example.com", 993, "user@example.com", "password",
            "ssl", 'SUBJECT "shipped"', "1-Jan-2026",
        )
    # assert mock_conn method calls
```

### File naming convention

Test files for integration-level tests are named `tests/test_{module_name}.py`. API layer test files are named `tests/api/test_{api_module_name}.py`. Test functions are named `test_{what_it_checks}`.

---

## Coverage

A minimum coverage floor is enforced. Coverage collection and the threshold are
defined in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["custom_components"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

`fail_under` makes the test run exit non-zero if total coverage drops below the
floor — the CI `pytest` job runs with `--cov=custom_components` so a regression
fails the build. Raise the floor as coverage improves to ratchet against
regressions.

To generate a local coverage report (requires the `pytest-cov` dev dependency):

```bash
.venv/bin/pytest tests/ --cov=custom_components --cov-report=term-missing
```

---

## CI Integration

Tests run automatically on every push and pull request via `.github/workflows/pytest.yml`.

**Workflow:** `pytest + lint`
**Trigger:** push or PR to any branch (`branches: ["**"]`)
**Python version:** 3.14

The workflow has two parallel jobs:

**`pytest` job:**
1. Checks out the repository
2. Sets up Python 3.14 with pip caching
3. Installs the package and dev dependencies: `pip install ".[dev]"`
4. Runs the full test suite: `python -m pytest tests/ -v --tb=short`

**`ruff + mypy` job:**
1. Checks out the repository
2. Sets up Python 3.14 with pip caching
3. Installs lint tools: `pip install ".[dev]" ruff mypy`
4. Runs `ruff check .`
5. Runs `ruff format --check .`
6. Runs `mypy custom_components/shop2parcel/`

Both jobs must pass for a PR to be mergeable. The `hassfest.yml` and `hacs.yml` workflows additionally validate `manifest.json` and HACS packaging requirements on every PR.
