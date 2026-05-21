<!-- generated-by: gsd-doc-writer -->
# Development

This document covers local development setup, project conventions, build commands, code style, and the pull request process for the Shop2Parcel integration.

---

## Local Setup

### Prerequisites

- Python 3.14 (matches the HA runtime and `requires-python = ">=3.14"` in `pyproject.toml`)
- A virtual environment at `.venv/` — always use `.venv/bin/python`, never bare `python` or `pip` (system Python is externally managed per PEP 668)

### Clone and Install

```bash
git clone https://github.com/Pascal-ZeGerman/shop2parcel
cd shop2parcel
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The `.[dev]` extra installs all development dependencies: `pytest`, `pytest-asyncio`, `aioresponses`, and `pytest-homeassistant-custom-component`.

There is no build step. The integration is loaded directly from `custom_components/shop2parcel/` — no compilation or transpilation is needed.

### Google API stubs in tests

`gmail_client.py` imports from `googleapiclient` at module level. The test suite's `tests/conftest.py` registers `MagicMock` stubs for `google`, `googleapiclient`, and `googleapiclient.errors` in `sys.modules` before any integration import. Always run pytest via `.venv/bin/pytest` — the conftest runs automatically.

---

## Build Commands

There are no `package.json` scripts. All developer commands are run directly via `.venv/bin/python` or the tool's entry point inside the venv.

| Command | Description |
|---------|-------------|
| `.venv/bin/python -m pip install -e ".[dev]"` | Install package and dev dependencies in editable mode |
| `.venv/bin/pytest` | Run the full test suite |
| `.venv/bin/pytest tests/ -v --tb=short` | Run tests with verbose output (matches CI) |
| `.venv/bin/pytest tests/api/` | Run API-layer unit tests only |
| `ruff check .` | Lint the codebase |
| `ruff format --check .` | Check formatting without making changes |
| `ruff format .` | Apply formatting changes |
| `mypy custom_components/shop2parcel/` | Run type checking |

---

## Code Style

### Linter: Ruff

Config lives in `pyproject.toml` under `[tool.ruff]` and `[tool.ruff.lint]`.

- **Line length**: 100 characters
- **Target version**: `py314`
- **Selected rules**: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort), `UP` (pyupgrade), `B` (bugbear)
- **Ignored**: `E501` (line-length is enforced by `line-length`, not this rule)
- **Test file relaxation**: `B`, `UP`, `F401`, `F841`, `E402` are ignored in `tests/**/*.py`

Run lint: `ruff check .`
Run format check: `ruff format --check .`
Apply format: `ruff format .`

CI enforces both `ruff check` and `ruff format --check` in the `lint` job of `.github/workflows/pytest.yml`.

### Type checker: mypy

Config lives in `pyproject.toml` under `[tool.mypy]`.

- **strict**: `false`
- **ignore_missing_imports**: `true` (HA stubs are incomplete)
- **Excluded paths**: `tests/`, `.venv/`, `.claude/`
- Per-module overrides disable `misc`, `no-untyped-def`, `no-untyped-call`, and `attr-defined` for `options_flow`, `config_flow`, and `coordinator` — these modules use HA generic types that lack complete stubs.

Run type check: `mypy custom_components/shop2parcel/`

CI runs mypy in the same `lint` job.

---

## Key Architectural Conventions

These conventions are enforced by code review and must be followed in all contributions.

### No HA imports in `api/`

All modules under `custom_components/shop2parcel/api/` must not import from `homeassistant`. This boundary keeps the API layer independently testable without a running HA instance. The coordinator layer (`coordinator.py`, `gmail_coordinator.py`, `imap_coordinator.py`) is the only place that imports HA helpers.

Files in `api/`: `carrier_codes.py`, `email_parser.py`, `exceptions.py`, `gmail_client.py`, `imap_client.py`, `parcelapp.py`.

### Shared aiohttp session

Always obtain the HTTP session via `async_get_clientsession(hass)` — never create `aiohttp.ClientSession()` directly. The shared session reuses connections and respects HA's SSL configuration. `ParcelAppClient` accepts the session as a constructor argument (injected by the coordinator).

### Synchronous I/O via executor

`google-api-python-client` (`googleapiclient`) and `imaplib` are synchronous libraries. All calls to these libraries must go through `hass.async_add_executor_job` to avoid blocking the HA event loop. `GmailClient` accepts an `async_add_executor_job` callable as a constructor argument so it can be tested without a real `hass` instance.

### Error handling hierarchy

| Exception (from `api/exceptions.py`) | Coordinator action |
|--------------------------------------|-------------------|
| `GmailAuthError`, `ImapAuthError`, `ParcelAppAuthError` | Raise `ConfigEntryAuthFailed` — triggers HA reauth flow |
| `GmailTransientError`, `ImapTransientError`, `ParcelAppTransientError` | Raise `UpdateFailed` — logs error, retries next poll |
| `ParcelAppQuotaError` | Set `quota_exhausted_until`; skip forwarding until reset |
| `ParcelAppAlreadyAddedError` | Treat as idempotent success; write tracking number to dedup store |
| `ParcelAppInvalidTrackingError` | Log and skip; does NOT write to dedup store |

### Store must be loaded before first refresh

In `async_setup_entry`, `_async_load_store()` must be called before `async_config_entry_first_refresh()`. Loading the store hydrates `submitted_tracking_numbers` and `quota_exhausted_until` from disk. Skipping this step causes the coordinator to re-POST every previously forwarded shipment on the first poll cycle, wasting the 20/day parcelapp.net add-delivery quota.

### `manifest.json` version must match release tag

The release workflow (`release.yml`) validates that `manifest.json` version equals the git tag. Bump `manifest.json` version in the same PR as feature work, before tagging. `pyproject.toml` version is for dev tooling only and does not need to match.

---

## Directory Structure

```
custom_components/shop2parcel/   # Integration root
    api/                         # Pure-Python API clients (no HA imports)
        carrier_codes.py         # Carrier name → parcelapp.net carrier code mapping
        email_parser.py          # HTML and regex tracking-number extraction
        exceptions.py            # Custom exception taxonomy
        gmail_client.py          # Gmail API async wrapper (executor-based)
        imap_client.py           # IMAP fetch client (executor-based)
        parcelapp.py             # parcelapp.net add-delivery and view-deliveries client
    coordinator.py               # Base DataUpdateCoordinator, PollStats, Shop2ParcelStore
    gmail_coordinator.py         # GmailCoordinator subclass (_async_update_data)
    imap_coordinator.py          # ImapCoordinator subclass (_async_update_data)
    config_flow.py               # UI config flow (connection type, credentials)
    options_flow.py              # UI options flow (poll interval, query, debug mode)
    sensor.py                    # Shipment sensor and diagnostic sensor platform setup
    binary_sensor.py             # Active shipments binary sensor
    diagnostic_sensor.py         # Diagnostic sensor entity base
    diagnostics.py               # HA diagnostics endpoint (redacts credentials)
    application_credentials.py   # OAuth2 application credentials for Gmail
    const.py                     # All constants (DOMAIN, CONF_*, DEFAULT_*)
    __init__.py                  # Entry setup/unload, platform forwarding
    manifest.json                # HACS + HA integration manifest
    strings.json                 # UI strings (config flow, options flow)
    translations/                # Locale strings

tests/                           # pytest test suite
    api/                         # Unit tests for api/ modules
    conftest.py                  # Shared fixtures + google/googleapiclient stubs
    fixtures/                    # Static test data (email HTML samples, etc.)
    test_coordinator.py          # Coordinator unit tests
    test_config_flow.py          # Config flow integration tests
    test_options_flow.py         # Options flow integration tests
    test_init.py                 # async_setup_entry / async_unload_entry tests
    ...                          # Other per-module test files
```

---

## Branch Conventions

No formal branch naming convention is documented. The default and main branch is `main`. Typical patterns used in this repository:

- `fix/<issue-number>-short-description` for bug fixes
- `feat/<topic>` for new features

---

## PR Process

1. Open a PR against `main` from a topic branch.
2. All three CI jobs must pass before merge:
   - `pytest` — runs the full test suite on Python 3.14
   - `ruff + mypy` — lint, format check, and type check
   - `hassfest` — validates `manifest.json` against HA requirements
   - `HACS Action` — validates HACS repo structure
3. Bump `manifest.json` version in the same PR as feature work if the change warrants a release. Do not tag before the manifest bump is on `main`.
4. For pre-release tags, append `-rc`, `-beta`, or `-alpha` to the version (e.g., `v1.2.0-rc1`). The release workflow marks these as GitHub pre-releases automatically.
5. There is no separate PR template — describe what the change does and reference any issue numbers in the PR body.

See `docs/GETTING-STARTED.md` for prerequisites and first-run instructions.
