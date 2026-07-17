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

## Namespace Rollout & Operations

The merchant/sub-tenant namespace layer is gated by five env-backed feature flags. They are Django
settings read from environment variables at startup, so **a flag change takes effect on
restart/redeploy — rollback latency equals deploy latency.** There is no runtime toggle.

### Feature flags

| Environment variable | Setting | Meaning | Default |
| --- | --- | --- | --- |
| `OCTONOMY_NAMESPACE_SCHEMA_ENABLED` | `NAMESPACE_SCHEMA_ENABLED` | S1 namespace columns/constraints are applied | `true` |
| `OCTONOMY_NAMESPACE_READ_ENABLED` | `NAMESPACE_READ_ENABLED` | namespace-aware reads are live | `true` |
| `OCTONOMY_NAMESPACE_AUTH_ENFORCED` | `NAMESPACE_AUTH_ENFORCED` | namespace is enforced against service-token grants | `true` |
| `OCTONOMY_NAMESPACE_V2_API_ENABLED` | `NAMESPACE_V2_API_ENABLED` | the namespaced `/api/v2` surface is served | `true` |
| `OCTONOMY_NAMESPACE_WRITE_ENABLED` | `NAMESPACE_WRITE_ENABLED` | namespaced rows may be persisted (kill switch) | `false` |

The read/auth machinery is always fail-closed; `SCHEMA`/`READ`/`AUTH` are rollout-phase assertions
the dependency check orders. `NAMESPACE_V2_API_ENABLED` is the only flag that gates the edge: when
off, a **namespaced** v2 request is refused with `503 namespace_api_disabled` while global v1/v2
traffic continues. `NAMESPACE_WRITE_ENABLED` is enforced on **every** write path — HTTP, management
commands, and any background writer — not only HTTP routing.

### Dependency contract (enforced at boot)

A Django system check refuses to start on an invalid combination, so an unsafe configuration is
caught by `python manage.py check` in CI/deploy rather than in production. Rules (error ids in
parentheses):

- `READ` requires `SCHEMA` (E010); `AUTH` requires `READ` (E011).
- `WRITE` requires `SCHEMA` (E012) **and** `READ` (E013).
- `V2_API` requires `READ` (E014) **and** `AUTH` (E015).
- **(deploy check only)** `WRITE` requires the S1 constraint-swap migrations applied (E016), so the
  headline "two merchants, same slug" case cannot fail with duplicate-key errors. This check is
  deploy-tagged (`manage.py check --deploy`) so it never runs during `manage.py migrate`.

E013 is the safety rule: it makes **`WRITE` on with `READ` off unbootable** — the "v2 accepting
namespaced writes that nobody can read" configuration cannot start.

### Rollout sequence (enable)

Rehearse the full sequence in staging (below) before production. Enable one flag per deploy and
verify before the next:

1. **Expand + constraint swap** — apply the S1 migrations (`manage.py migrate`), then
   `OCTONOMY_NAMESPACE_SCHEMA_ENABLED=true`. Verify with `python manage.py verify_namespace_scope`.
2. **Read** — `OCTONOMY_NAMESPACE_READ_ENABLED=true`. Namespace-aware reads live; global reads
   unchanged.
3. **Auth** — `OCTONOMY_NAMESPACE_AUTH_ENFORCED=true`. Namespace enforced against restricted grants.
4. **v2** — `OCTONOMY_NAMESPACE_V2_API_ENABLED=true`. The namespaced v2 surface accepts merchant
   reads. **Dashboards/metrics must be live before this step** (see below).
5. **Write (last)** — `OCTONOMY_NAMESPACE_WRITE_ENABLED=true`, only after the deploy check (E016)
   confirms the constraint swap is applied. Merchant writes are now accepted.

### Rollback ladder (disable)

Disable in this order; **columns and `SCHEMA` stay** (no data is removed, no migration is reversed):

1. **`OCTONOMY_NAMESPACE_V2_API_ENABLED=false`** — first. Namespaced v2 requests get
   `503 namespace_api_disabled`; merchant traffic stops immediately while global clients keep
   working.
2. **`OCTONOMY_NAMESPACE_AUTH_ENFORCED=false`**.
3. **`OCTONOMY_NAMESPACE_WRITE_ENABLED=false`** — writes go back to global-only. Must come **before**
   turning reads off (the E013 check enforces this ordering: writes off before reads off).
4. **`OCTONOMY_NAMESPACE_READ_ENABLED=false`**.

Never roll back by widening visibility — namespaced rows must never become visible through a global
(v1) read. Writes-off-first, then withdraw the surface; the data stays partitioned and simply
becomes unreachable until re-enablement.

### Dashboards & metrics (must be live before v2 enablement)

Observability is structured JSON logs (no separate metrics backend). Build dashboards/alerts by
aggregating these fields in the log pipeline:

- **`request_completed`** (logger `octonomy.requests`), one line per request, carries: `version`,
  `namespace_type`, `namespace_id`, `status_code`, `error_code`, `duration_ms`. This single line
  covers most required signals:
  - *requests by version + namespace type* — group by `version`, `namespace_type`.
  - *endpoint latency* — `duration_ms` by `path`/`version`.
  - *4xx by mismatch reason* / *auth-deny reasons* — group by `error_code` (e.g.
    `namespace_invalid`, `namespace_not_supported`, `namespaced_writes_disabled`,
    `namespace_api_disabled`, `forbidden`, `tenant_mismatch`, `application_mismatch`). Note
    `error_code = conflict` is a *conflict rate* signal covering both uniqueness collisions and
    business-rule conflicts (e.g. scope-move-blocked) — for duplicate-key precision use the
    dedicated metric below, not this code.
- **`namespace_conflict`** (logger `octonomy.metrics`), one line per duplicate-key collision on a
  namespace-aware unique constraint, with `entity` (`tag`/`tag_alias`/`vocabulary`) and
  `namespace_type`/`namespace_id`. This is *duplicate-key errors on the new constraints*, emitted
  only from the actual uniqueness-violation branches (never business-rule conflicts); `namespace_type`
  is null for a global-scope collision, so dashboards split global vs merchant cleanly.
- **`outbox_dispatch_summary`** (logger `octonomy.metrics`), one line per dispatcher run: run totals
  (`published`, `failed`, `dead_lettered`, `recovered`) and `lag_by_namespace_type` — per namespace
  type, the deliverable `backlog` count and `oldest_pending_seconds`. This is *outbox lag by
  namespace type*: a merchant namespace falling behind shows up independently of global traffic.
- *backfill-remaining per table* — not applicable: the NULL-global design carries no data backfill
  (existing rows are already global). Use `python manage.py verify_namespace_scope` for
  scope-invariant row counts.

Example (log pipeline, pseudo-query): count namespaced 4xx by reason —
`filter message=request_completed AND namespace_type!=null AND status_code>=400 | count by error_code`.

### `namespace_mismatch` spike response

A spike in namespaced 4xx (`error_code` in `namespace_invalid` / `namespace_not_supported`, or a rise
in `403 forbidden` on namespaced requests) is triaged by **spread**:

- **Concentrated on one `request_id` source / service client / `namespace_id`** → most likely a
  **misconfigured client**: a caller sending `X-Namespace-*` to `/api/v1`, a typo'd `namespace_type`
  (which strands that caller's data in an unreachable scope — caller responsibility, by design), or a
  token whose grant does not cover the namespace. Response: identify the client from `request_id` /
  token prefix, confirm the intended scope, and coordinate a client-side fix. No server change.
- **Spread across many clients / tenants / `namespace_id` values** → possible **probing/enumeration**.
  Response: cross-namespace object lookups already return `404` (no existence disclosure) and
  restricted grants fail closed, so isolation holds; rate-limit or block the source at the edge and
  review auth-deny reasons.

### Backfill verification

No data backfill is required (global scope is `null`/`null`, so existing rows are already global).
Confirm scope invariants after the S1 migration and constraint swap:

```bash
python manage.py verify_namespace_scope
```

### Post-deploy verification checklist

- `python manage.py check` passes (flag dependency contract is valid) and, on the deploy host,
  `python manage.py check --deploy` passes (constraint swap applied when writes are enabled).
- `GET /health/ready` returns healthy.
- `request_completed` and `outbox_dispatch_summary` are visible in the log pipeline and the
  namespace dashboards render.
- Smoke tests below pass against the deployed environment.

### Smoke tests

With a merchant-scoped service token (grant `tenant + application + namespace_type/namespace_id`):

```bash
BASE=https://api.example.com
TOK=<merchant-token>
NS=(-H "X-Tenant-ID: tenant_demo" -H "X-Namespace-Type: merchant" -H "X-Namespace-ID: merchant_a")

# 1. Merchant read is namespace-scoped (200, only merchant_a rows + opted-in globals).
curl -sS "$BASE/api/v2/tags?application_id=commerce" -H "Authorization: Bearer $TOK" "${NS[@]}"

# 2. v1 rejects namespace headers (400 namespace_not_supported).
curl -sS "$BASE/api/v1/tags?application_id=commerce" -H "Authorization: Bearer $TOK" "${NS[@]}"

# 3. Namespaced write is accepted only when WRITE is enabled; otherwise
#    403 namespaced_writes_disabled (kill switch) — never a 500.
curl -sS -X POST "$BASE/api/v2/tags" -H "Authorization: Bearer $TOK" "${NS[@]}" \
  -H "Content-Type: application/json" \
  -d '{"application_id":"commerce","name":"Premium","slug":"premium","type":"label"}'

# 4. With NAMESPACE_V2_API_ENABLED=false (rolled back), the same namespaced request
#    returns 503 namespace_api_disabled while global v2 continues to serve.
```

The automated equivalent is the registry-driven isolation sweep (`tests/isolation/`), which asserts
no merchant sees another merchant's rows across every v2 read endpoint.

### Staging rehearsal

Before any production flag change, rehearse the **full** sequence in staging and record the result:

1. From all-off (or a clean deploy), walk the rollout sequence above one flag per deploy, running
   the post-deploy checklist and smoke tests at each step; enable writes last and create two
   merchants with the same slug to confirm the constraint swap holds.
2. Then walk the rollback ladder, confirming: namespaced v2 returns `503` after step 1, global
   traffic is unaffected throughout, previously-written merchant rows remain intact (not deleted),
   and no namespaced row becomes visible through a v1 read.
3. Confirm `python manage.py check` rejects a deliberately invalid combination (e.g.
   `WRITE=true READ=false`) with `octonomy.E013`.

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
