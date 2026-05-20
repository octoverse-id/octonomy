# Octonomy Agent Instructions

Octonomy is a standalone, multi-tenant, multi-application Tag Management / Taxonomy Service.

## Product Rules

- Octonomy stores tags and tag assignments only.
- Octonomy does not store or duplicate external resource data.
- All tenant-owned data must be scoped by `tenant_id`.
- `application_id` is required for tag assignments.
- `tag.application_id = null` means the tag is shared across applications in the same tenant.
- App-specific tags may only be assigned inside their own application.
- Shared tags may be assigned to any application in the same tenant.
- Tag deletion should be implemented as deactivation, not hard delete.

## API Rules

- REST is the primary API surface for v1.
- Keep GraphQL out of v1 implementation unless explicitly requested as a separate future phase.
- Maintain OpenAPI coverage for all public endpoints.
- Use consistent JSON error responses with `error.code`, `error.message`, `error.details`, and `error.request_id`.
- Use limit/offset pagination for list endpoints.

## Backend Conventions

- Use Django, Django REST Framework, PostgreSQL, and UUID primary keys.
- Use PostgreSQL JSONB for tag metadata.
- Prefer service-layer business rules over embedding cross-row logic directly in views.
- Keep tenant isolation explicit in every query.
- Add database constraints and indexes for uniqueness, lookup performance, and idempotency.
- Use Octonomy service API keys for service-to-service auth.
- Service tokens must be stored hashed and scoped by tenant, optional application, and scopes.

## Testing Expectations

- Add tests for tenant isolation, application isolation, idempotency, and validation rules.
- API changes must include API tests and OpenAPI schema coverage.
- Assignment writes should be tested against PostgreSQL behavior, not only mocked storage.

## Local Development

- Keep Docker Compose working for PostgreSQL local development.
- Keep `.env.example`, README setup steps, and Makefile commands current.
- Do not introduce external services for v1 unless explicitly approved.

## Web Browsing

- Use the `/browse` skill from gstack for all web browsing.
- Do not use `mcp__claude-in-chrome__*` tools.
