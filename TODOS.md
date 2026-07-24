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

### NS-5: Grant matching — DB-filter grant lookup (#63)
- **Done:** `tenant_grants` filters by `tenant_id` in SQL instead of loading every grant the client
  holds across all tenants and filtering in Python (`core/auth.py`; cached per request via
  `request_tenant_grants`). Exact-equivalent result set — no auth decision or error reason changes —
  so a client granted many *tenants* no longer scans them all per request. Uses the existing grant
  indexes; no migration.
- **Remaining (still a tripwire):** the *single-tenant, many-namespace* fan-out (one grant per
  merchant in one tenant) is NOT reduced. `tenant_grants` intentionally does not narrow by namespace,
  because the permission layer inspects the whole tenant grant set to produce precise
  tenant / namespace / application error reasons — a namespace pre-filter would change which error a
  denied request gets. Reducing the per-merchant scan requires refactoring that error reasoning first
  (compute the denial reason without materialising every grant), then adding a safe-superset namespace
  filter. **Tripwire:** revisit when per-merchant grant issuance is actually enabled at scale.

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
