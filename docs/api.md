# API Notes

Base path: `/api/v1`

Required headers for tenant-owned endpoints:

```text
Authorization: Bearer <service-token>
X-Tenant-ID: tenant_demo
```

Service tokens are created by operators with `python manage.py create_service_token`. The command
accepts optional application, namespace, and `--metadata '<json-object>'` arguments. Tokens are
scoped by tenant, optional application, optional namespace restriction, and API scopes:

- `tags:read`: read tag, vocabulary, assignment, and resource tag endpoints.
- `tags:write`: mutate tags, vocabularies, and assignments.
- `audit:read`: read audit log endpoints.

A grant with `application_id = null` allows all applications in that tenant. A grant with a
specific `application_id` only allows requests that supply the same application scope.

Namespace grants are fail-closed:

- Omitting namespace arguments creates a global-only grant.
- `--namespace-type <type> --namespace-id <id>` creates an exact namespace grant and requires
  `--application`.
- `--namespace-wildcard` is an explicit broad grant across global and namespaced requests. It may
  be tenant-wide or application-specific.

Example exact merchant grant:

```bash
python manage.py create_service_token \
  --name svc-merchant-a \
  --tenant tenant_demo \
  --application commerce \
  --namespace-type merchant \
  --namespace-id merchant_a \
  --scope tags:read \
  --scope tags:write
```

## Namespace Trust Model

Octonomy enforces exact namespace grants against the request namespace. A token restricted to
`merchant/merchant_a` cannot access global data or another merchant namespace. Legacy grants with
null namespace fields remain global-only; null is not a namespace wildcard.

An explicit namespace-wildcard grant authorizes the client to assert any namespace inside the
grant's tenant and optional application boundary. Under that broad grant, namespace selection is
caller-asserted partitioning: the client backend is responsible for authenticating the merchant
and sending the correct namespace. Do not expose service tokens to browsers or mobile clients.
Use exact per-merchant grants for untrusted tiers and reserve wildcard grants for trusted backend
services.

Optional mutation audit actor header:

```text
X-Actor-ID: svc-catalog
```

When `X-Actor-ID` is omitted, audit logs use the authenticated service client name.

Tag responses include `usage_count`, computed from current tag assignments, and
`vocabulary_id` when the tag belongs to a vocabulary.

Tag aliases are alternate tenant-scoped names for canonical tags. Alias read endpoints require
`tags:read`; alias mutation endpoints require `tags:write`. Aliases can only point at active
canonical tags, and deactivating a tag also deactivates its aliases.

Errors use this shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": {},
    "request_id": "req_..."
  }
}
```

Audit endpoints:

```text
GET /api/v1/audit-logs
GET /api/v1/tags/{tag_id}/audit-logs
GET /api/v1/resources/{resource_type}/{resource_id}/audit-logs
```

`GET /api/v1/audit-logs` supports filters for `application_id`, `action`, `entity_type`,
`entity_id`, `tag_id`, `resource_type`, `resource_id`, `actor_id`, and `operation_id`.
Vocabulary mutations emit `vocabulary.created`, `vocabulary.updated`, and
`vocabulary.deactivated` audit actions with `entity_type = "vocabulary"`.
Alias mutations emit `tag_alias.created`, `tag_alias.updated`, and `tag_alias.deactivated` audit
actions with `entity_type = "tag_alias"`.

Vocabulary endpoints:

```text
GET /api/v1/vocabularies
POST /api/v1/vocabularies
GET /api/v1/vocabularies/{vocabulary_id}
PATCH /api/v1/vocabularies/{vocabulary_id}
DELETE /api/v1/vocabularies/{vocabulary_id}
```

Vocabularies are tenant-scoped and may be shared across applications by leaving
`application_id` null. Application-specific vocabularies may only contain tags for the same
application. Shared tags can only belong to shared vocabularies.

`GET /api/v1/vocabularies` supports `application_id`, `include_shared`, `slug`, `is_active`,
`q`, `limit`, and `offset`.

Tag endpoints:

```text
GET /api/v1/tags?vocabulary_id={vocabulary_id}
POST /api/v1/tags
PATCH /api/v1/tags/{tag_id}
```

`POST` and `PATCH` accept optional `vocabulary_id`. `GET /api/v1/tags` supports filtering by
`vocabulary_id`.

Alias and resolution endpoints:

```text
GET /api/v1/tag-aliases
POST /api/v1/tag-aliases
GET /api/v1/tag-aliases/{alias_id}
PATCH /api/v1/tag-aliases/{alias_id}
DELETE /api/v1/tag-aliases/{alias_id}
GET /api/v1/tags/{tag_id}/aliases
GET /api/v1/tag-resolution?slug={slug}&type={type}&application_id={application_id}
```

`POST /api/v1/tag-aliases` accepts `application_id`, `tag_id`, `name`, `slug`, `metadata`, and
`is_active`. `GET /api/v1/tag-aliases` supports `application_id`, `include_shared`, `tag_id`,
`slug`, `is_active`, `q`, `limit`, and `offset`.
`GET /api/v1/tag-resolution` resolves active canonical tags first, then active aliases whose
canonical tag is also active. Without `application_id`, only shared tags and aliases are resolved.
If multiple active canonical tags share the same slug across different tag types, provide `type`
to disambiguate.

Assignment writes can use aliases:

```json
{
  "application_id": "commerce",
  "alias_slug": "promo",
  "resource_type": "product",
  "resource_id": "prod_123"
}
```

`POST /tag-assignments` accepts exactly one of `tag_id`, `alias_id`, or `alias_slug`.
Bulk assign and resource replace accept `tag_ids`, `alias_slugs`, or both.

Paginated list endpoints use:

```json
{
  "data": [],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "count": 0,
    "next": null,
    "previous": null
  }
}
```
