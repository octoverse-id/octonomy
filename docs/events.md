# Outbox Events

Octonomy records integration-oriented domain events in the transactional outbox
(`outbox_events`) inside the same database transaction as the mutation that
caused them. A dispatcher later publishes each event through the configured
transport (structured log or signed webhook). This document is the consumer
contract: the event envelope, the per-event payloads, delivery/replay
semantics, and how to route events per namespace.

See [`docs/architecture.md`](architecture.md#transactional-event-outbox) for the
dispatcher internals and [`docs/operations.md`](operations.md) for running the
dispatcher and webhook transport.

## Event envelope

Every published event is a JSON object with the same top-level shape, produced by
`serialize_outbox_event`:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string (UUID) | Unique event id. Stable across redeliveries — use it for idempotency. |
| `tenant_id` | string | Owning tenant. |
| `application_id` | string \| null | Owning application; `null` for tenant-shared rows. |
| `namespace_type` | string \| null | Namespace axis (e.g. `merchant`). `null` = the global (tenant-shared) namespace. **Additive field, see below.** |
| `namespace_id` | string \| null | Namespace identifier within `namespace_type`. `null` when `namespace_type` is `null`. **Additive field, see below.** |
| `event_type` | string | One of the types in [Event types](#event-types). |
| `aggregate_type` | string | `tag`, `vocabulary`, `tag_alias`, or `tag_assignment`. |
| `aggregate_id` | string | Id of the aggregate the event is about. |
| `operation_id` | string (UUID) \| null | Correlates every event/audit row emitted by one request. Bulk and replace operations share one `operation_id`. |
| `request_id` | string \| null | Inbound `X-Request-ID`, if any. |
| `actor_id` | string \| null | Resolved actor (`X-Actor-ID` or the service client name). |
| `tag_id` | string (UUID) \| null | Related tag, when applicable. |
| `resource_type` | string \| null | External resource type for assignment events. |
| `resource_id` | string \| null | External resource id for assignment events. |
| `payload` | object | Event-type-specific body; see [Event types](#event-types). |
| `metadata` | object | Free-form JSON object; `{}` by default. |

`namespace_type` and `namespace_id` always equal the namespace of the row the
event describes: a global mutation emits `null`/`null`; a merchant mutation emits
that merchant's `namespace_type`/`namespace_id`. Events are never namespace-blind
— a merchant write never emits a global-looking event.

## Namespace fields and consumer compatibility

`namespace_type` and `namespace_id` are **additive** JSON fields. They were added
without removing, renaming, or changing the meaning of any pre-existing field.

- **Existing consumers keep working.** A consumer written before namespaces
  existed ignores the two unknown keys and behaves exactly as before. Global
  events serialize to the historical shape plus `namespace_type: null` and
  `namespace_id: null`.
- **Do not treat `null` as a wildcard.** `null`/`null` is the concrete global
  (tenant-shared) namespace, not "any namespace".
- **Routing lives in the JSON body**, not in webhook headers. Webhook headers
  (`X-Octonomy-Event-ID`, `X-Octonomy-Event-Type`, `X-Octonomy-Tenant-ID`,
  `X-Octonomy-Request-ID`, `X-Octonomy-Signature`) are unchanged; read
  `namespace_type`/`namespace_id` from the parsed body to partition events.

## Consumer routing guidance

Partition downstream processing on the tuple
`(tenant_id, application_id, namespace_type, namespace_id)`:

- **Global events** (`namespace_type == null`) are tenant-shared. Route them to
  the tenant/application-level consumer; they are visible to every merchant
  under that application.
- **Merchant (namespaced) events** (`namespace_type != null`) belong to exactly
  one merchant. Route them to that merchant's partition/topic/handler keyed by
  `namespace_id` (scoped within `namespace_type` and `application_id`). Never
  fan a merchant event out to other merchants.
- A per-merchant sink can subscribe with a filter like
  `namespace_type == "merchant" AND namespace_id == "<merchant>"`, optionally
  also consuming global events if it wants tenant-shared taxonomy changes.

Because `namespace_id` values are opaque, caller-canonical strings, treat them as
exact-match partition keys (no case-folding or normalization).

## Delivery and replay semantics

Delivery is **at-least-once and unchanged by the namespace fields.**

- The dispatcher claims a row, publishes outside the row-locking transaction,
  then marks it published. A crash between publish and mark, or an expired
  processing claim recovered by a later run, can therefore **redeliver** an
  event. Consumers must deduplicate on the stable `id` (and may use
  `operation_id` to group a request's events).
- Failed deliveries are retried with exponential backoff and dead-lettered after
  the configured maximum attempts. Retries and dead-lettering reuse the same
  serialized event — including its namespace fields — so a redelivered event has
  the same shape and namespace as the original.
- Ordering is best-effort by creation time and is not guaranteed under retries.
  Events within one operation share `operation_id` but may arrive interleaved.

## Event types

Events are emitted only for real changes: idempotent no-op writes, repeated
deletes, and no-op updates emit nothing.

| `event_type` | `aggregate_type` | `payload` |
| --- | --- | --- |
| `tag.created` | `tag` | `{ "after": <tag snapshot> }` |
| `tag.updated` | `tag` | `{ "before": <changed fields>, "after": <changed fields> }` |
| `tag.deactivated` | `tag` | `{ "before": {"is_active": true}, "after": {"is_active": false} }`, plus `"cascaded_alias_ids": [<uuid>, ...]` when deactivation cascaded to active aliases |
| `vocabulary.created` | `vocabulary` | `{ "after": <vocabulary snapshot> }` |
| `vocabulary.updated` | `vocabulary` | `{ "before": <changed fields>, "after": <changed fields> }` |
| `vocabulary.deactivated` | `vocabulary` | `{ "before": {"is_active": true}, "after": {"is_active": false} }` |
| `tag_alias.created` | `tag_alias` | `{ "after": <alias snapshot> }` |
| `tag_alias.updated` | `tag_alias` | `{ "before": <changed fields>, "after": <changed fields> }` |
| `tag_alias.deactivated` | `tag_alias` | `{ "before": {"is_active": true}, "after": {"is_active": false} }`; when cascaded from a tag deactivation, also `"cascade": {"source_event_type": "tag.deactivated", "source_tag_id": <uuid>}` |
| `assignment.created` | `tag_assignment` | `{ "after": <assignment snapshot> }` |
| `assignment.removed` | `tag_assignment` | `{ "before": <assignment snapshot> }` |

`before`/`after` on `*.updated` events contain only the fields that actually
changed (foreign keys are reported as `parent_id`, `vocabulary_id`, `tag_id`).

### Snapshots

Snapshots capture entity attributes at the time of the event. The routing
namespace is on the **envelope** (`namespace_type`/`namespace_id`), so snapshots
carry entity state without duplicating it.

- **tag**: `id`, `tenant_id`, `application_id`, `name`, `slug`, `type`,
  `description`, `parent_id`, `vocabulary_id`, `metadata`, `is_active`,
  `created_at`, `updated_at`.
- **vocabulary**: `id`, `tenant_id`, `application_id`, `name`, `slug`,
  `description`, `metadata`, `is_active`, `created_at`, `updated_at`.
- **tag_alias**: `id`, `tenant_id`, `application_id`, `tag_id`, `name`, `slug`,
  `metadata`, `is_active`, `created_at`, `updated_at`.
- **tag_assignment**: `id`, `tenant_id`, `application_id`, `tag_id`,
  `resource_type`, `resource_id`, `assigned_by`, `assigned_at`.
