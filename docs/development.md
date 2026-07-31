# Development

Requires **Python 3.12+** and **Django 5.2 LTS**. PostgreSQL is the supported database.

## Setup

```bash
cp .env.example .env
make install
make db-up
make migrate
make seed
make run
```

With `DJANGO_DEBUG=true` (the local default), the optional admin console is enabled at
`http://localhost:8000/admin/`. Create a superuser to sign in:

```bash
python manage.py createsuperuser
```

See [operations](operations.md) for enabling it in production.

## API and Docs (local)

`/api/v2` is the primary, advertised surface (it adds the namespace axis); `/api/v1` is **still
supported — not deprecated**. Interactive docs are served locally at:

```text
http://localhost:8000/api/docs/swagger/    # v1 + v2 dropdown (opens on v2)
http://localhost:8000/api/docs/redoc/       # v2 (default); v1 at /api/docs/v1/redoc/
http://localhost:8000/api/schema/           # v2 schema (default); v1 at /api/v1/schema/
```

See the [API reference](api.md) for the namespace surface and the [versioning policy](versioning.md)
for how the two surfaces coexist.

## Quality Gates

Run the narrow checks while developing:

```bash
make lint
make check
make migration-check
make test
make openapi-check
```

Before opening a PR, run the combined gate (lint, checks, migrations, tests, OpenAPI drift,
dependency audit, and version-check):

```bash
make release-check
```

For the SemVer bump rules and the full release process, see the
[versioning policy](versioning.md) and the [release runbook](release.md).

For local SQLite coverage that mirrors the CI SQLite job:

```bash
make test-sqlite
```

## Environment

- `DATABASE_URL`: PostgreSQL connection URL. Production deployments should use PostgreSQL, not
  SQLite.
- `DJANGO_SECRET_KEY`: Django signing secret. Use a non-default value when `DJANGO_DEBUG=false`.
- `DJANGO_DEBUG`: set to `false` outside local development.
- `ALLOWED_HOSTS`: comma-separated allowed hostnames. Do not use `*` in production.
- `SERVICE_TOKEN_PEPPER`: HMAC pepper used to hash service API tokens. Use a non-default,
  secret value outside local development.
- `OCTONOMY_API_VERSION`: version string exposed in generated OpenAPI metadata.
- `MAX_BULK_TAGS`: maximum number of tags accepted by bulk endpoints.
- `LOG_LEVEL`: structured logging level.
- `OCTONOMY_ADMIN_ENABLED`: mount the superuser-only admin at `/admin/`. Defaults to `DJANGO_DEBUG`
  (on locally, off in production unless explicitly enabled).
- `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`: default to true when `DJANGO_DEBUG=false`; set
  explicitly to override for unusual deployments.

## Service Tokens

Create a local token after migrations:

```bash
python manage.py create_service_token \
  --name svc-catalog \
  --tenant tenant_demo \
  --application commerce \
  --scope tags:read \
  --scope tags:write \
  --scope audit:read \
  --metadata '{"owner":"platform"}'
```

Omitting namespace arguments creates a global-only grant. Add `--namespace-type` and
`--namespace-id` together for an exact merchant/sub-tenant grant, or use `--namespace-wildcard`
only for a trusted backend that is authorized to select namespaces inside the grant's
tenant/application boundary.

The raw token is printed once. Revoke a token by prefix:

```bash
python manage.py revoke_service_token --prefix <key-prefix>
```

## Audit Development

Use `X-Actor-ID` on mutation requests to make audit rows easier to inspect during local testing.
When `X-Actor-ID` is omitted, audit logs use the authenticated service client name. Assignment
APIs fall back to `assigned_by` only when no service client identity is available.

## Outbox Development

Dispatch pending outbox events with the default local logging transport:

```bash
python manage.py dispatch_outbox_events --limit 100
python manage.py dispatch_outbox_events --limit 100 --retry-failed
```

For local webhook testing, set:

```text
OCTONOMY_OUTBOX_TRANSPORT=webhook
OCTONOMY_WEBHOOK_URL=http://localhost:9000/octonomy-events
OCTONOMY_WEBHOOK_SIGNING_SECRET=local-webhook-secret
OCTONOMY_WEBHOOK_TIMEOUT_SECONDS=10
OCTONOMY_OUTBOX_CLAIM_TIMEOUT_SECONDS=60
```

The default transport logs the event payload and marks successful rows as `published`. Failed rows
are marked `failed` with delivery `attempts` and `last_error` populated for inspection. Expired
claims are tracked separately in `recoveries`.

## Seed Data

`make seed` creates demo vocabularies, tags, aliases, assignments, and a tenant-wide `svc-demo`
service token for tenant `tenant_demo`. Store the printed token immediately; Octonomy stores only
the token hash and prefix.

The default vocabularies are shared `labels`, commerce `product-labels`, and CMS
`content-labels`. The default aliases include `promoted` and `hero` for `featured`, `discount`
and `promo` for `sale`, and `urgent` for `breaking-news`.
