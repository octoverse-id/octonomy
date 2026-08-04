# Octonomy

[![CI](https://github.com/octoverse-id/octonomy/actions/workflows/ci.yml/badge.svg)](https://github.com/octoverse-id/octonomy/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

Octonomy is a standalone, multi-tenant, multi-application tag management and taxonomy service.

It stores vocabularies, tags, aliases, tag assignments, audit logs, and transactional outbox events
for external resources such as articles, images, orders, products, and documents. Octonomy does not
own or duplicate external resource data.

> **Project status:** Stable and following
> [Semantic Versioning](https://semver.org/spec/v2.0.0.html). `/api/v2` is the primary, advertised
> API surface (it adds the namespace axis); `/api/v1` remains **fully supported — not deprecated**.
> Breaking changes are never made in place: they ship as a new URL-versioned surface alongside the
> existing one.

## Stack

- Python 3.12+
- Django 5.2 LTS
- Django REST Framework
- PostgreSQL
- drf-spectacular for OpenAPI
- django-unfold for the optional admin console
- pytest and ruff for tests/linting

## Local Development

```bash
cp .env.example .env
make install
make db-up
make migrate
make seed
make run
```

`make seed` prints a demo `svc-demo` service token for `tenant_demo`. Store that token from the
terminal output; it cannot be retrieved later.

API base URL (`/api/v2` is the primary surface; `/api/v1` is still supported):

```text
http://localhost:8000/api/v2
http://localhost:8000/api/v1
```

Health checks:

```text
GET /health/live
GET /health/ready
```

OpenAPI schema and docs (the default routes serve **v2**; v1 stays browsable at its own routes):

```text
GET /api/schema/          # v2 (default)
GET /api/v1/schema/       # v1
GET /api/v2/schema/       # v2
GET /api/docs/swagger/    # v1 + v2 via the "Select a definition" dropdown (opens on v2)
GET /api/docs/redoc/      # v2 (default)
GET /api/docs/v1/redoc/   # v1
GET /api/docs/v2/redoc/   # v2
```

## Deployment

Octonomy is self-hosted — run it on your own infrastructure. The
[deployment guide](docs/deployment.md) covers three paths — **Docker Compose**, **Kubernetes**, and a
**VPS / systemd** box — with copy-pasteable example configs under [`deploy/`](deploy). Every path runs
the same three processes from one image: the Gunicorn API server, a one-shot `migrate`, and the outbox
dispatcher.

## Admin Console (optional)

An optional, superuser-only admin console (themed with django-unfold) is available at `/admin/` as a
thin operator interface over the REST service — REST remains the primary API. It is mounted only when
`OCTONOMY_ADMIN_ENABLED` is true, which **defaults to `DJANGO_DEBUG`** (on in local development, off
in production unless you explicitly enable it). Bootstrap access with a superuser:

```bash
python manage.py migrate
python manage.py createsuperuser
# with DEBUG=true (or OCTONOMY_ADMIN_ENABLED=true):
make run   # then visit http://localhost:8000/admin/
```

Enabling it in production is a deliberate operator choice — see the
[operations guide](docs/operations.md) for the HTTPS/secrets/`collectstatic` checklist.

## Authentication and Tenant Scope

All tenant-owned API requests require:

```text
Authorization: Bearer <service-token>
X-Tenant-ID: tenant_demo
```

Mutation requests may also include:

```text
X-Actor-ID: svc-catalog
```

Create a local service token with tenant/application grants:

```bash
python manage.py create_service_token \
  --name svc-catalog \
  --tenant tenant_demo \
  --application commerce \
  --scope tags:read \
  --scope tags:write \
  --scope audit:read
```

The token is printed once. Octonomy stores only its keyed hash and prefix. `X-Tenant-ID` is the
source of truth for tenant isolation, and the authenticated service token must be granted access
to that tenant and any supplied `application_id`. `X-Actor-ID` is optional; audit logs otherwise
use the authenticated service client name.

## Common Commands

```bash
make test
make lint
make check
make migration-check
make openapi-check
make release-check
```

## API Examples

These examples use the primary `/api/v2` surface in the **global** namespace (no `X-Namespace-*`
headers). The same paths exist under `/api/v1`, which stays supported. To scope a request to a
merchant/sub-tenant namespace, add the `X-Namespace-Type`/`X-Namespace-ID` headers on `/api/v2` —
see the [API reference](docs/api.md) for the namespace surface.

Create a shared vocabulary:

```bash
curl -X POST http://localhost:8000/api/v2/vocabularies \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Labels","slug":"labels","metadata":{}}'
```

Create a shared tag:

```bash
curl -X POST http://localhost:8000/api/v2/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Featured","slug":"featured","type":"label","metadata":{}}'
```

Create an application-specific tag in a vocabulary:

```bash
curl -X POST http://localhost:8000/api/v2/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","vocabulary_id":"<vocabulary-uuid>","name":"Sale","slug":"sale","type":"label","metadata":{}}'
```

Create an alias for a tag:

```bash
curl -X POST http://localhost:8000/api/v2/tag-aliases \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_id":"<tag-uuid>","name":"Promo","slug":"promo","metadata":{}}'
```

Resolve a tag or alias slug:

```bash
curl "http://localhost:8000/api/v2/tag-resolution?slug=promo&application_id=commerce" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

List tags in a vocabulary:

```bash
curl "http://localhost:8000/api/v2/tags?vocabulary_id=<vocabulary-uuid>" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

Assign a tag to a resource:

```bash
curl -X POST http://localhost:8000/api/v2/tag-assignments \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_id":"<tag-uuid>","resource_type":"product","resource_id":"prod_123","assigned_by":"svc-catalog"}'
```

Replace all tags for a resource:

```bash
curl -X POST http://localhost:8000/api/v2/resources/product/prod_123/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_ids":["<tag-uuid>"],"assigned_by":"svc-catalog"}'
```

List resources assigned to a tag:

```bash
curl "http://localhost:8000/api/v2/tags/<tag-uuid>/resources?application_id=commerce" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

List audit logs:

```bash
curl "http://localhost:8000/api/v2/audit-logs?action=assignment.created" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

## Documentation

- [Architecture](docs/architecture.md) — system design, layers, and extension points.
- [API reference](docs/api.md) — endpoints, scopes, errors, and pagination.
- [Development](docs/development.md) — local setup, environment variables, and service tokens.
- [Deployment](docs/deployment.md) — self-hosting on Docker, Kubernetes, or a VPS (ready-to-use configs in `deploy/`).
- [Operations](docs/operations.md) — health, logging, namespace rollout, and the outbox runbook.
- [Versioning](docs/versioning.md) — SemVer policy, bump rules, and how `/api/v2` (primary) and `/api/v1` (supported) coexist.
- [Release process](docs/release.md) — pre-release gates and the "Cutting a Release" runbook.
- [Changelog](CHANGELOG.md) — notable changes by release.

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions,
and the PR process, and our [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md). Do not open a
public issue for security problems.

## License

Octonomy is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for
attribution.
