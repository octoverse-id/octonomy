# Contributing to Octonomy

Thanks for your interest in contributing! Octonomy is a multi-tenant tag management and taxonomy
service built with Django and Django REST Framework. This guide covers how to get set up, the
conventions we follow, and how to get a change merged.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

See [`docs/development.md`](docs/development.md) for the full local setup. The short version:

```bash
cp .env.example .env
make install      # uv sync --extra dev
make db-up        # start PostgreSQL via docker compose
make migrate
make seed         # demo data + a one-time svc-demo token (printed once)
make run
```

`make seed` prints a demo service token to the terminal — store it immediately, it cannot be
retrieved later.

## Quality gates

Run these before opening a PR; CI runs the same checks and must pass before merge:

```bash
make lint      # ruff check
make format    # ruff format (line length 100)
make test      # pytest (runs against PostgreSQL)
make openapi   # regenerate the OpenAPI schema and confirm it builds
```

## Coding conventions

These mirror the project rules in [`AGENTS.md`](AGENTS.md) — read it for the full set:

- **Tenant isolation is explicit in every query.** All tenant-owned data is scoped by
  `tenant_id`; `application_id` scoping rules apply to tags and assignments.
- **Business rules live in the service layer**, not in views. Keep cross-row logic out of
  serializers and views.
- **Use UUID primary keys** and PostgreSQL `JSONB` for tag metadata.
- **Add database constraints and indexes** for uniqueness, lookups, and idempotency.
- **Tag deletion is deactivation**, never a hard delete.
- Maintain **OpenAPI coverage** for every public endpoint, and use the standard JSON error
  shape (`error.code`, `error.message`, `error.details`, `error.request_id`).

## Testing expectations

- Add tests for **tenant isolation, application isolation, idempotency, and validation rules**.
- API changes must include **API tests and OpenAPI schema coverage**.
- Assignment writes should be tested against real **PostgreSQL** behavior, not only mocked
  storage.

## Branches, commits, and PRs

We follow [Conventional Branch](https://conventional-branch.github.io/) naming:

- Format: `<type>/<description>`, lowercase alphanumerics, hyphens, and dots.
- Allowed types: `feature`, `feat`, `bugfix`, `fix`, `hotfix`, `release`, `chore`.
- When a change tracks an issue, include the number: `feature/123-tag-aliases`,
  `fix/124-audit-log-race`.

For pull requests:

- Keep PRs focused and include tests.
- Reference the issue you're closing with `Closes #<issue-number>` in the PR body.
- Summarize what changed and why, and note any new env vars, migrations, or API surface.
- Ensure `make lint`, `make test`, and `make openapi` pass locally.

## Reporting security issues

Please do **not** open public issues for security vulnerabilities. See [`SECURITY.md`](SECURITY.md)
for private reporting instructions.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
