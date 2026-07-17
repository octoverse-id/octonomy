# TODOS

Deferred work captured during reviews. Each item has enough context to pick up cold.

## Namespace layer (issue #36) — deferred from 2026-06-22 eng review

### NS-1: Scope-move semantics for namespaced rows
- **What:** Define and implement safe handling for changing a tag/vocabulary/alias's
  `namespace_type`/`namespace_id` (or `application_id`) after creation.
- **Why:** A silent scope move orphans existing assignments, child tags, aliases, and
  vocabulary references — valid rows become invalid after the fact.
- **Context:** Eng review chose NOT to add an immutability guard in the initial build
  (kept the diff tight). Current code only partially blocks `application_id` changes.
  Until this lands, a caller PATCHing namespace/application can corrupt isolation
  invariants. Options when picked up: (a) reject scope mutation with a named
  `scope_immutable` error, or (b) atomic safe-move tooling that re-parents dependents.
- **Depends on:** #41 (domain services) shipped.
- **Risk if ignored:** silent cross-scope reference corruption. **No guard ships today.**

### NS-2: Full alias resolution precedence ladder
- **What:** Specify the complete resolution order across the 3 scope levels created by
  `include_global=true` (exact namespace, app-global, tenant-global), including
  alias-vs-canonical ordering and an explicit tie/ambiguity error.
- **Why:** Decision #8 only states "most-specific wins"; the app-global vs tenant-global
  tie and alias-shadows-canonical cases are unspecified → nondeterministic, data-order
  dependent resolution.
- **Context:** Ships with decision #8's "most-specific wins" rule for now. Refine the
  full ladder + named ambiguity error in a follow-up.
- **Depends on:** #41 selectors.

### NS-3: Grant rollback ordering (spell out)
- **What:** Document the exact rollback sequence once v2 namespaced writes exist:
  writes off first → v2 namespace requests rejected or exact-filtered → never downgrade
  to v1 visibility (which would expose namespaced rows as global).
- **Why:** The issue's rollback section is generic; a naive flag rollback can either
  strand merchant data or expose it through global reads.
- **Context:** Outside-voice (Codex) finding. Pairs with the NAMESPACE_WRITE_ENABLED
  flip-last sequencing and the deploy-tagged system check.
- **Depends on:** #45 (flags/rollout).

### NS-4: include_global query plan — EXPLAIN on Postgres
- **What:** Verify the `include_global=true` read path (OR over nullable namespace
  columns, with pagination + ordering) uses the partial composite indexes and does not
  scan/sort badly on large tenants. Run EXPLAIN on a prod-sized Postgres, not just unit tests.
- **Why:** Unit tests pass on tiny datasets; the OR-heavy nullable filter can degrade on
  large tenants without the right per-branch indexes.
- **Depends on:** #42 (v2 read endpoints) + the namespace lookup indexes.

### NS-5: Grant matching — DB-filter if per-merchant fan-out grows
- **What:** Switch `matching_grants` (core/auth.py:57-66) from prefetched Python filtering
  to a DB-filtered query if per-merchant grants are ever issued at scale.
- **Why:** Today grant counts are small (no per-merchant fan-out at launch, decision #4),
  so the O(N) Python scan per request is fine. If a future tier issues one grant per
  merchant, the scan becomes a per-request latency cost.
- **Tripwire:** revisit when enabling per-merchant grant issuance.

### NS-6: Constraint-swap lock measurement
- **What:** Before the constraint-swap migration, measure row counts on `tag_assignments`,
  `audit_logs`, `outbox_events` and estimate lock duration on a prod-like Postgres.
- **Why:** Eng review kept Django-native constraints (no CONCURRENTLY) and accepted a brief
  maintenance window; confirm the window is actually brief at real scale before deploy.
- **Depends on:** #39 (schema/migrations).

### NS-7: Post-burn-in flag + index cleanup — deferred from S7 (#45)
- **What:** After the namespace rollout has burned in and stabilised in production, remove the
  rollout feature flags and their system check, and drop any superseded pre-swap indexes left from
  the constraint swap.
- **Why:** The five `OCTONOMY_NAMESPACE_*` flags and the E010–E016 dependency check are rollout
  scaffolding, not steady-state config; once namespaced writes are permanently on, the flags are
  dead weight and the old global-only indexes (if any survive the swap) are unused.
- **Context:** S7 shipped the flags, system check, metrics, and runbook but deliberately kept the
  cleanup for a later pass so rollback stays available through burn-in. When picked up: confirm no
  environment still relies on a disabled flag, delete the flags/settings/`.env.example` entries and
  the `namespace_flag_dependencies` / `namespace_write_requires_swap` checks, simplify
  `guard_namespace_write_enabled` / the v2 edge gate if writes are unconditionally on, and remove the
  now-redundant indexes after an `EXPLAIN` confirms they are unused.
- **Tripwire:** revisit once merchant writes have been enabled in production without rollback for a
  full burn-in window.
- **Depends on:** #45 shipped and merchant writes enabled in production.
