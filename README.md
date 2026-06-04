# Octonomy

[![CI](https://github.com/octoverse-id/octonomy/actions/workflows/ci.yml/badge.svg)](https://github.com/octoverse-id/octonomy/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Octonomy is a standalone, multi-tenant, multi-application tag management and taxonomy service.

It stores vocabularies, tags, and tag assignments for external resources such as articles,
images, orders, products, and documents. Octonomy does not own or duplicate external resource
data.

> **Project status:** early and evolving (`0.1.0`). APIs may change before `1.0`.

## Stack

- Python 3.10+
- Django
- Django REST Framework
- PostgreSQL
- drf-spectacular for OpenAPI
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

API base URL:

```text
http://localhost:8000/api/v1
```

Health checks:

```text
GET /health/live
GET /health/ready
```

OpenAPI schema:

```text
GET /api/schema/
GET /api/docs/swagger/
GET /api/docs/redoc/
```

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
make format
make openapi
```

## API Examples

Create a shared vocabulary:

```bash
curl -X POST http://localhost:8000/api/v1/vocabularies \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Labels","slug":"labels","metadata":{}}'
```

Create a shared tag:

```bash
curl -X POST http://localhost:8000/api/v1/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Featured","slug":"featured","type":"label","metadata":{}}'
```

Create an application-specific tag in a vocabulary:

```bash
curl -X POST http://localhost:8000/api/v1/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","vocabulary_id":"<vocabulary-uuid>","name":"Sale","slug":"sale","type":"label","metadata":{}}'
```

Create an alias for a tag:

```bash
curl -X POST http://localhost:8000/api/v1/tag-aliases \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_id":"<tag-uuid>","name":"Promo","slug":"promo","metadata":{}}'
```

Resolve a tag or alias slug:

```bash
curl "http://localhost:8000/api/v1/tag-resolution?slug=promo&application_id=commerce" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

List tags in a vocabulary:

```bash
curl "http://localhost:8000/api/v1/tags?vocabulary_id=<vocabulary-uuid>" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

Assign a tag to a resource:

```bash
curl -X POST http://localhost:8000/api/v1/tag-assignments \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_id":"<tag-uuid>","resource_type":"product","resource_id":"prod_123","assigned_by":"svc-catalog"}'
```

Replace all tags for a resource:

```bash
curl -X POST http://localhost:8000/api/v1/resources/product/prod_123/tags \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_ids":["<tag-uuid>"],"assigned_by":"svc-catalog"}'
```

List resources assigned to a tag:

```bash
curl "http://localhost:8000/api/v1/tags/<tag-uuid>/resources?application_id=commerce" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

List audit logs:

```bash
curl "http://localhost:8000/api/v1/audit-logs?action=assignment.created" \
  -H "Authorization: Bearer <service-token>" \
  -H "X-Tenant-ID: tenant_demo"
```

## Documentation

- [Architecture](docs/architecture.md) — system design, layers, and extension points.
- [API reference](docs/api.md) — endpoints, scopes, errors, and pagination.
- [Development](docs/development.md) — local setup, environment variables, and service tokens.

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions,
and the PR process, and our [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a vulnerability? Please report it privately — see [SECURITY.md](SECURITY.md). Do not open a
public issue for security problems.

## License

Octonomy is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for
attribution.
