# TODOS

Active deferred work. Each item has enough context to pick up cold, and a **trigger** saying when it
stops being deferred.

Finished and won't-do items are condensed into [Resolved](#resolved) at the bottom — one line each,
so the list above stays actionable while the "already considered, don't re-propose" signal survives.
Full rationale for anything resolved lives in its linked issue/PR and in git history.

## Namespace layer (issue #36)

The namespace feature (S1–S7) is **built, tested, documented, and merged**; epic #36 is closed as
delivered. Octonomy is self-hosted, so production rollout, burn-in, and per-deployment verification
are the operator's responsibility, guided by `docs/operations.md` ("Namespace Rollout & Operations").
NS-5 below is the only follow-up still live — a dormant scale tripwire.

### NS-5: Grant matching — DB-filter grant lookup (issue #63 CLOSED; residual tracked here)
Issue #63 is closed: its tenant-level half shipped (PR #70), and the remaining per-merchant work is a
dormant tripwire with no trigger yet, so it lives here rather than as a perpetually-open issue.
**When the tripwire fires (per-merchant grant issuance enabled at scale), re-open #63 or file a fresh
issue from this entry.**

- **Done (PR #70):** `tenant_grants` filters by `tenant_id` in SQL instead of loading every grant the
  client holds across all tenants and filtering in Python (`core/auth.py`; cached per request via
  `request_tenant_grants`). Exact-equivalent result set — no auth decision or error reason changes —
  so a client granted many *tenants* no longer scans them all per request. Uses the existing grant
  indexes; no migration.
- **Residual (tripwire, not triggered):** the *single-tenant, many-namespace* fan-out (one grant per
  merchant in one tenant) is NOT reduced. `tenant_grants` intentionally does not narrow by namespace,
  because the permission layer inspects the whole tenant grant set to produce precise
  tenant / namespace / application error reasons — a namespace pre-filter would change which error a
  denied request gets. Reducing the per-merchant scan requires refactoring that error reasoning first
  (compute the denial reason without materialising every grant), then adding a safe-superset namespace
  filter (`Q(namespace_wildcard=True) | Q(global) | Q(exact scope_context)` — proven a safe superset
  because a request only evaluates grants against its own scope + `GLOBAL_SCOPE`, per
  `requested_scope_contexts`). **Trigger:** per-merchant grant issuance enabled at scale.

## Admin layer (epic #83)

### ADM-1: Distinct reactivation lifecycle events — DEFERRED (from /plan-eng-review of #83/#85)
The Unfold admin (#85) reactivates a Tag/Vocabulary/TagAlias via `update_*(row, {"is_active": True})`
plus an active-relation guard (revalidate parent/vocabulary are active before flipping `is_active`).
That path emits a **generic `*.updated(is_active)`** audit/outbox event, not a distinct reactivation
signal, and does **not** auto-reactivate the aliases that `deactivate_tag` cascaded off (intentional
asymmetry).

- **What:** Add dedicated `reactivate_tag` / `reactivate_vocabulary` / `reactivate_tag_alias` services
  emitting `*.reactivated` audit + outbox events, with an explicit alias-reactivation policy.
- **Why:** Outbox consumers currently can't distinguish a reactivation from any other edit. A downstream
  system that must react specifically to reactivation (re-index a search corpus, re-notify) cannot today.
- **Pros:** Distinct lifecycle signal; symmetric with `deactivate_*`; one home for reactivation rules.
- **Cons:** New service code + new event types + their own tests; unnecessary until a consumer needs it.
- **Context:** The A3 decision + a cross-model tension in the #83 eng review chose the minimal
  guard-on-`update_*` path for correctness now. This is the richer alternative (A3 option B), deferred as
  engineered-enough restraint — don't ship speculative event types before a consumer requires them.
- **Depends on / blocked by:** #85 merged. **Trigger:** an outbox consumer needs to distinguish
  reactivation events.

## Deployment (PR #95)

### DEP-1: Publish an official container image — DEFERRED (from PR #95)
`docs/deployment.md` has every *container* operator build the image themselves
(`docker build -t octonomy:local .` for Compose; for Kubernetes, tag and push to a registry the
cluster can pull from). There is no published image, so nobody can `docker pull` a release. The
VPS/systemd path is unaffected — it installs from a source checkout into a virtualenv.

- **What:** A CI job that builds and pushes a versioned image (e.g. `ghcr.io/octoverse-id/octonomy`)
  on release tags, plus multi-arch (amd64/arm64) and provenance/signing if the registry supports it.
  Then update the guide to `pull` by default and keep "build it yourself" as the alternative.
- **Why:** Building the image is the biggest step in both container paths, and Kubernetes additionally
  requires a cluster-pullable registry — an extra dependency for anyone without one. It also means no
  two deployments provably run the same bytes for a given release.
- **Cons:** Adds a publish surface to own — registry credentials, tag hygiene, and a supply-chain
  story (who can push, how images are signed). Publishing is a one-way door reputationally: once
  people pull `:3.1.0`, that tag has to keep meaning the same thing.
- **Context:** Raised as explicit out-of-scope follow-up in the PR #95 description; the guide already
  notes the absence. Deliberately not bundled with the docs change.
- **Trigger:** the build-it-yourself step becomes real adoption friction, or a release needs to be
  independently verifiable by digest.

## Configuration

### CFG-1: Rename `OCTONOMY_API_VERSION` — DEFERRED (raised 2026-08-05)
The name says "the version of the API", which is the one thing it is not. Octonomy has three version
surfaces (`docs/versioning.md`), and this variable drives the middle one:

| Surface | Where | Answers |
| --- | --- | --- |
| Package version | `pyproject.toml` | Which release am I running? |
| **Schema/document version** | **OpenAPI `info.version`, set by `OCTONOMY_API_VERSION`** | **Which build produced this schema document?** |
| URL contract version | `/api/v1/`, `/api/v2/` | Which contract am I calling? |

It has exactly one consumer: `config/settings.py:44` feeds `SPECTACULAR_SETTINGS["VERSION"]`
(`settings.py:338`), which becomes `info.version` in the generated schemas. It never touches routing.
The v1/v2 contract is a separate setting entirely (`REST_FRAMEWORK["ALLOWED_VERSIONS"]`,
`settings.py:331`), and the two never meet in code. `docs/versioning.md` already calls this row the
"API **schema** version" — clearer than the env var it configures.

- **What:** Rename to `OCTONOMY_SCHEMA_VERSION` (or `OCTONOMY_OPENAPI_VERSION`). Migration path:
  accept both names for one minor with the old one logging a deprecation warning, document the new
  name, then drop the old in the next major. Touches `config/settings.py`, `.env.example`,
  `docs/versioning.md`, `docs/development.md`, `docs/release.md`, `deploy/.env.production.example`,
  and the `version-check` grep in the `Makefile`.
- **Why:** A real reader hit this and asked why the variable is set to `3.0.1` when the API versions
  are v1/v2. The name invites exactly that misreading, and the misreading is dangerous in the other
  direction too: someone "correcting" it to `v2` would stamp a nonsense `info.version` on both schema
  documents.
- **Pros:** The name would state what it does; removes a recurring source of confusion in an
  operator-facing surface; aligns the env var with the vocabulary `docs/versioning.md` already uses.
- **Cons:** A breaking config rename needs a deprecation window and touches ~7 files plus the
  `version-check` gate. Pure ergonomics — nothing is broken today.
- **Context:** Blast radius is unusually small right now: 3.0.1 changed both the env template and the
  `docs/release.md` production checklist to say **leave it unset**, since the application default
  already tracks the release. Realistically no deployment should have it set at all, so a rename
  would strand almost nobody. That window narrows as more people deploy from the new guide.
- **Trigger:** the next time `config/settings.py` version handling is touched anyway, or another
  report of the same confusion. Do it before the deployment guide drives wide adoption of the
  current name.

---

## Resolved

Closed out; kept as a one-line ledger so settled decisions are not re-proposed. Anything marked
**won't-do** was considered and deliberately rejected — read the linked issue before reopening it.

- **NS-1 — Scope-move immutability.** DONE (#61, PR #66). Shipped the `scope_immutable` 409: a PATCH
  changing `application_id`/`namespace_type`/`namespace_id` is rejected; re-create in the target scope.
  Atomic re-parenting tooling is **won't-do** until a real "move a merchant's data" use case appears.
- **NS-2 — Alias resolution precedence ladder.** DONE (#62, PR #68). Ladder documented
  (`(app,exact ns)` > `(app,global ns)` > `(shared,global)`; canonical beats alias regardless of
  scope), `ambiguous_resolution` guard added for same-rung ties, and a read-auth
  body-`application_id` bypass closed in the same PR.
- **NS-3 — Rollback ordering.** DONE (#59, PR #65). `docs/operations.md` carries the
  incident-graduated rollback ladder, why the boot dependency check forces that order, and the
  "never downgrade to v1 visibility" prohibition.
- **NS-4 — `include_global` query plan.** **Won't-do** (#60 closed). Nothing to build in the app; it
  is a per-deployment `EXPLAIN` check. `docs/operations.md` ("Read-path query plan
  (`include_global=true`)") tells operators to verify on prod-sized data and add a per-branch index
  if it degrades. Not a maintainer task for a self-hosted product.
- **NS-6 — Constraint-swap lock window.** DONE as tooling + guidance (#58 closed, PR #64).
  `python manage.py estimate_namespace_swap_lock` ships; measuring the window against real row counts
  is a per-deployment operator step on a restored clone, not a maintainer task.
- **NS-7 — Post-burn-in flag + index cleanup.** **Won't-do** (reframed). The five
  `OCTONOMY_NAMESPACE_*` flags and the E010–E016 dependency check are **permanent operator
  configuration**, not rollout scaffolding — each deployment uses them for its own staged rollout and
  kill-switch. There is no maintainer-side "remove the flags" task; dropping them after a burn-in is
  a local operator decision.
