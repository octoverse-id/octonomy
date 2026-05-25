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
- Tag aliases are alternate identifiers for canonical tags and must follow tenant/application
  compatibility rules.
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

## Development Pipeline

- Branch names must follow Conventional Branch naming from
  https://conventional-branch.github.io/.
- Use `<type>/<description>` with lowercase alphanumerics, hyphens, and dots only where valid.
- Allowed branch types are `feature`, `feat`, `bugfix`, `fix`, `hotfix`, `release`, and
  `chore`.
- Example branch names: `feature/tag-aliases`, `fix/audit-log-race`, and
  `chore/update-agent-rules`.
- Do not use the old `codex/...` branch prefix in this repository.
- The `code-review/` directory is reserved for local code review pipeline artifacts.
- Review agents must write findings to `code-review/findings.md`.
- Patch agents must read `code-review/findings.md`, apply valid fixes, and write the patch
  summary to `code-review/patches.md`.
- Agents must never stage or commit `code-review/findings.md`, `code-review/patches.md`, or any
  other generated review artifact.
- After creating a PR, remove all local files under `code-review/` except the tracked
  `code-review/.gitkeep` placeholder.

## Web Browsing

- Use the `/browse` skill from gstack for all web browsing.
- Do not use `mcp__claude-in-chrome__*` tools.
