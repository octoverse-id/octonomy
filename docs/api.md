# API Notes

Base path: `/api/v1`

Required headers for tenant-owned endpoints:

```text
Authorization: Bearer dev-token
X-Tenant-ID: tenant_demo
```

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
