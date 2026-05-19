# Architecture

Octonomy is a REST-first taxonomy service. It owns tags and tag assignments, while external
systems retain ownership of resources.

## Layers

- `config`: Django project settings and URL routing.
- `octonomy.core`: request ids, auth placeholder, errors, pagination, health checks, logging.
- `octonomy.tags`: tag model, validation, CRUD API, tag filtering.
- `octonomy.assignments`: assignment model, idempotent writes, resource/tag query APIs.
- `octonomy.openapi`: OpenAPI metadata and future schema customizations.

## Tenant and Application Boundaries

Every tag and assignment has a `tenant_id`. Assignments also require `application_id`.
Tags may be shared across applications by leaving `application_id` null.

Application-specific tags may only be assigned within the same application. Shared tags may be
assigned to any application in the same tenant.

## Future Extension Points

- GraphQL read API
- tag aliases
- tag groups or vocabularies
- audit logs
- usage counts
- event publishing for assignment changes
