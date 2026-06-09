# Release Process

## Current Target

Octonomy `1.0.0` is the stable release of the REST v1 contract. The REST API under `/api/v1` is the
release surface for v1 and follows Semantic Versioning. GraphQL, external broker transports,
persisted counters, and external JWT or API gateway auth remain future phases.

## Versioning

Octonomy follows Semantic Versioning. Bug fixes are a patch, backward-compatible additions are a
minor (additive on `/api/v1`), and breaking changes ship a new `/api/v2` plus a major bump. See
[`versioning.md`](versioning.md) for the full policy and what counts as breaking.

- Package metadata uses PEP 440 (`1.0.0`); OpenAPI and user-facing docs use SemVer (`1.0.0`).
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
   - `.env.example` and the production checklist below (`OCTONOMY_API_VERSION`)
   - regenerate the schema with `make openapi` (updates `docs/openapi.yaml` `info.version`)
   - refresh the lock with `uv lock`
3. Update `CHANGELOG.md`: move the `[Unreleased]` entries under `## [<version>] - <date>`, add the
   `[<version>]` compare link, and reset `[Unreleased]` to `compare/v<version>...HEAD`.
4. Run the gates, then open the PR with `Closes #<issue>`:

   ```bash
   make release-check   # lint, checks, migrations, tests, openapi drift, audit, version-check
   ```

5. After merge and green CI, tag the merge commit and publish the release:

   ```bash
   git tag -a v<version> -m "Octonomy <version>" <merge-commit>
   git push origin v<version>
   gh release create v<version> --title "v<version>" --notes-file <notes>
   ```

6. Close the tracking issue and delete the merged branch.

> `gh` caveats (observed on `gh 2.4.0`): `release create` has no `--latest` / `--verify-tag` — a
> published, non-prerelease release is "Latest" by default. `gh issue close` has no `--comment`;
> post the comment separately with `gh issue comment` before closing.

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
- `OCTONOMY_API_VERSION=1.0.0`
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
curl -f https://<host>/api/schema/
```

Then use a real service token to verify tenant-scoped reads and a non-production tenant mutation.
