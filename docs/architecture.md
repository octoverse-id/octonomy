# Architecture

Octonomy is a REST-first taxonomy service. It owns tags and tag assignments, while external
systems retain ownership of resources.

## Layers

- `config`: Django project settings and URL routing.
- `octonomy.core`: request ids, auth placeholder, errors, pagination, health checks, logging.
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

## Audit and Usage Counts

Mutation APIs write tenant-scoped audit logs for actual changes only. Idempotent no-op writes,
such as assigning an already assigned tag or removing a missing assignment, do not create audit
rows.

Tag responses expose `usage_count`, computed from current tag assignments rather than persisted on
the tag row.

## Future Extension Points

- GraphQL read API
- tag aliases
- nested tag groups within vocabularies
- event publishing for assignment changes
