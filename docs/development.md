# Development

## Setup

```bash
cp .env.example .env
make install
make db-up
make migrate
make seed
make run
```

## Quality Gates

```bash
make test
make lint
make openapi
```

## Environment

- `DATABASE_URL`: PostgreSQL connection URL.
- `AUTH_BEARER_TOKEN_DEV`: development bearer token.
- `MAX_BULK_TAGS`: maximum number of tags accepted by bulk endpoints.
- `LOG_LEVEL`: structured logging level.

## Audit Development

Use `X-Actor-ID` on mutation requests to make audit rows easier to inspect during local testing.
Assignment APIs fall back to `assigned_by` when the actor header is not present.
