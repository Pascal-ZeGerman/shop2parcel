<!-- generated-by: gsd-doc-writer -->
# Testing

## Test Framework and Setup

Shop2Parcel uses [pytest](https://docs.pytest.org/) with [pytest-homeassistant-custom-component](https://pypi.org/project/pytest-homeassistant-custom-component/) as its primary test harness. This package mirrors the test infrastructure used by Home Assistant core and supplies the `hass` fixture, `MockConfigEntry`, and the full HA async test harness without requiring a running HA instance.

| Library | Purpose |
|---------|---------|
| `pytest` | Test runner and assertion framework |
| `pytest-homeassistant-custom-component` | HA fixtures (`hass`, `MockConfigEntry`, `enable_custom_integrations`) |
| `pytest-asyncio` | Async test support (`asyncio_mode = "auto"` — all `async def test_` functions run automatically) |
| `aioresponses` | Intercepts outbound `aiohttp` HTTP calls in tests without real network access |

Before running tests, install the project with its dev dependencies:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

All dev dependencies are declared under `[project.optional-dependencies] dev` in `pyproject.toml`.

**Google API mocking note:** `tests/conftest.py` installs `MagicMock` stubs for `google`, `google.oauth2`, `googleapiclient`, and `googleapiclient.errors` into `sys.modules` before any integration import occurs. This is required because `gmail_client.py` imports from `googleapiclient` at module load time, and the Google client library is not installed in the test environment.

## Running Tests

All commands use the project's virtual environment Python.

**Run the full test suite:**

```bash
.venv/bin/pytest
```

**Run with verbose output and short tracebacks (matches CI):**

```bash
.venv/bin/pytest tests/ -v --tb=short
```

**Run a single test file:**

```bash
.venv/bin/pytest tests/test_coordinator.py -v
```

**Run a single test by name:**

```bash
.venv/bin/pytest tests/test_coordinator.py::test_new_shipment_is_posted -v
```

**Run only the API unit tests:**

```bash
.venv/bin/pytest tests/api/ -v
```

**Run with coverage reporting** (source scoped to `custom_components/`):

```bash
.venv/bin/pytest --cov=custom_components --cov-report=term-missing
```

The `testpaths`, `asyncio_mode`, and `pythonpath` settings in `pyproject.toml` are applied automatically — no extra flags needed for a standard run.

## Test Structure

Tests are organized under `tests/` with a flat module layer for integration-level tests and a nested `tests/api/` layer for unit tests of the API client modules.

```
tests/
├── conftest.py                  # Shared fixtures and Google API sys.modules stubs
├── fixtures/                    # HTML email fixtures for email parser tests
│   ├── shopify_shipping_email.html
│   ├── ups_shipping.html
│   ├── usps_shipping.html
│   ├── fedex_shipping.html
│   ├── href_tracking.html
│   └── plain_text_tracking.txt
├── test_init.py                 # Integration setup and teardown
├── test_coordinator.py          # Coordinator polling, forwarding, deduplication (65 tests)
├── test_config_flow.py          # Config flow UI steps (26 tests)
├── test_options_flow.py         # Options flow UI steps
├── test_sensor.py               # Sensor entity state (7 tests)
├── test_binary_sensor.py        # Binary sensor entity state
├── test_diagnostic_sensor.py    # Diagnostic sensor entity state
├── test_diagnostics.py          # HA diagnostics dump
├── test_debug_mode.py           # Debug/dry-run mode (DBG-01 through DBG-06)
├── test_multi_account.py        # Multi-account isolation (MULT-01, MULT-02)
├── test_store_migration.py      # Store v1→v2 migration (D-01, D-02, D-03)
├── test_const.py                # Constants sanity checks
└── api/
    ├── test_carrier_codes.py    # Carrier code lookup
    ├── test_email_parser.py     # EmailParser parsing strategies (60 tests)
    ├── test_exceptions.py       # Custom exception hierarchy
    ├── test_gmail_client.py     # GmailClient HTTP interactions (19 tests)
    ├── test_imap_client.py      # ImapClient IMAP interactions (11 tests)
    └── test_parcelapp.py        # ParcelAppClient HTTP interactions (20 tests)
```

## Writing New Tests

**File naming:** Test files follow the `test_*.py` convention. Files under `tests/` correspond to integration modules; files under `tests/api/` correspond to modules in `custom_components/shop2parcel/api/`.

**Async tests:** With `asyncio_mode = "auto"` in `pyproject.toml`, any `async def test_` function is automatically treated as an async test — no `@pytest.mark.asyncio` decorator is needed (though `tests/test_diagnostics.py` uses explicit markers for compatibility reasons).

**Core fixtures from `tests/conftest.py`:**

- `hass` — provided automatically by `pytest-homeassistant-custom-component`; a fully initialized but isolated HA instance
- `enable_custom_integrations` — `autouse=True` fixture that allows the HA component loader to find `custom_components/` during tests
- `mock_config_entry` — a `MockConfigEntry` pre-populated with valid Gmail OAuth2 token data and a `test-parcelapp-key`
- `mock_imap_config_entry` — a `MockConfigEntry` pre-populated with valid IMAP connection data
- `setup_coordinator_with_data(hass, mock_config_entry, data)` — shared helper that patches all coordinator dependencies (GmailClient, ParcelAppClient, EmailParser, Store, OAuth2 flow) and seeds `coordinator.data` with a supplied dict; returns the configured coordinator

**Mocking coordinator dependencies:** Integration tests that exercise the coordinator must patch all I/O. The standard pattern patches at the module where the name is looked up:

```python
with (
    patch("custom_components.shop2parcel.gmail_coordinator.GmailClient") as mock_gmail_cls,
    patch("custom_components.shop2parcel.gmail_coordinator.ParcelAppClient") as mock_parcel_cls,
    patch("custom_components.shop2parcel.gmail_coordinator.EmailParser") as mock_parser_cls,
    patch("custom_components.shop2parcel.coordinator.Shop2ParcelStore") as mock_store_cls,
    patch("custom_components.shop2parcel.gmail_coordinator.config_entry_oauth2_flow") as mock_oauth,
):
    mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
    mock_store_cls.return_value.async_save = AsyncMock()
    # ... configure mocks and run assertions
```

**Mocking HTTP responses:** Tests for `ParcelAppClient` and similar HTTP clients use `aioresponses` to intercept `aiohttp` calls:

```python
from aioresponses import aioresponses

async def test_add_delivery_success(client):
    with aioresponses() as m:
        m.post("https://web.parcelapp.net/...", status=200)
        await client.async_add_delivery(...)
```

**Email parser tests** use real HTML fixture files from `tests/fixtures/`. Load them via the provided fixtures (`shopify_html`, `ups_html`, `usps_html`, `fedex_html`) or read them directly with `pathlib.Path`.

**Test doc-string conventions:** Each test function carries a doc-string with the requirement ID it covers (e.g., `"""FWRD-01: New parsed shipment triggers ParcelAppClient.async_add_delivery."""`). Use the same convention when adding tests for a new requirement.

## Coverage Requirements

No minimum coverage thresholds are configured. The `[tool.coverage.run]` section in `pyproject.toml` scopes coverage collection to `custom_components/` when `--cov` is passed, but CI does not enforce a threshold — coverage is measured for visibility, not as a gate.

To generate a local coverage report:

```bash
.venv/bin/pytest --cov=custom_components --cov-report=term-missing --cov-report=html
```

The HTML report is written to `htmlcov/`.

## CI Integration

Tests run in the **`pytest + lint`** workflow (`.github/workflows/pytest.yml`) on every push and pull request to any branch.

**`pytest` job:**

| Setting | Value |
|---------|-------|
| Workflow file | `.github/workflows/pytest.yml` |
| Trigger | Push or PR to any branch |
| Runner | `ubuntu-latest` |
| Python version | `3.14` |
| Install command | `pip install ".[dev]"` |
| Test command | `python -m pytest tests/ -v --tb=short` |

**`ruff + mypy` job** (runs in parallel with the pytest job):

| Tool | Command |
|------|---------|
| ruff lint | `ruff check .` |
| ruff format | `ruff format --check .` |
| mypy | `mypy custom_components/shop2parcel/` |

Both jobs must pass for a PR to be considered ready to merge. The `hassfest` and `hacs` workflows (`.github/workflows/hassfest.yml` and `.github/workflows/hacs.yml`) run separately to validate `manifest.json` and HACS packaging requirements.
