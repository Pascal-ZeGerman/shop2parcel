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

## CI/CD Workflows

The repository has five GitHub Actions workflows. Their triggers differ — they are documented individually below.

### pytest + lint (`pytest.yml`)

**Triggers:** `push` to any branch, `pull_request` to any branch.

Runs two parallel jobs under Python 3.14:

- **pytest** — installs `.[dev]` and runs `python -m pytest tests/ -v --tb=short`.
- **ruff + mypy** — installs `.[dev]` plus `ruff` and `mypy`, then runs `ruff check .`, `ruff format --check .`, and `mypy custom_components/shop2parcel/`.

Both jobs must pass before a PR can be merged.

### hassfest (`hassfest.yml`)

**Triggers:** `push` to any branch, `pull_request` to any branch.

Runs `home-assistant/actions/hassfest@master` to validate `manifest.json` and related integration metadata files against official Home Assistant requirements.

### HACS Action (`hacs.yml`)

**Triggers:** `push` to any branch, `pull_request` to any branch.

Runs `hacs/action@22.5.0` with `category: integration` and `ignore: brands`. Validates that the repository structure meets HACS distribution requirements (presence of `hacs.json`, correct `custom_components/` layout, etc.).

### CodeQL (`codeql.yml`)

**Triggers:** `push` to any branch, `pull_request` to any branch, **plus a weekly schedule** (`cron: "0 3 * * 1"` — Mondays at 03:00 UTC).

Runs GitHub's CodeQL static analysis on the Python codebase using the `security-and-quality` query suite. <!-- VERIFY: CodeQL results appear in the repository Security tab — requires GitHub Advanced Security to be enabled on the repo -->

### Release (`release.yml`)

**Triggers:** `push` of a tag matching `v*` only. This workflow does **not** run on branch pushes or pull requests.

Steps executed by the release workflow:

1. Check out the repository at the pushed tag (full history, `fetch-depth: 0`).
2. Validate that `custom_components/shop2parcel/manifest.json` `.version` matches the tag name with the leading `v` stripped. The workflow exits non-zero if they diverge.
3. Detect whether the tag is a pre-release: the tag name is tested for the substrings `-rc`, `-beta`, and `-alpha`. If any match, `is_prerelease=true` is set.
4. Create a GitHub release using `softprops/action-gh-release@v2.3.2` with `generate_release_notes: true`, `draft: false`, and the pre-release flag from the previous step.

---

## Release Process

Follow these steps in order. Tagging before the manifest version is updated will cause the release workflow to fail at step 2 with an explicit error message.

### Step 1 — Bump `manifest.json` version in a PR

Edit `custom_components/shop2parcel/manifest.json` and set the `version` field to the new version string (no leading `v`):

```json
{
  "version": "1.2.0"
}
```

Open a PR, wait for all four branch-triggered checks (pytest, hassfest, HACS, CodeQL) to pass, then merge to `main`.

### Step 2 — Push the tag

After the PR is merged:

```bash
git fetch origin main
git tag vX.Y.Z origin/main
git push origin vX.Y.Z
```

The `release.yml` workflow fires automatically on the tag push, validates the manifest match, and creates the GitHub release with auto-generated notes.

### Pre-release Tags

Tags containing `-rc`, `-beta`, or `-alpha` anywhere in the tag name are automatically published as GitHub pre-releases. No manual flag is needed.

| Tag example | Pre-release? |
|-------------|-------------|
| `v1.2.0` | No |
| `v1.2.0-rc1` | Yes |
| `v1.2.0-beta` | Yes |
| `v1.2.0-alpha.3` | Yes |

HACS shows pre-releases only when the user opts in to experimental/pre-release installs in HACS settings.

### Recovery — If You Tag Before Bumping the Manifest

The release workflow will fail with:

```
ERROR: manifest.json version (X.Y.Z) does not match tag (A.B.C)
Bump manifest.json version to A.B.C before tagging.
```

To recover:

1. Open a hotfix PR that bumps only `manifest.json` `version` to the target version.
2. Merge the PR to `main`.
3. Delete the premature tag locally and on the remote:
   ```bash
   git tag -d vA.B.C
   git push origin :refs/tags/vA.B.C
   ```
4. Re-push the tag pointing at the new `main` HEAD:
   ```bash
   git fetch origin main
   git tag vA.B.C origin/main
   git push origin vA.B.C
   ```

---

## Environment Setup

Shop2Parcel has no server-side environment to configure. All credentials are entered by the end user through the Home Assistant UI during integration setup and stored in HA's encrypted config entry storage.

For the full list of configuration fields and their storage model, see [CONFIGURATION.md](CONFIGURATION.md).

The only external services the integration calls at runtime are:

- **Google Gmail API** — authenticated via OAuth2 tokens stored in the HA config entry (Gmail connection type only).
- **parcelapp.net API** — authenticated via the user's API key stored in the HA config entry.

<!-- VERIFY: parcelapp.net API base URL and required scopes -->
<!-- VERIFY: Google Cloud project and OAuth2 consent screen requirements for the Gmail API scope -->

---

## Rollback Procedure

HACS supports selecting a previous release version during a redownload:

1. In HA, go to **Settings > Devices & Services** and note the current version shown on the Shop2Parcel tile.
2. In HACS, navigate to the Shop2Parcel integration page and select **Redownload**.
3. Choose the previous release version from the version dropdown.
4. Restart Home Assistant when prompted.

Alternatively, delete and re-push the last known-good tag if you need to restore a specific release artifact on GitHub. <!-- VERIFY: HACS version picker allows selecting any previously published release tag -->

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

- **GitHub CodeQL** — Automated weekly security scans run against the default branch every Monday at 03:00 UTC. Results appear in the repository's Security tab. <!-- VERIFY: GitHub repository Security tab URL for CodeQL results -->

No external APM, Sentry, Datadog, or alerting service is configured in the integration.
