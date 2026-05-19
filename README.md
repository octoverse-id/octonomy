# Octonomy

Octonomy is a standalone, multi-tenant, multi-application tag management and taxonomy service.

It stores tags and tag assignments for external resources such as articles, images, orders,
products, and documents. Octonomy does not own or duplicate external resource data.

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
Authorization: Bearer dev-token
X-Tenant-ID: tenant_demo
```

The bearer token is a development placeholder. `X-Tenant-ID` is the source of truth for tenant
isolation.

## Common Commands

```bash
make test
make lint
make format
make openapi
```

## API Examples

Create a shared tag:

```bash
curl -X POST http://localhost:8000/api/v1/tags \
  -H "Authorization: Bearer dev-token" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Featured","slug":"featured","type":"label","metadata":{}}'
```

Create an application-specific tag:

```bash
curl -X POST http://localhost:8000/api/v1/tags \
  -H "Authorization: Bearer dev-token" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","name":"Sale","slug":"sale","type":"label","metadata":{}}'
```

Assign a tag to a resource:

```bash
curl -X POST http://localhost:8000/api/v1/tag-assignments \
  -H "Authorization: Bearer dev-token" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_id":"<tag-uuid>","resource_type":"product","resource_id":"prod_123","assigned_by":"svc-catalog"}'
```

Replace all tags for a resource:

```bash
curl -X POST http://localhost:8000/api/v1/resources/product/prod_123/tags \
  -H "Authorization: Bearer dev-token" \
  -H "X-Tenant-ID: tenant_demo" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","tag_ids":["<tag-uuid>"],"assigned_by":"svc-catalog"}'
```

List resources assigned to a tag:

```bash
curl "http://localhost:8000/api/v1/tags/<tag-uuid>/resources?application_id=commerce" \
  -H "Authorization: Bearer dev-token" \
  -H "X-Tenant-ID: tenant_demo"
```
