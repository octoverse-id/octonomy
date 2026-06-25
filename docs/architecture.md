# Architecture

Octonomy is a REST-first taxonomy service. It owns tags and tag assignments, while external
systems retain ownership of resources.

## Layers

- `config`: Django project settings and URL routing.
- `octonomy.core`: request ids, auth enforcement, errors, pagination, health checks, logging.
- `octonomy.service_auth`: service clients, hashed API keys, and tenant/application grants.
- `octonomy.tags`: vocabulary and tag models, validation, CRUD APIs, taxonomy filtering.
- `octonomy.assignments`: assignment model, idempotent writes, resource/tag query APIs.
- `octonomy.audit`: append-only audit logs for tag, vocabulary, and assignment mutations.
- `octonomy.events`: transactional outbox events, dispatch state, and delivery transports.
- `octonomy.openapi`: OpenAPI metadata and future schema customizations.

## Tenant, Application, and Vocabulary Boundaries

Every tag and assignment has a `tenant_id`. Assignments also require `application_id`.
Tags may be shared across applications by leaving `application_id` null.

Application-specific tags may only be assigned within the same application. Shared tags may be
assigned to any application in the same tenant.

Vocabularies group tags into named tenant-scoped taxonomies. A shared vocabulary has
`application_id = null` and can contain shared tags or application-specific tags from any
application in the tenant. An application-specific vocabulary can only contain tags from the same
application. Shared tags cannot belong to application-specific vocabularies.

Tag aliases are alternate names or slugs for canonical tags. Aliases are tenant-scoped and may be
shared or application-specific. Shared tags may have shared or application-specific aliases, while
application-specific tags may only have aliases in the same application. Aliases only resolve when
both the alias and canonical tag are active; deactivating a tag deactivates its aliases.
Cascade alias deactivation is covered by the parent tag's audit log and does not emit one
`tag_alias.deactivated` audit row per alias. It does emit per-alias outbox events for downstream
consumers. Tag and alias deactivation are currently one-way through the public API.

## Namespace Schema

Namespace fields are present on tags, vocabularies, aliases, assignments, audit logs, outbox
events, and service client grants. Global rows use `namespace_type = null` and
`namespace_id = null`; Octonomy does not store a `"global"` string sentinel. Any non-global
namespace row must also have `application_id` because merchant/sub-tenant isolation sits below an
application.

`namespace_type` and `namespace_id` are caller-canonical external identifiers. Octonomy stores them
case-sensitively, does not trim or normalize them, and limits each to 100 characters. The literal
`global` is reserved as a namespace type so callers cannot create ambiguous global-looking rows.
If a client deletes and recreates a merchant, preserving namespace identifier stability is the
client's responsibility.

Service grants keep legacy `null/null` namespace rows global-only for the namespace axis. Broad
namespace access is an explicit `namespace_wildcard` boolean on grants rather than a special
namespace string, so wildcard authorization cannot collide with caller-owned namespace values.

## Audit and Usage Counts

Mutation APIs write tenant-scoped audit logs for actual changes only. Idempotent no-op writes,
such as assigning an already assigned tag or removing a missing assignment, do not create audit
rows.

Audit actor resolution prefers explicit `X-Actor-ID`, then the authenticated service client name,
then assignment `assigned_by` for legacy/internal service paths. Service client names are readable
for operators but mutable, so future audit hardening should add a stable service client id column
alongside the display actor.

Tag responses expose `usage_count`, computed from current tag assignments rather than persisted on
the tag row.

## Service Authentication

Tenant-owned APIs require an Octonomy service token. Tokens are stored as keyed hashes and grant
access by tenant, optional application, and scope. Health endpoints remain unauthenticated.
Production deployments must provide a non-default `SERVICE_TOKEN_PEPPER`.

## Delivered Extension Points

The following extension points have moved from future design into the implemented backend:

- Vocabularies: tenant-scoped tag groupings that can be shared or application-specific.
- Tag aliases: alternate identifiers and synonym-style resolution for canonical tags.
- Audit logs: append-only mutation history with actor, request, operation, tag, and resource
  correlation.
- Computed usage counts: `usage_count` on tag responses derived from current assignments.
- Service API key auth: Octonomy-managed service clients with tenant/application grants and
  scoped access checks.
- Broker-free event delivery: a transactional outbox dispatcher with logging and webhook
  transports, retry backoff, expired-claim recovery, and dead-letter handling.

## Transactional Event Outbox

Octonomy records integration-oriented domain events in `outbox_events` inside the same database
transaction as successful mutations. Events are emitted for actual tag, vocabulary, alias, and
assignment changes. Idempotent no-op writes, repeated deletes, and no-op updates do not emit
events.

Outbox events carry tenant/application context plus correlation fields such as `operation_id`,
`request_id`, `actor_id`, `tag_id`, `resource_type`, and `resource_id`. Bulk and replace operations
emit one event per concrete assignment created or removed while sharing the same `operation_id`.

Current event types:

- `tag.created`
- `tag.updated`
- `tag.deactivated`
- `vocabulary.created`
- `vocabulary.updated`
- `vocabulary.deactivated`
- `tag_alias.created`
- `tag_alias.updated`
- `tag_alias.deactivated`
- `assignment.created`
- `assignment.removed`

When tag deactivation cascades to active aliases, Octonomy emits the parent `tag.deactivated` event
with `cascaded_alias_ids` and one `tag_alias.deactivated` outbox event per alias using the same
`operation_id`.

The dispatcher is intentionally broker-free for v1. It claims rows quickly in the database,
publishes outside the row-locking transaction, and then marks each event as published, retryable
failed, or dead-lettered. Expired processing claims are recovered by later dispatcher runs without
counting as delivery attempts.

Default local logging transport:

```bash
python manage.py dispatch_outbox_events --limit 100
python manage.py dispatch_outbox_events --limit 100 --retry-failed
```

The default transport logs structured event JSON. The optional webhook transport posts the same
event JSON to an absolute `http` or `https` `OCTONOMY_WEBHOOK_URL`, does not follow redirects, and
signs the request with `OCTONOMY_WEBHOOK_SIGNING_SECRET`.

## Release And Operations Readiness

The v1 REST surface is stable as of `1.0.0` and follows Semantic Versioning. Release readiness
is enforced through Django system checks, migration drift checks, an OpenAPI contract drift gate,
test-coverage thresholds, a dependency vulnerability scan, SQLite tests, and PostgreSQL tests in CI.

Production deployments should run PostgreSQL, set non-default `DJANGO_SECRET_KEY` and
`SERVICE_TOKEN_PEPPER` values, avoid wildcard `ALLOWED_HOSTS`, and use `/health/ready` for database
readiness. Operational details for service-token rotation, outbox dispatch, backup, and smoke tests
are documented in `docs/operations.md` and `docs/release.md`.

## Future Extension Points

- GraphQL read API for flexible tag and resource lookup.
- Nested tag groups within vocabularies.
- Audit log retention, export, and compliance filtering.
- Persisted or cached usage counters for high-volume tenants.
- External auth integration with JWT or API gateway identity.
- External broker integrations such as Kafka, SNS/SQS, or RabbitMQ using the outbox transport
  abstraction.
