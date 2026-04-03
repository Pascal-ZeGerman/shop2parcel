<!-- GSD:project-start source:PROJECT.md -->
## Project

**Shop2Parcel — Shopify → Parcel App Home Assistant Integration**

Shop2Parcel is a Home Assistant custom integration that polls the Shopify API for order shipment data and forwards it to a personal parcel tracking app at web.parcelapp.net. It bridges the gap between Shopify's order fulfillment system and a self-hosted HA ecosystem, making shipment tracking a first-class sensor in Home Assistant.

**Core Value:** Shipment data from Shopify orders automatically appears in Home Assistant — without manual entry.

### Constraints

- **Tech Stack**: Python 3.11+, Home Assistant async architecture (`aiohttp` for HTTP), standard HA integration patterns
- **Auth**: Shopify credentials likely via a private app token (Admin API); parcelapp.net auth TBD from API inspection
- **Rate Limiting**: Shopify Admin REST API allows 2 req/s (leaky bucket); polling interval must respect this
- **Privacy**: API keys/tokens must be stored in HA's credential store, not in plain config YAML
- **Discovery**: Shopify API endpoint behavior must be confirmed via Android app traffic analysis (mitmproxy/Charles) before assuming endpoint shape
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Runtime
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.14.x | Runtime | HA 2026.3+ requires Python >=3.14.2. This is non-negotiable — HA ships its own Python environment on all official install methods. |
| Home Assistant | 2026.x (target: 2025.1+) | Host platform | Current stable is 2026.3. Integration must declare `homeassistant: "2025.1.0"` minimum in hacs.json. |
### Core HA Integration Framework
| Component | Pattern | Purpose | Why |
|-----------|---------|---------|-----|
| `config_entries.ConfigFlow` | `config_flow.py` | UI-driven setup | Standard HA pattern for all cloud integrations since HA 2021+. Stores credentials in encrypted config entry storage. Never use YAML-only config for a credential-holding integration. |
| `DataUpdateCoordinator` | `coordinator.py` | Polling orchestration | Single scheduled poll feeds all entities. Prevents N entities from each making separate API calls. The `_async_update_data()` method is the only place API calls happen. |
| `CoordinatorEntity` | `sensor.py` | Sensor base class | Subscribes to coordinator updates automatically. Entity properties read from `self.coordinator.data` — no independent I/O. |
| `ConfigEntryAuthFailed` | Error handling | Auth failure signaling | Raising this in `_async_update_data` triggers HA's built-in reauth flow rather than logging a generic error. |
| `UpdateFailed` | Error handling | Transient error signaling | Raising this keeps the last known state while logging the error. Use for network timeouts and 5xx responses. |
### HTTP Client
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `aiohttp` | 3.13.x (bundled with HA) | All outbound HTTP | HA bundles aiohttp; do not add it as a `requirements` entry. Use `async_get_clientsession(hass)` to obtain the shared session. Creating a new session per-request violates HA quality scale rules. |
| `homeassistant.helpers.aiohttp_client.async_get_clientsession` | Built-in | Session acquisition | HA's shared session reuses connections and respects HA's SSL certificate handling. The [inject-websession quality rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/) mandates passing this session into any API client class. |
- `requests` — synchronous; will block the HA event loop
- `httpx` — acceptable alternative, but aiohttp is already bundled so adding httpx as a dependency is unnecessary weight
- A new `aiohttp.ClientSession()` created per-request — wastes connections, violates HA patterns
### Shopify API
| Approach | Version/Endpoint | Purpose | Why |
|----------|-----------------|---------|-----|
| Shopify Custom App (Admin API) | Created via Shopify Dev Dashboard | Authentication source | Private Apps are being deprecated; new apps must be created via Dev Dashboard. Existing private app tokens continue to work but cannot be created after Jan 1, 2026. Custom apps from the Dev Dashboard work identically. |
| REST Admin API (legacy path) | `/admin/api/2024-10/orders.json` | Order/fulfillment polling | REST API was declared legacy October 1, 2024, but **existing custom apps retain access indefinitely** — Shopify has not set a deprecation deadline for REST on custom (non-public) apps. Full REST deprecation applies only to new public apps (required GraphQL since April 1, 2025). For a personal/private custom app this is low risk. |
| `X-Shopify-Access-Token` header | n/a | Auth header | Pass the Admin API access token via this header on every request. No OAuth flow needed for custom apps — the token is static after install. |
| GraphQL Admin API (preferred path) | `/admin/api/2024-10/graphql.json` | Alternative to REST | More future-proof. Required for new public apps. The `orders` query with `fulfillments` subfields returns the same tracking data. Adds query complexity but avoids REST deprecation risk. |
- REST Admin API: Leaky bucket, ~40 requests per app per store per minute (2 req/s sustained). Shopify returns HTTP 429 with a `Retry-After` header when the bucket is full.
- GraphQL Admin API: 100 points/second (Standard plan). A simple orders query costs ~1-5 points.
- Polling at 5-minute intervals (HA default minimum is 5 seconds but Shopify context makes minutes appropriate) will never approach these limits.
- `GET /admin/api/2024-10/orders.json?fulfillment_status=shipped&updated_at_min={timestamp}` — incremental polling for new/updated fulfilled orders
- `GET /admin/api/2024-10/orders/{id}/fulfillments.json` — fulfillment details with `tracking_number`, `tracking_numbers`, `tracking_company`, `tracking_url`
### Credential Storage
| Pattern | Mechanism | Purpose | Why |
|---------|-----------|---------|-----|
| Config entry `data` dict | HA encrypted storage | Shopify access token, parcelapp.net API key | `data` is stored in `.storage/core.config_entries` which HA encrypts. Sensitive tokens MUST go in `data`, not `options`. The `SchemaConfigFlowHandler` shortcut stores everything in `options` — do NOT use it for this integration. |
| `vol.Schema` + `voluptuous` | Input validation in config flow | Validate token format on entry | Already bundled with HA; no extra dependency needed. |
- `configuration.yaml` — no encryption, visible in filesystem
- `options` dict — semantically wrong, not secret-optimized
- Entity attributes — visible in HA UI and logs
### Testing
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `pytest-homeassistant-custom-component` | Latest (updated daily against HA releases) | HA test fixtures | Provides the `hass` fixture, `MockConfigEntry`, and the full HA async test harness. The canonical testing package for custom integrations — mirrors what HA core uses. |
| `pytest-asyncio` | Bundled via above | Async test support | Required for `async def test_` patterns |
| `aioresponses` | Latest | Mock aiohttp responses | Intercept outbound HTTP calls in tests without real network. Pairs with the shared aiohttp session. |
### HACS Packaging
| Component | Requirement | Details |
|-----------|-------------|---------|
| `hacs.json` (repo root) | Required | Minimum: `{"name": "Shop2Parcel", "homeassistant": "2025.1.0"}`. Optional: `"hacs": "1.32.0"` for minimum HACS version. |
| `custom_components/shop2parcel/` | Required | All integration files live here. One integration per repo — HACS requirement. |
| `manifest.json` | Required | Must include `version` (custom integrations only), `domain`, `name`, `codeowners`, `documentation`, `requirements`, `iot_class: "cloud_polling"`, `config_flow: true`. |
| GitHub Releases | Preferred | HACS shows the 5 latest releases. Use semantic versioning (`1.0.0`). Not strictly required — HACS falls back to default branch. |
| Brand assets | Optional for personal use | `brand/` directory with `icon.png`. Required for HACS default repository inclusion; not needed for personal/custom repo installation. |
| GitHub Actions | Recommended | `hassfest` action validates manifest.json; `hacs` action validates HACS requirements. Both run on every PR. |
## Full File Structure
## Alternatives Considered
| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| HTTP client | `aiohttp` (bundled) | `httpx` | httpx would add a dependency; aiohttp is already in HA's venv |
| HTTP client | `aiohttp` (bundled) | `requests` | Synchronous — blocks the HA event loop |
| Shopify API | REST Admin API (short term) | GraphQL Admin API | GraphQL is more future-proof but more complex for simple read-only polling; REST works fine for custom apps with no stated deprecation deadline |
| Auth | Static access token | OAuth2 | OAuth2 is for public Shopify apps; custom/private apps use static tokens — no OAuth needed |
| Testing | `pytest-homeassistant-custom-component` | Manual HA instance testing | Manual testing is slow and non-reproducible |
| HA data pattern | `DataUpdateCoordinator` | Per-entity `async_update()` | Per-entity polling makes N API calls for N sensors; coordinator makes 1 call |
## Installation (Development)
# Create venv matching HA's Python version
# Dev dependencies
# Run tests
## Key `manifest.json` Template
## Open Questions / Low-Confidence Areas
## Sources
- [HA Developer Docs: Fetching Data / DataUpdateCoordinator](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [HA Developer Docs: Config Flow Handler](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
- [HA Developer Docs: Integration Manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [HA Developer Docs: inject-websession quality rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/)
- [HA Developer Docs: Async patterns](https://developers.home-assistant.io/docs/asyncio_working_with_async/)
- [HA 2026.3 Release Notes (Python 3.14)](https://www.home-assistant.io/blog/2026/03/04/release-20263/)
- [Shopify: Access tokens for custom apps](https://shopify.dev/docs/apps/auth/admin-app-access-tokens)
- [Shopify: Legacy custom apps — Jan 2026 deprecation](https://changelog.shopify.com/posts/legacy-custom-apps-can-t-be-created-after-january-1-2026)
- [Shopify: REST Admin API fulfillment endpoints](https://shopify.dev/docs/api/admin-rest/latest/resources/fulfillment)
- [Shopify: API rate limits](https://shopify.dev/docs/api/usage/rate-limits)
- [HACS: Integration publishing requirements](https://www.hacs.xyz/docs/publish/integration/)
- [pytest-homeassistant-custom-component (PyPI)](https://pypi.org/project/pytest-homeassistant-custom-component/)
- [GitHub: jpawlowski/hacs.integration_blueprint](https://github.com/jpawlowski/hacs.integration_blueprint)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
