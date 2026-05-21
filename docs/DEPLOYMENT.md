<!-- generated-by: gsd-doc-writer -->
# Deployment

Shop2Parcel is a Home Assistant custom integration distributed via HACS. "Deployment" means publishing a new release to GitHub so that HACS detects it and offers the update to users. There is no server to provision, no container to push, and no infrastructure to manage.

---

## Deployment Targets

| Method | Config File | Notes |
|--------|-------------|-------|
| HACS custom repository | `hacs.json` | Primary install method. Users add `https://github.com/Pascal-ZeGerman/shop2parcel` as a custom HACS repository. |
| Manual file copy | n/a | Copy `custom_components/shop2parcel/` into the HA config `custom_components/` directory. Supported but not recommended — no automatic updates. |

HACS reads `manifest.json` for the integration version and `hacs.json` for the minimum Home Assistant version requirement (`homeassistant: "2025.1.0"`).

---

## CI/CD Pipeline

All five workflows run on every push and pull request. The release workflow additionally triggers on version tags.

### Pull request and push checks

| Workflow | File | Trigger | What it does |
|----------|------|---------|--------------|
| pytest + lint | `.github/workflows/pytest.yml` | Push / PR (all branches) | Runs `pytest tests/ -v --tb=short`, then `ruff check`, `ruff format --check`, and `mypy custom_components/shop2parcel/` — all under Python 3.14. |
| hassfest | `.github/workflows/hassfest.yml` | Push / PR (all branches) | Validates `manifest.json` using the official `home-assistant/actions/hassfest` action. |
| HACS Action | `.github/workflows/hacs.yml` | Push / PR (all branches) | Validates HACS repository structure (file layout, `hacs.json` integrity, category `integration`). |
| CodeQL | `.github/workflows/codeql.yml` | Push / PR (all branches) + scheduled weekly Monday 03:00 UTC | Static security analysis using the `security-and-quality` query suite on Python. |

### Release workflow

| Workflow | File | Trigger |
|----------|------|---------|
| Release | `.github/workflows/release.yml` | Push of a `v*` tag |

**Steps executed by the release workflow:**

1. Check out the repository at the pushed tag (full history with `fetch-depth: 0`).
2. Validate that `manifest.json` `.version` matches the tag name (without the leading `v`). The workflow exits with a non-zero status if they diverge.
3. Detect whether the tag is a pre-release: tags containing `-rc`, `-beta`, or `-alpha` are automatically published as GitHub pre-releases.
4. Create a GitHub release using `softprops/action-gh-release@v2.3.2` with auto-generated release notes appended to a fixed body template.

---

## Release Process

Follow these steps in order. Tagging before the manifest version is updated will fail the release workflow at step 2.

1. **Bump version in both files** — edit `custom_components/shop2parcel/manifest.json` `.version` to the new version string. The `pyproject.toml` `version` field is used only by dev tooling and must be kept in sync manually, but HACS and HA only read `manifest.json`.

2. **Open and merge a PR** — the pytest, hassfest, HACS, and CodeQL checks must all pass. Do not tag from a feature branch.

3. **Tag the merged commit:**

   ```bash
   git fetch origin main
   git tag vX.Y.Z origin/main
   git push origin vX.Y.Z
   ```

4. **Verify the release workflow** completes successfully in the Actions tab. A GitHub release is created automatically with notes generated from commits since the previous tag.

**Pre-releases:** Any tag containing `-rc`, `-beta`, or `-alpha` (e.g., `v1.2.0-rc1`) is automatically published as a GitHub pre-release. HACS shows pre-releases only when the user opts in to experimental versions.

**Recovery — if you tag before bumping the manifest:**

1. Open a hotfix PR that bumps only `manifest.json` (and `pyproject.toml`) to the target version.
2. Merge the PR to `main`.
3. Delete the premature tag locally and on the remote:
   ```bash
   git tag -d vX.Y.Z
   git push origin :refs/tags/vX.Y.Z
   ```
4. Re-push the tag pointing at the new `main` HEAD:
   ```bash
   git fetch origin main
   git tag vX.Y.Z origin/main
   git push origin vX.Y.Z
   ```

---

## Environment Setup

Shop2Parcel has no server-side environment to configure. All credentials are entered by the end user through the Home Assistant UI during integration setup.

For the full list of configuration fields and their storage model, see [CONFIGURATION.md](CONFIGURATION.md).

The only external services the integration calls at runtime are:

- **Google Gmail API** — authenticated via OAuth2 tokens stored in the HA config entry.
- **parcelapp.net API** — authenticated via the user's API key stored in the HA config entry.

<!-- VERIFY: parcelapp.net API base URL and required scopes -->
<!-- VERIFY: Google Cloud project and OAuth2 consent screen requirements for the Gmail API scope -->

---

## Rollback Procedure

HACS retains the previously installed version in its download cache. To roll back:

1. In HA, go to **Settings > Devices & Services > Integrations** and note the current integration version.
2. In HACS, navigate to the Shop2Parcel integration page and select **Redownload**.
3. Choose the previous release version from the version dropdown.
4. Restart Home Assistant when prompted.

Alternatively, re-tag the last known-good commit and push it — HACS users who refresh will see the new (reverted) release.

---

## Monitoring

This integration runs inside the Home Assistant process. Observability uses HA's built-in mechanisms:

- **HA logs** — All integration log output is under the logger name `custom_components.shop2parcel`. Set log level in `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.shop2parcel: debug
  ```

- **Integration state** — The coordinator last-update timestamp and error state are visible in **Settings > Devices & Services > Shop2Parcel**.

- **Debug mode option** — When `debug_mode` is enabled in the integration options, tracking numbers are logged and a persistent HA notification is displayed instead of posting to parcelapp.net. Useful for validating email parsing without consuming the parcelapp.net add-delivery quota.

- **GitHub CodeQL** — Automated weekly security scans run against the `main` branch. Results appear in the repository's Security tab.

<!-- VERIFY: GitHub repository Security tab URL for CodeQL results -->
