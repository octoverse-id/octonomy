# Release Process

## Current Target

Octonomy is preparing the `1.0.0-rc.1` release candidate. The REST API under `/api/v1` is the
release surface for v1. GraphQL, external broker transports, persisted counters, and external JWT
or API gateway auth remain future phases.

## Versioning

- Python package metadata uses PEP 440 format: `1.0.0rc1`.
- OpenAPI and user-facing docs use SemVer prerelease format: `1.0.0-rc.1`.
- Set `OCTONOMY_API_VERSION` when a deployment should expose a different schema version string.

## Release Checklist

Before cutting a release candidate:

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

## Contract Freeze Criteria

A v1 release candidate should satisfy these checks:

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
- `OCTONOMY_API_VERSION=1.0.0-rc.1`
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
