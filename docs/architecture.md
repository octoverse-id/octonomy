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
`tag_alias.deactivated` row per alias. Tag and alias deactivation are currently one-way through
the public API.

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

## Future Extension Points

- GraphQL read API for flexible tag and resource lookup.
- Nested tag groups within vocabularies.
- Audit log retention, export, and compliance filtering.
- Persisted or cached usage counters for high-volume tenants.
- Event publishing for tag, vocabulary, and assignment changes.
- External auth integration with JWT or API gateway identity.
