# API Notes

Base path: `/api/v1`

Required headers for tenant-owned endpoints:

```text
Authorization: Bearer <service-token>
X-Tenant-ID: tenant_demo
```

Service tokens are created by operators with `python manage.py create_service_token`. The command
accepts optional `--metadata '<json-object>'` for operator-owned client metadata. Tokens are scoped
by tenant, optional application, and scopes:

- `tags:read`: read tag, vocabulary, assignment, and resource tag endpoints.
- `tags:write`: mutate tags, vocabularies, and assignments.
- `audit:read`: read audit log endpoints.

A grant with `application_id = null` allows all applications in that tenant. A grant with a
specific `application_id` only allows requests that supply the same application scope.

Optional mutation audit actor header:

```text
X-Actor-ID: svc-catalog
```

When `X-Actor-ID` is omitted, audit logs use the authenticated service client name.

Tag responses include `usage_count`, computed from current tag assignments, and
`vocabulary_id` when the tag belongs to a vocabulary.

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
