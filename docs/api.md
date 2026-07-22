# API Notes

Base path: `/api/v1` (global-only) or `/api/v2` (adds the namespace surface). Both are served by
one view tree; see [API Versions and the Namespace Surface (v2)](#api-versions-and-the-namespace-surface-v2).

Required headers for tenant-owned endpoints:

```text
Authorization: Bearer <service-token>
X-Tenant-ID: tenant_demo
```

Service tokens are created by operators with `python manage.py create_service_token`. The command
accepts optional application, namespace, and `--metadata '<json-object>'` arguments. Tokens are
scoped by tenant, optional application, optional namespace restriction, and API scopes:

- `tags:read`: read tag, vocabulary, assignment, and resource tag endpoints.
- `tags:write`: mutate tags, vocabularies, and assignments.
- `audit:read`: read audit log endpoints.

A grant with `application_id = null` allows all applications in that tenant. A grant with a
specific `application_id` only allows requests that supply the same application scope.

Namespace grants are fail-closed:

- Omitting namespace arguments creates a global-only grant.
- `--namespace-type <type> --namespace-id <id>` creates an exact namespace grant and requires
  `--application`.
- `--namespace-wildcard` is an explicit broad grant across global and namespaced requests. It may
  be tenant-wide or application-specific.

Example exact merchant grant:

```bash
python manage.py create_service_token \
  --name svc-merchant-a \
  --tenant tenant_demo \
  --application commerce \
  --namespace-type merchant \
  --namespace-id merchant_a \
  --scope tags:read \
  --scope tags:write
```

## Namespace Trust Model

Octonomy enforces exact namespace grants against the request namespace. A token restricted to
`merchant/merchant_a` cannot access global data or another merchant namespace. Legacy grants with
null namespace fields remain global-only; null is not a namespace wildcard.

An explicit namespace-wildcard grant authorizes the client to assert any namespace inside the
grant's tenant and optional application boundary. Under that broad grant, namespace selection is
caller-asserted partitioning: the client backend is responsible for authenticating the merchant
and sending the correct namespace. Do not expose service tokens to browsers or mobile clients.
Use exact per-merchant grants for untrusted tiers and reserve wildcard grants for trusted backend
services.

Optional mutation audit actor header:

```text
X-Actor-ID: svc-catalog
```

When `X-Actor-ID` is omitted, audit logs use the authenticated service client name.

## API Versions and the Namespace Surface (v2)

`/api/v1` and `/api/v2` are served by one view/serializer tree (a version shim). The version is
chosen by the URL prefix; only the namespace surface differs.

- **`/api/v1` is global-only and unchanged.** It ignores namespaces and **rejects** any
  `X-Namespace-Type`/`X-Namespace-ID` header with `400 namespace_not_supported` — a misrouted v2
  client fails loudly rather than silently reading or writing the global namespace. This rejection
  is resolved before authentication, so it is never masked by a `401` on an unauthenticated request.
- **`/api/v2` adds the namespace axis** through request headers:

```http
X-Namespace-Type: merchant
X-Namespace-ID: merchant_a
```

### v2 header contract

- **Absent `X-Namespace-Type` = the global namespace** (global is `null`, not the string `global`).
- If `X-Namespace-Type` is present, `X-Namespace-ID` is **required** (and vice versa).
- A namespaced request must also identify its application (`application_id` query/body param);
  namespace isolation sits below application.
- The literal `global` is **reserved** as a type and rejected.
- Values are opaque, caller-canonical strings: validated for blank/whitespace via the same
  `validate_external_id` hygiene as `application_id`, **not** case-folded (`merchant_a` ≠
  `Merchant_A`), and capped at 100 characters. There is no server-side type registry/allowlist —
  a typo strands the caller's own data in an unreachable scope (fail-safe), it does not leak.
- Each namespace header must be sent exactly once (a comma-folded/repeated header is rejected).

### v2 error codes

| Code | Status | When |
| ---- | ------ | ---- |
| `namespace_not_supported` | 400 | `X-Namespace-*` sent to `/api/v1`. |
| `namespace_invalid` | 400 | Reserved `global`, type without id (or id without type), or a folded/repeated header. |
| `validation_error` | 400 | Blank/whitespace namespace value (field-level detail). |
| `namespaced_writes_disabled` | 403 | A namespaced write while `NAMESPACE_WRITE_ENABLED` is off (see below). |
| `scope_immutable` | 409 | A PATCH that changes a row's `application_id`/`namespace_type`/`namespace_id`. Scope is fixed at creation (v1 and v2); re-create in the target scope instead. |
| `namespace_mismatch` | — | Reserved for visible-object cases only. Cross-namespace object lookups return `404` (no existence disclosure), never `namespace_mismatch`. |

### Merchant reads, global rows, and discovery

v2 merchant (namespaced) reads **exclude global rows by default** — isolation is fail-closed,
matching the v1 `include_shared` precedent. Pass `include_global=true` to also return global rows,
but only rows the caller is actually authorized for are merged (an exact merchant grant that lacks
global authority still sees none, even with `include_global=true`).

To **discover global tags** (e.g. to assign a shared tag by id or alias) from the exclude-default
world, list them explicitly: issue a global request (omit the namespace headers) or send
`include_global=true` on the merchant request. Resource-tag and assignment reads still return a
global tag object when a merchant has assigned a global tag, regardless of the `include_global`
default — that is the intended contract, not a leak.

Mixed merchant+global result sets are ordered by the endpoint's existing ordering plus an `id`
tiebreaker, so pagination is stable. Slug collisions across scopes (e.g. a global `premium` and a
merchant `premium`) are **distinct rows and are not de-duplicated**.

### Namespaced writes are gated

`NAMESPACE_WRITE_ENABLED` (env `OCTONOMY_NAMESPACE_WRITE_ENABLED`, default **off**) is the write
kill-switch. While off, v2 reads are namespace-aware but any **write** carrying a namespace scope
returns `403 namespaced_writes_disabled`; global writes (v1 and v2-global) are unaffected. The flag
stays off until audit/outbox propagate namespace and the rollout controls land. Because of this,
**v1 writers govern the merchant-visible global taxonomy**: legacy v1 integrations create global
rows that v2 merchants may consume via `include_global`.

### Caching

Cacheable reads (`GET`/`HEAD`) send `Vary: Authorization, X-Tenant-ID, X-Namespace-Type,
X-Namespace-ID` so a shared cache never serves one caller's or namespace's rows to another.

### usage_count per version

`usage_count` is computed from current assignments, not persisted. v1/global responses keep the
legacy tenant-wide count. v2 counts only assignments visible to the requesting scope: a global v2
view counts global assignments only, a merchant view counts same-merchant plus global assignments.
Global and merchant counts are expected to differ.

Tag responses include `usage_count`, computed from current tag assignments, and
`vocabulary_id` when the tag belongs to a vocabulary.

Tag aliases are alternate tenant-scoped names for canonical tags. Alias read endpoints require
`tags:read`; alias mutation endpoints require `tags:write`. Aliases can only point at active
canonical tags, and deactivating a tag also deactivates its aliases.

Errors use this shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": {},
    "request_id": "req_..."
  }
}
```

Audit endpoints:

```text
GET /api/v1/audit-logs
GET /api/v1/tags/{tag_id}/audit-logs
GET /api/v1/resources/{resource_type}/{resource_id}/audit-logs
```

`GET /api/v1/audit-logs` supports filters for `application_id`, `action`, `entity_type`,
`entity_id`, `tag_id`, `resource_type`, `resource_id`, `actor_id`, and `operation_id`.
Vocabulary mutations emit `vocabulary.created`, `vocabulary.updated`, and
`vocabulary.deactivated` audit actions with `entity_type = "vocabulary"`.
Alias mutations emit `tag_alias.created`, `tag_alias.updated`, and `tag_alias.deactivated` audit
actions with `entity_type = "tag_alias"`.

Vocabulary endpoints:

```text
GET /api/v1/vocabularies
POST /api/v1/vocabularies
GET /api/v1/vocabularies/{vocabulary_id}
PATCH /api/v1/vocabularies/{vocabulary_id}
DELETE /api/v1/vocabularies/{vocabulary_id}
```

Vocabularies are tenant-scoped and may be shared across applications by leaving
`application_id` null. Application-specific vocabularies may only contain tags for the same
application. Shared tags can only belong to shared vocabularies.

`GET /api/v1/vocabularies` supports `application_id`, `include_shared`, `slug`, `is_active`,
`q`, `limit`, and `offset`.

Tag endpoints:

```text
GET /api/v1/tags?vocabulary_id={vocabulary_id}
POST /api/v1/tags
PATCH /api/v1/tags/{tag_id}
```

`POST` and `PATCH` accept optional `vocabulary_id`. `GET /api/v1/tags` supports filtering by
`vocabulary_id`.

Alias and resolution endpoints:

```text
GET /api/v1/tag-aliases
POST /api/v1/tag-aliases
GET /api/v1/tag-aliases/{alias_id}
PATCH /api/v1/tag-aliases/{alias_id}
DELETE /api/v1/tag-aliases/{alias_id}
GET /api/v1/tags/{tag_id}/aliases
GET /api/v1/tag-resolution?slug={slug}&type={type}&application_id={application_id}
```

`POST /api/v1/tag-aliases` accepts `application_id`, `tag_id`, `name`, `slug`, `metadata`, and
`is_active`. `GET /api/v1/tag-aliases` supports `application_id`, `include_shared`, `tag_id`,
`slug`, `is_active`, `q`, `limit`, and `offset`.
`GET /api/v1/tag-resolution` resolves active canonical tags first, then active aliases whose
canonical tag is also active. Without `application_id`, only shared tags and aliases are resolved.
If multiple active canonical tags share the same slug across different tag types, provide `type`
to disambiguate.

Assignment writes can use aliases:

```json
{
  "application_id": "commerce",
  "alias_slug": "promo",
  "resource_type": "product",
  "resource_id": "prod_123"
}
```

`POST /tag-assignments` accepts exactly one of `tag_id`, `alias_id`, or `alias_slug`.
Bulk assign and resource replace accept `tag_ids`, `alias_slugs`, or both.

Paginated list endpoints use:

```json
{
  "data": [],
  "pagination": {
    "limit": 50,
    "offset": 0,
    "count": 0,
    "next": null,
    "previous": null
  }
}
```
