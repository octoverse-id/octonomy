# Architecture

Octonomy is a REST-first taxonomy service. It owns tags and tag assignments, while external
systems retain ownership of resources.

## Layers

- `config`: Django project settings and URL routing.
- `octonomy.core`: request ids, auth placeholder, errors, pagination, health checks, logging.
- `octonomy.tags`: tag model, validation, CRUD API, tag filtering.
- `octonomy.assignments`: assignment model, idempotent writes, resource/tag query APIs.
- `octonomy.audit`: append-only audit logs for tag and assignment mutations.
- `octonomy.openapi`: OpenAPI metadata and future schema customizations.

## Tenant and Application Boundaries

Every tag and assignment has a `tenant_id`. Assignments also require `application_id`.
Tags may be shared across applications by leaving `application_id` null.

Application-specific tags may only be assigned within the same application. Shared tags may be
assigned to any application in the same tenant.

## Audit and Usage Counts

Mutation APIs write tenant-scoped audit logs for actual changes only. Idempotent no-op writes,
such as assigning an already assigned tag or removing a missing assignment, do not create audit
rows.

Tag responses expose `usage_count`, computed from current tag assignments rather than persisted on
the tag row.

## Future Extension Points

- GraphQL read API
- tag aliases
- tag groups or vocabularies
- event publishing for assignment changes
