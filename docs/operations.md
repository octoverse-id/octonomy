# Operations

## Runtime Shape

Octonomy is a Django REST service backed by PostgreSQL. It does not require an external message
broker for v1. Transactional outbox rows are stored in PostgreSQL and dispatched by a management
command using the built-in logging transport or the optional webhook transport.

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

## Namespace Schema Migrations

The namespace S1 migrations use Django-native nullable columns, check constraints, conditional
unique constraints, and ordinary indexes so the SQLite and PostgreSQL migration gates stay aligned.
They intentionally do not use raw PostgreSQL DDL or `CREATE INDEX CONCURRENTLY`.
No data backfill is required for existing rows because `namespace_type = null` and
`namespace_id = null` is the global scope.

Verify row counts and scope-invariant violations after migration:

```bash
python manage.py verify_namespace_scope
```

The uniqueness constraint swap is atomic and non-concurrent. Schedule a brief maintenance window
for production-sized tables, and measure row counts on `tag_assignments`, `audit_logs`, and
`outbox_events` before deployment. The down migration is cleanly reversible only while no
merchant-namespace rows exist; after merchant writes, rollback should use the namespace feature
flags rather than restoring the old global-only uniqueness constraints.

## Outbox Dispatcher

Dispatch pending events:

```bash
python manage.py dispatch_outbox_events --limit 100
```

Retry failed events:

```bash
python manage.py dispatch_outbox_events --limit 100 --retry-failed
```

The command prints:

```text
published=<count> failed=<count> dead_lettered=<count> recovered=<count>
```

Dispatcher state:

- `pending`: ready for first delivery when `available_at <= now()`.
- `processing`: claimed by a dispatcher worker until `claim_expires_at`.
- `published`: delivered successfully.
- `failed`: retryable after backoff when `available_at <= now()` and `--retry-failed` is used.
- `dead_letter`: terminal failure after `OCTONOMY_OUTBOX_MAX_ATTEMPTS` delivery attempts.

Recommended scheduling for production-like environments:

- Run one dispatcher worker on a short interval, such as every minute.
- Keep `--limit` small enough that one run finishes comfortably before the next starts.
- Use the same application image and environment as the API service.
- Include `--retry-failed` in the scheduled command when automatic retry of failed events is
  desired.

The dispatcher uses row locking with `skip_locked` where supported, so multiple workers can safely
split eligible rows on PostgreSQL. Rows are claimed inside a short transaction, published outside
that transaction, and then marked with the result — completion only applies while the worker still
holds the claim token, so a stolen claim cannot mark a delivered event failed. A later run recovers
expired `processing` claims by returning them to `pending` for redelivery (an expired claim is not a
delivery failure), without incrementing delivery `attempts`; expired-claim recoveries are tracked in
`recoveries` and counted under `recovered`, never `failed`.

Configuration:

```text
OCTONOMY_OUTBOX_TRANSPORT=logging
OCTONOMY_OUTBOX_MAX_ATTEMPTS=5
OCTONOMY_OUTBOX_RETRY_BASE_SECONDS=30
OCTONOMY_OUTBOX_RETRY_MAX_SECONDS=3600
OCTONOMY_OUTBOX_CLAIM_TIMEOUT_SECONDS=60
```

`OCTONOMY_OUTBOX_RETRY_BASE_SECONDS` has a minimum effective value of 1 second.

Webhook transport:

```text
OCTONOMY_OUTBOX_TRANSPORT=webhook
OCTONOMY_WEBHOOK_URL=https://example.internal/octonomy-events
OCTONOMY_WEBHOOK_SIGNING_SECRET=<secret>
OCTONOMY_WEBHOOK_TIMEOUT_SECONDS=10
```

Webhook requests use `POST` with `Content-Type: application/json` and the same event JSON fields
as the logging transport. Requests include `X-Octonomy-Event-ID`, `X-Octonomy-Event-Type`,
`X-Octonomy-Tenant-ID`, optional `X-Octonomy-Request-ID`, and `X-Octonomy-Signature`.
The signature value is `sha256=<hex digest>` where the digest is HMAC SHA-256 over the request
body using `OCTONOMY_WEBHOOK_SIGNING_SECRET`. `OCTONOMY_WEBHOOK_URL` must be an absolute `http`
or `https` URL, webhook dispatch does not follow redirects, and
`OCTONOMY_OUTBOX_CLAIM_TIMEOUT_SECONDS` must be greater than
`OCTONOMY_WEBHOOK_TIMEOUT_SECONDS`.

## Outbox Inspection

Pending or retryable events:

```sql
select id, tenant_id, event_type, aggregate_type, aggregate_id,
       status, attempts, recoveries, available_at
from outbox_events
where status in ('pending', 'failed')
order by available_at asc
limit 50;
```

Expired claims:

```sql
select id, tenant_id, event_type, attempts, recoveries, claimed_at, claim_expires_at
from outbox_events
where status = 'processing' and claim_expires_at <= now()
order by claim_expires_at asc
limit 50;
```

Retryable failed events:

```sql
select id, tenant_id, event_type, attempts, recoveries, last_error, available_at
from outbox_events
where status = 'failed'
order by available_at asc
limit 50;
```

Dead-lettered events:

```sql
select id, tenant_id, event_type, attempts, recoveries, last_error, updated_at
from outbox_events
where status = 'dead_letter'
order by updated_at desc
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
