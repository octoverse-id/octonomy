# API Notes

Base path: `/api/v1`

Required headers for tenant-owned endpoints:

```text
Authorization: Bearer dev-token
X-Tenant-ID: tenant_demo
```

Optional mutation audit actor header:

```text
X-Actor-ID: svc-catalog
```

Tag responses include `usage_count`, computed from current tag assignments.

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
