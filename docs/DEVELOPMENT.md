<!-- generated-by: gsd-doc-writer -->
# Development Guide

This guide is for developers contributing to or modifying the Shop2Parcel integration.

---

## Local Setup

### Prerequisites

- Python 3.14 — required by `requires-python = ">=3.14"` in `pyproject.toml` and the CI pipeline, matching the HA runtime
- Git

### Clone and install

```bash
git clone https://github.com/Pascal-ZeGerman/shop2parcel.git
cd shop2parcel
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Always use `.venv/bin/python` — never bare `python` or `pip`. System Python is externally managed (PEP 668) and will refuse package installs.

The `.[dev]` extras install everything needed for testing:

| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support (`asyncio_mode = "auto"` in `pyproject.toml`) |
| `aioresponses` | Mock aiohttp responses in tests |
| `pytest-homeassistant-custom-component` | HA test fixtures (`hass`, `MockConfigEntry`) |

`ruff` and `mypy` are not declared in `.[dev]` — install them separately for local linting:

```bash
.venv/bin/python -m pip install ruff mypy
```

There is no compile or build step. The integration loads directly from `custom_components/shop2parcel/`.

### Google API stubs in tests

`gmail_client.py` imports from `googleapiclient` at module level. `tests/conftest.py` registers `MagicMock` stubs for `google`, `googleapiclient`, and `googleapiclient.errors` in `sys.modules` before any integration import. Always run pytest from the project root — the conftest runs automatically.

---

## Build Commands

| Command | Description |
|---|---|
| `.venv/bin/python -m pip install -e ".[dev]"` | Install package and dev dependencies in editable mode |
| `.venv/bin/python -m pytest tests/ -v --tb=short` | Run full test suite (matches CI) |
| `.venv/bin/python -m pytest tests/api/` | Run API-layer unit tests only |
| `.venv/bin/python -m ruff check .` | Lint — report all violations |
| `.venv/bin/python -m ruff format --check .` | Check formatting without writing |
| `.venv/bin/python -m ruff format .` | Auto-format all source files |
| `.venv/bin/python -m mypy custom_components/shop2parcel/` | Type-check the integration |

---

## Code Style

### Ruff (linter + formatter)

Configured in `pyproject.toml` under `[tool.ruff]` and `[tool.ruff.lint]`.

- **Line length**: 100 characters
- **Target**: Python 3.14 (`target-version = "py314"`)
- **Active rule sets**: `E` (pycodestyle), `F` (Pyflakes), `I` (isort), `UP` (pyupgrade), `B` (flake8-bugbear)
- **Suppressed**: `E501` — line-length is enforced by the `line-length` setting, not this rule code
- **Per-file overrides for `tests/**/*.py`**: `B`, `UP`, `F401`, `F841`, `E402` are suppressed so test files can use older idioms and bare assert patterns

Run before every commit:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format .
```

Both checks run in CI on every push and PR (`.github/workflows/pytest.yml`, `lint` job).

### mypy (type checker)

Configured in `pyproject.toml` under `[tool.mypy]`.

- **`strict = false`** — not full strict mode
- **`ignore_missing_imports = true`** — suppresses missing stubs for third-party packages (HA stubs are incomplete)
- **`follow_imports = "silent"`**
- **Excluded paths**: `tests/`, `.venv/`, `.claude/`

#### Per-module overrides

The following `[[tool.mypy.overrides]]` sections are defined in `pyproject.toml`. Only these three modules have specific error-code suppressions — no other modules inherit these suppressions.

| Module | Suppressed error codes | Reason |
|---|---|---|
| `custom_components.shop2parcel.options_flow` | `misc`, `no-untyped-def`, `no-untyped-call` | HA generic types (`OptionsFlowWithReload`, `ConfigFlowResult`) lack complete stubs |
| `custom_components.shop2parcel.config_flow` | `misc`, `no-untyped-def`, `no-untyped-call` | HA generic types (`ConfigFlow`, `ConfigFlowResult`) lack complete stubs |
| `custom_components.shop2parcel.coordinator` | `misc`, `no-untyped-def`, `no-untyped-call`, `attr-defined` | HA generic types (`DataUpdateCoordinator`, `OAuth2Session`) lack complete stubs |

`attr-defined` is suppressed **only** for `coordinator.py`. It is not suppressed for `options_flow.py` or `config_flow.py`.

Run type checks:

```bash
.venv/bin/python -m mypy custom_components/shop2parcel/
```

CI runs mypy in the same `lint` job as ruff.

---

## Project Structure

```
custom_components/shop2parcel/   # Integration root
    api/                         # Pure-Python API clients (no HA imports)
        carrier_codes.py         # Shopify carrier name → parcelapp.net carrier code mapping
        email_parser.py          # Tiered HTML + regex tracking-number extraction
        exceptions.py            # Custom exception taxonomy
        gmail_client.py          # Gmail API async wrapper (executor-based)
        imap_client.py           # IMAP fetch client (executor-based, imaplib)
        parcelapp.py             # parcelapp.net add-delivery and view-deliveries client
    coordinator.py               # Base: Shop2ParcelCoordinator, PollStats, Shop2ParcelStore
    gmail_coordinator.py         # GmailCoordinator subclass (_async_update_data — OAuth2 + Gmail API)
    imap_coordinator.py          # ImapCoordinator subclass (_async_update_data — IMAP SINCE-date fetch)
    config_flow.py               # UI config flow (connection type, credentials)
    options_flow.py              # UI options flow (poll interval, query, debug mode, broad scan)
    sensor.py                    # ShipmentSensor entities + 6 diagnostic sensor registrations
    binary_sensor.py             # Quota-exhausted binary sensor
    diagnostic_sensor.py         # Six diagnostic CoordinatorEntity subclasses
    diagnostics.py               # HA diagnostics platform endpoint (redacts credentials)
    application_credentials.py   # OAuth2 application credentials for Gmail path
    const.py                     # All constants: DOMAIN, CONF_*, DEFAULT_*, normalize_tracking_number()
    __init__.py                  # Entry setup/unload, coordinator construction, platform forwarding
    manifest.json                # HACS + HA integration manifest (version, requirements, iot_class)
    strings.json                 # UI strings for config + options flows
    translations/                # Locale files

tests/
    api/                         # Unit tests for api/ modules (carrier_codes, email_parser, etc.)
    conftest.py                  # Shared fixtures + google/googleapiclient sys.modules stubs
    fixtures/                    # Static test data (email HTML samples)
    test_coordinator.py          # Coordinator unit tests
    test_config_flow.py          # Config flow integration tests
    test_options_flow.py         # Options flow integration tests
    test_init.py                 # async_setup_entry / async_unload_entry tests
    test_sensor.py               # Sensor platform tests
    test_binary_sensor.py        # Binary sensor tests
    test_diagnostic_sensor.py    # Diagnostic sensor tests
    test_diagnostics.py          # Diagnostics endpoint tests
    test_debug_mode.py           # Debug/dry-run mode tests
    test_multi_account.py        # Multi-account config entry tests
    test_store_migration.py      # Store v1→v2 migration tests
    test_const.py                # normalize_tracking_number and constant tests
```

### Where to add new features

| Feature type | Files to modify |
|---|---|
| New config option | `const.py` (constant), `config_flow.py` or `options_flow.py` (UI), `strings.json` (labels), relevant coordinator |
| New sensor entity | `sensor.py` (shipment sensors) or `diagnostic_sensor.py` (diagnostic sensors) |
| New binary sensor | `binary_sensor.py` |
| New carrier template | `api/email_parser.py` (detect + parse functions, `CARRIER_REGISTRY`), `api/carrier_codes.py` |
| New exception type | `api/exceptions.py` — no HA imports in this file |
| Parcelapp API changes | `api/parcelapp.py` |

---

## Key Architectural Conventions

### No HA imports in `api/`

All modules under `custom_components/shop2parcel/api/` must not import from `homeassistant`. This boundary keeps the API layer independently testable without a running HA instance. The coordinator layer (`coordinator.py`, `gmail_coordinator.py`, `imap_coordinator.py`) is the only place that imports HA helpers.

### Shared aiohttp session

Always obtain the HTTP session via `async_get_clientsession(hass)`. Never create `aiohttp.ClientSession()` directly. `ParcelAppClient` accepts the session as a constructor argument injected by the coordinator.

### Synchronous I/O via executor

`google-api-python-client` and `imaplib` are synchronous libraries. All calls must go through `hass.async_add_executor_job`. `GmailClient` and `ImapClient` accept an `async_add_executor_job` callable as a constructor argument so they can be tested without a real `hass` instance.

### Error handling hierarchy

| Exception (`api/exceptions.py`) | Coordinator action |
|---|---|
| `GmailAuthError`, `ImapAuthError`, `ParcelAppAuthError` | Raise `ConfigEntryAuthFailed` — triggers HA reauth flow |
| `GmailTransientError`, `ImapTransientError`, `ParcelAppTransientError` | Raise `UpdateFailed` — logs, retries next poll |
| `ParcelAppQuotaError` | Set `_quota_exhausted_until`; skip forwarding until reset; save store immediately |
| `ParcelAppAlreadyAddedError` | Treat as idempotent success; write tracking number to dedup store |
| `ParcelAppInvalidTrackingError` | Log error; write to dedup store to suppress infinite retries |

### Store must be loaded before first refresh

In `async_setup_entry`, `_async_load_store()` must be called before `async_config_entry_first_refresh()`. Loading the store hydrates `_submitted_tracking_numbers` and `_quota_exhausted_until` from disk. Skipping this step causes the coordinator to re-POST every previously forwarded shipment on the first poll cycle, wasting the 20/day parcelapp.net add-delivery quota.

---

## Email Parser Strategies

`api/email_parser.py` implements a tiered approach. `EmailParser.parse()` runs strategies in a fixed order — first successful match wins:

### Strategy order

```
CARRIER_REGISTRY (UPS → USPS → FedEx)
    ↓ (carrier detected but extraction failed: fall through, skip Tier 2)
    ↓ (no carrier detected: fall through)
HTML template strategy (_parse_html_template)  →  strategy_used = "html_template"
    ↓ (no match)
Tier 1 regex (_parse_regex_tier1)              →  strategy_used = "regex_fallback"
    ↓ (no match, carrier NOT detected, broad scan enabled)
Tier 2 broad scan (_parse_regex_tier2)         →  strategy_used = "broad_regex"
```

### 1. Carrier template registry (`CARRIER_REGISTRY`)

A list of `(detect_fn, parse_fn)` tuples, consulted first to handle direct carrier emails (UPS, USPS, FedEx direct shipping notifications, as opposed to Shopify merchant emails).

Each `detect_fn(html)` checks for carrier-specific HTML fingerprints with a mandatory `and "shopify" not in html_lower` guard. This guard prevents misclassifying Shopify merchant emails that mention a carrier name in their body.

If `detect_fn` returns `True` but `parse_fn` fails to extract a tracking number, `carrier_detected` is set to `True` and parsing falls through to the HTML and Tier 1 strategies — but Tier 2 broad scan is skipped to avoid false-positive matches on carrier-branded templates (phone numbers, order IDs, etc.).

### 2. HTML template strategy — `strategy_used = "html_template"`

BeautifulSoup scans all `<p>` and `<td>` elements. Extracts:
- **Tracking number**: any 10-40 char alphanumeric token validated by `_looks_like_tracking()`
- **Carrier name**: regex `\bvia\s+([A-Za-z][A-Za-z ]{1,29}?)...` (non-greedy to prevent over-capture)
- **Order name**: regex `#([A-Z0-9][\w\-]{1,30})`

If no tracking number is found in element text, falls back to `_extract_tracking_from_hrefs()` which scans `<a href>` query parameters and path segments.

### 3. Tier 1 regex fallback — `strategy_used = "regex_fallback"`

Operates on full plain-text from `soup.get_text()`. Requires a labeled anchor for the tracking field (`tracking number:`, `tracking #:`, `tracking no.:`, etc.). The order field requires `#` or `:` after `order` to prevent false positives like "Your order has shipped" yielding `order_name="#HAS"`. All regex quantifiers are bounded (max 40 chars) — no ReDoS risk. Also has an href fallback when no labeled tracking number is found.

### 4. Tier 2 broad scan — `strategy_used = "broad_regex"` (opt-in, default OFF)

Sweeps all 10-40 char alphanumeric tokens from text and hrefs with no keyword label requirement. Returns the longest match with carrier inferred from shape via `_infer_carrier()`. Populates `ParseResult.candidate_tokens` for diagnostics.

Disabled by default. Enable via `CONF_ENABLE_BROAD_SCAN` in the options flow. This strategy is high-recall / low-precision — false positives consume the 20/day parcelapp.net add-delivery quota.

### `ParseResult` structure

Every code path returns a fully populated `ParseResult` (never `None`):

```python
@dataclass(slots=True, frozen=True)
class ParseResult:
    shipment: ShipmentData | None
    skip_reason: str | None
    # "no_template_match" | "no_tracking_label" | "tracking_invalid" | "no_tracking_pattern"
    strategy_used: str | None
    # "html_template" | "ups_template" | "usps_template" | "fedex_template" | "regex_fallback" | "broad_regex"
    keyword_hits: dict[str, bool]
    # always exactly three keys: "tracking_regex", "order_regex", "carrier_regex"
    candidate_tokens: list[str]   # populated by Tier 2 only; empty list otherwise
```

`keyword_hits` always contains exactly the three keys, even for HTML-strategy parses (all `False` in that case). Coordinators can iterate without key guards.

Strategy name constants are exported from `email_parser.py` — tests import these instead of using bare strings:

```python
from custom_components.shop2parcel.api.email_parser import (
    STRATEGY_HTML,      # "html_template"
    STRATEGY_UPS,       # "ups_template"
    STRATEGY_USPS,      # "usps_template"
    STRATEGY_FEDEX,     # "fedex_template"
    STRATEGY_REGEX,     # "regex_fallback"
    STRATEGY_BROAD_REGEX,  # "broad_regex"
)
```

---

## Adding a New Carrier

To add support for a new carrier's direct shipping notification emails:

### Step 1 — Add tracking number patterns (if needed)

In `api/email_parser.py`, extend `_TRACKING_PATTERNS` if the new carrier uses a format not already covered:

```python
_TRACKING_PATTERNS = [
    re.compile(r"^1Z[A-Z0-9]{16}$"),       # UPS
    re.compile(r"^9[12345][0-9]{15,24}$"),  # USPS domestic
    re.compile(r"^[A-Z]{2}[0-9]{9}[A-Z]{2}$"),  # USPS international
    re.compile(r"^(?:[0-9]{12}|[0-9]{15}|[0-9]{20})$"),  # FedEx
    re.compile(r"^[0-9]{10,11}$"),          # DHL
    # add your pattern here — use bounded quantifiers only (no ReDoS risk)
]
```

Add a carrier-specific extraction regex as a compiled module-level constant:

```python
_MYCARRIER_TRACKING_RE = re.compile(r"\b(MC[0-9]{12})\b")
```

### Step 2 — Add a `STRATEGY_` constant

```python
STRATEGY_MYCARRIER = "mycarrier_template"
```

### Step 3 — Add detection and parse functions

```python
def _detect_mycarrier(html: str) -> bool:
    """Return True if html is a MyCarrier shipping notification email."""
    html_lower = html.lower()
    # The 'and "shopify" not in html_lower' guard is required for all carrier detectors.
    return "mycarrier.com" in html_lower and "shopify" not in html_lower


def _parse_mycarrier(html: str, message_id: str, email_date: int) -> ParseResult:
    """Extract tracking number from MyCarrier shipping notification email."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")
    m = _MYCARRIER_TRACKING_RE.search(text)
    if m and _looks_like_tracking(m.group(1)):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=m.group(1),
                carrier_name="MyCarrier",
                order_name="",   # direct carrier emails have no Shopify order number
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_MYCARRIER,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    # href fallback
    tn = _extract_tracking_from_hrefs(soup)
    if tn and _looks_like_tracking(tn):
        return ParseResult(
            shipment=ShipmentData(
                tracking_number=tn,
                carrier_name="MyCarrier",
                order_name="",
                message_id=message_id,
                email_date=email_date,
            ),
            skip_reason=None,
            strategy_used=STRATEGY_MYCARRIER,
            keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
        )
    return ParseResult(
        shipment=None,
        skip_reason="no_template_match",
        strategy_used=None,
        keyword_hits={"tracking_regex": False, "order_regex": False, "carrier_regex": False},
    )
```

### Step 4 — Register the entry in `CARRIER_REGISTRY`

```python
CARRIER_REGISTRY: list[_CarrierEntry] = [
    (_detect_ups, _parse_ups),
    (_detect_usps, _parse_usps),
    (_detect_fedex, _parse_fedex),
    (_detect_mycarrier, _parse_mycarrier),  # add here; first match wins — order matters
]
```

### Step 5 — Add the carrier code mapping in `api/carrier_codes.py`

Add an entry to `_SHOPIFY_TO_PARCEL` (Shopify carrier strings are lowercased):

```python
"mycarrier": "myc",   # verify code at https://api.parcel.app/external/supported_carriers.json
```

If the carrier has no parcelapp code, use `"pholder"` — it is a valid parcelapp placeholder that returns HTTP 200. Do not use `"none"` — that is a parcel-ha internal sentinel and not a valid API code.

### Step 6 — Write tests

Add tests in `tests/api/test_email_parser.py`:

```python
def test_detect_mycarrier_real_email():
    html = "<html>...mycarrier.com...MC123456789012...</html>"
    result = EmailParser().parse(html, "test-id", 0)
    assert result.strategy_used == STRATEGY_MYCARRIER
    assert result.shipment is not None
    assert result.shipment.tracking_number == "MC123456789012"

def test_detect_mycarrier_does_not_match_shopify_email():
    html = "<html>...shopify...mycarrier.com...</html>"
    assert not _detect_mycarrier(html)
```

---

## Branch and PR Conventions

No formal branch naming convention is documented. The main branch is `main`. Common patterns used in this repository:

- `fix/<issue-number>-short-description` for bug fixes
- `feat/<topic>` for new features

### CI checks on every PR

All of the following must pass before merge:

| Workflow file | Checks |
|---|---|
| `.github/workflows/pytest.yml` | `pytest tests/ -v --tb=short`, `ruff check`, `ruff format --check`, `mypy custom_components/shop2parcel/` |
| `.github/workflows/hassfest.yml` | Validates `manifest.json` against HA integration requirements |
| `.github/workflows/hacs.yml` | Validates HACS packaging requirements |
| `.github/workflows/codeql.yml` | GitHub CodeQL security scan |

---

## Release Process

1. **Bump `manifest.json` version in the same PR** as the feature work. The release workflow validates that `manifest.json` version matches the git tag and fails if they diverge. Never tag before the manifest bump is merged. `pyproject.toml` version is for dev tooling only and does not need to match `manifest.json`.

2. **Tag after the PR is merged**:

```bash
git fetch origin main
git tag vX.Y.Z origin/main
git push origin vX.Y.Z
```

3. **Pre-release detection is automatic**: tags containing `-rc`, `-beta`, or `-alpha` are marked as GitHub pre-releases by `.github/workflows/release.yml`.

4. **If you tag before bumping the manifest**: open a hotfix PR with only the manifest bump, merge it, delete the tag, and re-push it pointing at the new `main` HEAD.

---

## Key Design Decisions Reference

| Decision | Rule |
|---|---|
| Coordinator data key | `dict[str, ShipmentData]` keyed by Gmail message ID or IMAP UID (as string) |
| Dedup mechanism | `_submitted_tracking_numbers` — `OrderedDict`, max 1000 entries (`MAX_SUBMITTED_TRACKING_NUMBERS`), persisted to HA Store after every change |
| Store schema (v2) | `{"submitted_tracking_numbers": [...], "quota_exhausted_until": int \| None}` |
| Quota handling | `ParcelAppQuotaError` sets `_quota_exhausted_until`; polling continues but POSTs are skipped until reset |
| Diagnostic ring buffer | `PollStats.scan_events` holds the 50 most recent events; `scan_events_total` is unbounded cumulative |
| Debug / dry-run mode | `CONF_DEBUG_MODE = True` — no POSTs sent; events recorded as `outcome="dry_run_suppressed"` |
| IMAP date format | RFC 3501 requires English month abbreviations — `strftime('%b')` must NOT be used (locale-dependent) |
| `api/` boundary | No HA imports in `api/` — all HA translation happens in the coordinator layer |

See `docs/GETTING-STARTED.md` for prerequisites and first-run setup.
