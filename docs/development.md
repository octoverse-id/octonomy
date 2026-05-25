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
- `SERVICE_TOKEN_PEPPER`: HMAC pepper used to hash service API tokens. Use a non-default,
  secret value outside local development.
- `MAX_BULK_TAGS`: maximum number of tags accepted by bulk endpoints.
- `LOG_LEVEL`: structured logging level.

## Service Tokens

Create a local token after migrations:

```bash
python manage.py create_service_token \
  --name svc-catalog \
  --tenant tenant_demo \
  --application commerce \
  --scope tags:read \
  --scope tags:write \
  --scope audit:read \
  --metadata '{"owner":"platform"}'
```

The raw token is printed once. Revoke a token by prefix:

```bash
python manage.py revoke_service_token --prefix <key-prefix>
```

## Audit Development

Use `X-Actor-ID` on mutation requests to make audit rows easier to inspect during local testing.
When `X-Actor-ID` is omitted, audit logs use the authenticated service client name. Assignment
APIs fall back to `assigned_by` only when no service client identity is available.

## Seed Data

`make seed` creates demo vocabularies, tags, aliases, assignments, and a tenant-wide `svc-demo`
service token for tenant `tenant_demo`. Store the printed token immediately; Octonomy stores only
the token hash and prefix.

The default vocabularies are shared `labels`, commerce `product-labels`, and CMS
`content-labels`. The default aliases include `promoted` and `hero` for `featured`, `discount`
and `promo` for `sale`, and `urgent` for `breaking-news`.
