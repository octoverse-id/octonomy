# TODOS

Deferred work captured during reviews. Each item has enough context to pick up cold.

## Namespace layer (issue #36) — status

The namespace feature (S1–S7) is **built, tested, documented, and merged**; epic #36 is closed as
delivered. Octonomy is self-hosted, so production rollout, burn-in, and per-deployment verification
are the operator's responsibility, guided by `docs/operations.md` ("Namespace Rollout & Operations").
The follow-ups below are resolved except **NS-5**, which is a live scale tripwire.

### NS-1: Scope-move immutability — DONE (#61, PR #66)
Shipped the `scope_immutable` guard: a PATCH that changes `application_id`/`namespace_type`/
`namespace_id` is rejected (409). Re-create the row in the target scope instead. Atomic re-parenting
tooling (option b) remains unbuilt; revisit only if a real "move a merchant's data" use case appears.

### NS-2: Alias resolution precedence ladder — DONE (#62, PR #68)
Documented the full ladder (`(app,exact ns)` > `(app,global ns)` > `(shared,global)`; canonical beats
alias regardless of scope) and added the `ambiguous_resolution` guard for same-rung ties. Also closed
a read-auth body-`application_id` bypass in the same PR.

### NS-3: Rollback ordering — DONE (#59, PR #65)
`docs/operations.md` spells out the incident-graduated rollback ladder, why the order is forced by the
boot dependency check, and the "never downgrade to v1 visibility" prohibition.

### NS-4: `include_global` query plan — RESOLVED as operator guidance (#60 closed)
There is nothing to build in the app: it is a per-deployment `EXPLAIN` check. `docs/operations.md`
("Read-path query plan (`include_global=true`)") tells operators to verify the plan on prod-sized data
and add a per-branch index if it degrades. Not a maintainer task for a self-hosted product.

### NS-5: Grant matching — DB-filter if per-merchant fan-out grows (OPEN, #63)
- **What:** Switch `matching_grants` (core/auth.py) from prefetched Python filtering to a DB-filtered
  query if per-merchant grants are ever issued at scale.
- **Why:** Today grant counts are small (no per-merchant fan-out at launch), so the O(N) Python scan
  per request is fine. If a future tier issues one grant per merchant, the scan becomes a per-request
  latency cost.
- **Tripwire:** revisit when enabling per-merchant grant issuance. Not production-data dependent, so it
  stays open as backlog rather than being closed with the rollout items.

### NS-6: Constraint-swap lock window — RESOLVED as operator guidance (#58 closed)
Tooling shipped (`python manage.py estimate_namespace_swap_lock`, PR #64) and documented in
`docs/operations.md` ("Constraint-swap lock window (NS-6)"). Measuring the window against real row
counts is a per-deployment operator step run on a restored clone — not a maintainer task, and not
possible without production-scale data.

### NS-7: Post-burn-in flag + index cleanup — REFRAMED (not tracked as a task)
The five `OCTONOMY_NAMESPACE_*` flags and the E010–E016 dependency check were originally described as
rollout scaffolding to remove after a single production burn-in. For a self-hosted product they are
**permanent operator configuration** — each deployment uses them to run its own staged rollout and to
keep a kill-switch/rollback path — so there is no maintainer-side "remove the flags" task. If a
specific deployment ever wants to drop them after its own burn-in, that is a local operator decision,
guided by the runbook.
