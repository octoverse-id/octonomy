# Operations

## Runtime Shape

Octonomy is a Django REST service backed by PostgreSQL. It does not require an external message
broker for v1. Transactional outbox rows are stored in PostgreSQL and dispatched by a management
command using the built-in logging transport.

## Health Checks

Unauthenticated health endpoints:

```text
GET /health/live
GET /health/ready
```

`/health/live` confirms the process is serving HTTP. `/health/ready` checks database connectivity
and should be used for readiness gates.

## Logging And Correlation

Logs are structured JSON. Clients may send `X-Request-ID`; otherwise Octonomy generates a request
id. Mutation APIs also carry `operation_id` through audit logs and outbox events. Use these fields
together when investigating a request:

- `request_id`
- `operation_id`
- `tenant_id`
- `application_id`
- `actor_id`
- `tag_id`
- `resource_type`
- `resource_id`

## Service Token Operations

Create a service token:

```bash
python manage.py create_service_token \
  --name svc-catalog \
  --tenant tenant_demo \
  --application commerce \
  --scope tags:read \
  --scope tags:write \
  --scope audit:read
```

The raw token is printed once. Store it in the calling service secret manager immediately. Octonomy
stores only a keyed hash and prefix.

Revoke a token by prefix:

```bash
python manage.py revoke_service_token --prefix <key-prefix>
```

Rotation pattern:

1. Create a new token with equivalent or narrower grants.
2. Deploy the caller with the new token.
3. Confirm successful traffic with the new prefix.
4. Revoke the old prefix.

Changing `SERVICE_TOKEN_PEPPER` invalidates existing tokens because token hashes can no longer be
verified. Rotate all service tokens when changing the pepper.

## Outbox Dispatcher

Dispatch pending events:

```bash
python manage.py dispatch_outbox_events --limit 100
```

Retry failed events:

```bash
python manage.py dispatch_outbox_events --limit 100 --retry-failed
```

Recommended scheduling for production-like environments:

- Run one dispatcher worker on a short interval, such as every minute.
- Keep `--limit` small enough that one run finishes comfortably before the next starts.
- Use the same application image and environment as the API service.

The dispatcher uses row locking with `skip_locked` where supported, so multiple workers can safely
split pending rows on PostgreSQL.

## Outbox Inspection

Pending events:

```sql
select id, tenant_id, event_type, aggregate_type, aggregate_id, attempts, available_at
from outbox_events
where status = 'pending'
order by available_at asc
limit 50;
```

Failed events:

```sql
select id, tenant_id, event_type, attempts, last_error, available_at
from outbox_events
where status = 'failed'
order by available_at asc
limit 50;
```

Published event timeline for a tenant:

```sql
select id, event_type, aggregate_type, aggregate_id, published_at
from outbox_events
where tenant_id = '<tenant-id>' and status = 'published'
order by published_at desc
limit 50;
```

## Backup And Recovery

Back up PostgreSQL before release migrations in shared environments. The database contains the
canonical state for tags, vocabularies, aliases, assignments, service clients, audit logs, and
outbox events.

For restore drills, verify:

- service-token authentication still works for a known non-production client;
- tag and assignment queries return expected tenant-scoped data;
- audit logs and outbox events remain queryable;
- the dispatcher can publish or retry pending events after restore.
