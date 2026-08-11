# Release Process

## Current Target

Octonomy's REST API is served on two live surfaces: `/api/v2` (the primary, advertised surface,
which adds the namespace axis) and `/api/v1` (global-only, **still supported — not deprecated**).
Both follow Semantic Versioning. GraphQL, external broker transports, persisted counters, and
external JWT or API gateway auth remain future phases.

## Versioning

Octonomy follows Semantic Versioning. Bug fixes are a patch, backward-compatible additions are a
minor, and breaking changes ship a new parallel URL-versioned surface plus a major bump — as
`/api/v2` did, keeping `/api/v1` supported. See [`versioning.md`](versioning.md) for the full
policy and what counts as breaking.

- Package metadata uses PEP 440 (`3.0.1`); OpenAPI and user-facing docs use SemVer (`3.0.1`).
- Set `OCTONOMY_API_VERSION` when a deployment should expose a different schema version string.

## Release Checklist

Before cutting a release, run the pre-release gate:

```bash
make install
make lint
make check
make migration-check
make test
make openapi-check
```

For PostgreSQL-backed verification:

```bash
make db-up
DATABASE_URL=postgres://octonomy:octonomy@localhost:5432/octonomy make test
```

For SQLite compatibility coverage:

```bash
make test-sqlite
```

Before tagging a release, regenerate the checked OpenAPI artifact if the project chooses to publish
one from the repository:

```bash
make openapi
```

## Cutting a Release

Routine releases are cut manually. Pick the bump (`PATCH` / `MINOR` / `MAJOR`) per
[`versioning.md`](versioning.md), then:

1. Branch `release/<version>` (e.g. `release/1.1.0`).
2. Bump the version everywhere it is stamped:
   - `pyproject.toml` `version`
   - `config/settings.py` `API_VERSION` default
   - `.env.example` (`OCTONOMY_API_VERSION`)
   - regenerate both schemas with `make openapi` (updates `info.version` in `docs/openapi.yaml`
     **and** `docs/openapi-v2.yaml` — `make version-check` only inspects the v1 file, but the
     OpenAPI drift gate in CI regenerates and diffs both)
   - refresh the lock with `uv lock` (the project's own `version` in `uv.lock`)
   - the example image tags, which have no default to fall back on:
     `deploy/kubernetes/{deployment,migrate-job,dispatcher-cronjob}.yaml` (one `image:` each),
     `deploy/docker/compose.yaml` (**three** `image:` lines — migrate, app, dispatcher), and
     `docs/deployment.md`. `make version-check` enforces every one of these via
     `scripts/check-image-refs.sh`, including that a file has not silently *lost* its reference,
     so a missed tag is a red gate rather than a stale example someone finds in production.
   - `SECURITY.md` — only when the supported line changes (a patch inside the same `x.y` line
     does not move it)

   Deliberately **not** stamped: `deploy/.env.production.example` and
   `deploy/kubernetes/configmap.yaml` leave `OCTONOMY_API_VERSION` unset so the application default
   tracks the release on its own. Nothing validates `deploy/`, so a value pinned there would go
   stale silently.
3. Update `CHANGELOG.md`: move the `[Unreleased]` entries under `## [<version>] - <date>`, add the
   `[<version>]` compare link, and reset `[Unreleased]` to `compare/v<version>...HEAD`.
4. Run the gates, then open the PR with `Closes #<issue>`:

   ```bash
   make release-check   # lint, checks, migrations, tests, openapi drift, audit, version-check
   ```

5. After merge and green CI, tag the merge commit. Pushing the tag is what triggers
   `publish-image.yml`, so this is the step that builds and publishes the container image:

   ```bash
   git tag -a v<version> -m "Octonomy <version>" <merge-commit>
   git push origin v<version>
   ```

6. **Verify the image before announcing the release. This gate is mandatory.**

   A GitHub release is the announcement; the image is the artifact people actually run. Publishing
   the announcement first means telling people to `docker pull` a tag that may not exist — and
   `docs/deployment.md`, the `deploy/` examples, and the README quickstart all name `:<version>`
   the moment the release is public. **A release is not published until its image is.**

   The publish run can also fail for reasons that have nothing to do with your code: a queued run
   cancelled by a concurrent release (see ["When A Publish Run Does Not
   Appear"](#when-a-publish-run-does-not-appear)), or a first-ever publish against a private
   package (see ["First Publish To GHCR"](#first-publish-to-ghcr)).

   ```bash
   # a. The publish workflow actually ran for this tag, and succeeded.
   gh run list --workflow=publish-image.yml --limit 5

   # b. The version tag resolves, and the moving tags followed it.
   ./scripts/check-tag-unpublished.sh ghcr.io/octoverse-id/octonomy <version>   # -> "present <digest>"
   ./scripts/check-tag-unpublished.sh ghcr.io/octoverse-id/octonomy latest      # same digest, if this is the newest release

   # c. It is pullable by someone who is NOT logged in — the whole point of publishing it.
   #    A fresh DOCKER_CONFIG drops your credentials without logging you out.
   DOCKER_CONFIG=$(mktemp -d) docker pull ghcr.io/octoverse-id/octonomy:<version>

   # d. Both attestations verify. Check them BY PREDICATE TYPE — a bare `gh attestation
   #    verify` passes with the SBOM missing entirely.
   gh attestation verify oci://ghcr.io/octoverse-id/octonomy:<version> \
     --repo octoverse-id/octonomy --predicate-type https://slsa.dev/provenance/v1
   gh attestation verify oci://ghcr.io/octoverse-id/octonomy:<version> \
     --repo octoverse-id/octonomy --predicate-type https://spdx.dev/Document
   ```

   Step (d) needs **`gh` 2.49 or newer** — `gh attestation` does not exist on older builds (the
   `gh 2.4.0` packaged by Debian/Ubuntu prints `unknown command "attestation"`). This is not a
   reason to skip the gate: `publish-image.yml` runs these two exact commands on the runner, with
   both predicate types, **before** it promotes any tag, so a successful run in step (a) already
   proves the attestations verified. On an older `gh`, confirm it in the run log instead:

   ```bash
   gh run view <run-id> --log | grep -A3 "Verify provenance and SBOM"
   ```

   If any of these fail, **stop and fix the publish before continuing** — do not create the
   release. The version tag in git is already pushed, which is fine: re-publishing the same
   version re-promotes the digest it already published and refuses if the bytes differ, so the
   recovery path is safe to re-run.

7. Only once step 6 is fully green, publish the release:

   ```bash
   gh release create v<version> --title "v<version>" --notes-file <notes>
   ```

8. Close the tracking issue and delete the merged branch.

> `gh` caveats (observed on `gh 2.4.0`): `release create` has no `--latest` / `--verify-tag` — a
> published, non-prerelease release is "Latest" by default. `gh issue close` has no `--comment`;
> post the comment separately with `gh issue comment` before closing.

## First Publish To GHCR

**The very first run of `publish-image.yml` is expected to fail, and it is not a defect.** GHCR
creates a brand-new package as **private**, even when the repository is public. The workflow's last
gate before promoting tags is an anonymous pull — the check that the published image is actually
usable by someone who is not logged in — so that gate fails on the first publish, by design.

This happens minutes after the publish workflow first lands on `main`: merging it makes the next
green CI run publish `:edge`.

Recovery is one manual step, once, for the lifetime of the package:

1. Open **Packages** on the organisation or repository page and select `octonomy`.
2. **Package settings → Danger Zone → Change visibility → Public**.
3. Re-run the failed workflow run.

While the package is private, the failure looks like a `docker pull` returning `denied` or
`unauthorized`. Nothing else in the workflow needs changing, and nothing was published under a
release tag — the image is pushed by digest and only promoted to `:X.Y.Z` / `:X.Y` / `:latest`
**after** every check passes, so a failure at this gate leaves an untagged, prunable digest rather
than a broken release.

Also confirm, once, that **Inherit access from repository** stays enabled in the same settings page,
so `GITHUB_TOKEN` keeps its push permission for subsequent releases.

## When A Publish Run Does Not Appear

Release publishes share one concurrency group so they cannot promote `:X.Y` / `:latest` over each
other. GitHub keeps at most one run *pending* per group, so if several releases are cut inside one
build window, a queued one can be cancelled before it starts — its version is then tagged in git
with no image published.

Check the Actions tab after tagging. To publish a version whose run was cancelled, dispatch
`Publish image` **from that version's tag** with `dry_run` unchecked. That path is idempotent: if
the digest is already published it re-promotes it rather than rebuilding, and it refuses outright
if the version resolves to different bytes.

## Dependency Audit

CI scans the locked runtime dependencies for known vulnerabilities (the `security` job; run
locally with `make audit`). The gate fails closed: a newly disclosed runtime CVE — or a transient
advisory-service or network outage — blocks merges until resolved. To accept a known, triaged
advisory, suppress it explicitly by its ID in the audit command (`Makefile` `audit` target and the
CI step):

```bash
pip-audit --no-deps -r /dev/stdin --ignore-vuln GHSA-xxxx-xxxx-xxxx
```

## Release Compatibility Criteria

Every release should satisfy these checks:

- All public REST endpoints are documented by generated OpenAPI schema.
- Existing request and response shapes remain backwards compatible unless the change fixes a
  release-blocking correctness or security issue.
- Tenant isolation, application grants, idempotency, and soft-delete behavior are covered by tests.
- New migrations are committed and `makemigrations --check --dry-run` is clean.
- Health endpoints remain unauthenticated.
- Service-token auth remains required for tenant-owned API paths.
- Outbox dispatch has a documented retry path.

## Production Configuration Checklist

Set these environment variables explicitly outside local development:

- `DJANGO_DEBUG=false`
- `DJANGO_SECRET_KEY=<non-default-secret>`
- `DATABASE_URL=postgres://...`
- `ALLOWED_HOSTS=<comma-separated-hostnames>`
- `SERVICE_TOKEN_PEPPER=<non-default-secret-pepper>`
- `OCTONOMY_API_VERSION` — **leave unset.** It defaults to the version the build was cut from, so it
  tracks upgrades on its own. Set it only to deliberately advertise a different schema version.
- `LOG_LEVEL=INFO`
- `MAX_BULK_TAGS=200` or a deployment-specific cap

Run Django deploy checks as part of deployment validation:

```bash
python manage.py check --deploy
```

## Migration And Rollback Notes

- Apply migrations before serving the new application version:

```bash
python manage.py migrate
```

- Back up PostgreSQL before release migrations in shared environments.
- Rollbacks should restore both application code and database state when migrations are not safely
  reversible.
- Service tokens are shown only once at creation time. Rotate by creating a replacement token,
  updating callers, and revoking the old prefix.

## Smoke Test

After deployment, run these minimum checks:

```bash
curl -f https://<host>/health/live
curl -f https://<host>/health/ready
curl -f https://<host>/api/schema/        # v2 (default/advertised)
curl -f https://<host>/api/v1/schema/     # v1 (still supported)
```

Then use a real service token to verify tenant-scoped reads and a non-production tenant mutation.
