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

## Deployment

### DEP-2: Constrain who can publish — PARTLY DONE, residual DEFERRED (raised 2026-08-07, PR #105; epic #111 landed 2026-08-13)
`publish-image.yml` holds `packages: write`, `id-token: write` and `attestations: write`, so
triggering it is equivalent to publishing a signed, attested image as this repository. Epic #111
narrowed *how* that gets triggered by accident. **It did not narrow who can do it deliberately.**

**Correcting this entry's original framing, because it was wrong twice.** It said "write access is
publish access" and proposed rulesets as the answer. The collaborators are all **`admin`**, not
writers — and an admin can edit, disable or delete a repository-level ruleset, publish, and restore
it. Its trigger also read *"a second person gets write access. Do it before that, not after"*; by
the time anyone checked there were **three admins**, so the trigger had already fired twice and the
instruction was moot.

#### What epic #111 closed

- **#110** — `main` requires a pull request and all 7 CI checks, bypass list empty. This also fixed a
  live defect: Dependabot minor/patch PRs were merging **before CI finished** (PR #93 landed 74
  seconds ahead of its own build) because `dependabot-auto-merge.yml` depended on branch protection
  that did not exist.
- **#112** — `refs/tags/v*` can never be re-pointed or force-pushed **by anyone, admins included**
  (`current_user_can_bypass: never`, verified against a real push). Deletion is admin-only on
  purpose: without that escape a wrong-commit tag could never be cleared and the version would be
  burned.
- **#113** — all three rulesets are exported to `.github/rulesets/*.json`, so the policy is
  reviewable, diffable and restorable.
- **#116** — a release tag must name the actual release merge (`scripts/check-release-merge.sh`).
  This closed a hole DEP-2 never named: the publish gates could not tell the release merge from any
  later commit that still carried the same version, so a mistyped tag shipped the wrong tree under
  an immutable `:X.Y.Z` and reported success.

Net effect: a **mistake-guard**. Not a boundary.

#### What remains open — do not read the above as publish access being constrained

**The branch-dispatch route is untouched, and it preserves the entire original capability.** GitHub
loads a workflow definition from the ref that triggered it, so anyone who can push a branch can push
one whose `publish-image.yml` has every gate removed, dispatch it, and run arbitrary code holding
`packages: write` + `id-token: write` + `attestations: write`. Nothing shipped in #111 touches this.
It is not a "reduced" residual and should not be described as one.

Two smaller residuals worth stating so nobody over-trusts what landed:

- Required status checks gate **broken** code, not **hostile** code. The check names come from this
  repo's own workflow file, so a PR can redefine those jobs as no-ops and satisfy them.
- A repo admin can delete any of the three rulesets, act, and recreate it. Nothing detects that —
  see **DEP-4**.

- **What:** Decide and enforce who may publish, from outside the repository. In rough order of cost:
  - **Organisation-level rulesets** — the option this entry originally missed, and the cheapest path
    to something that is actually a boundary. An org ruleset is owned at the org, so repo admins
    cannot edit or delete it. Needs someone to hold org-owner separately from repo-admin to mean
    anything.
  - **Reduce the admin list.** All three collaborators are `admin`; day-to-day work needs `write` or
    `maintain`. This is the single highest-leverage change available, because it is what makes every
    repo-level ruleset bind the people it is aimed at. It is a **people decision**, not a config one.
  - **Move the publishing credential out of `GITHUB_TOKEN`.** Remove the repo's push access to the
    GHCR package and publish with a GitHub App token held as a secret in a protected `release`
    environment with required reviewers. This is the only design that survives a workflow edit —
    strip `environment:` and the credential is gone. Costs ongoing rotation.
  - **`workflow_run`-triggered privileged job**, so the privileged definition always comes from the
    default branch and the tag push is a mere signal. Changes the release runbook.
- **Why:** the routes that remain open are open to *intent*, and no in-repo configuration closes
  them. Anything that can be edited by the people it constrains is a speed bump.
- **Cons:** each option trades convenience or trust for a boundary. Org rulesets need org-level
  ownership to be separate; shrinking the admin list is a conversation; the App-token design adds a
  credential to rotate and an approval step to every release.
- **Trigger:** a contributor **outside the current three** gets write access, or the moment you want
  a boundary rather than a mistake-guard. Unlike the original trigger, this one has not fired yet.

### DEP-3: SBOM verification passes on one architecture — DEFERRED (raised 2026-08-12, PR #108)
`publish-image.yml` generates a per-architecture SPDX SBOM and attests **both against the index
digest**. They therefore share a subject *and* a predicate type, so
`gh attestation verify --predicate-type https://spdx.dev/Document` is satisfied by **either one**.
A digest carrying only the amd64 SBOM verifies exactly like a complete one — for the workflow's own
gate and for any operator following `docs/deployment.md`.

Not reachable on the normal path, which is why this is deferred rather than fixed: neither attest
step sets `continue-on-error`, so a failed second attestation fails the job *before* promotion,
`:X.Y.Z` never gets written, the pre-build guard reports `absent`, and the next run rebuilds.
Reaching a half-attested digest under a release tag needs someone to attach `:X.Y.Z` by hand —
which is **DEP-2**, not this.

- **What:** either attest each SBOM against its own platform-manifest digest and verify both
  explicitly, or produce one genuine multi-architecture SBOM and attest that.
- **Why:** "this release has an SBOM" should not be true when half of it is missing. The claim is
  operator-facing — `docs/deployment.md` tells people to verify the SPDX predicate by name
  precisely so a missing SBOM cannot pass.
- **Cons:** per-platform attestation reverses a deliberate #102 decision, documented in the
  workflow: both SBOMs attach to the index because *that* is what a release tag resolves to and
  what `gh attestation verify oci://…:X.Y.Z` presents. Splitting them means an operator verifying
  the tag no longer sees a complete set without knowing the per-arch digests. The single-SBOM route
  avoids that but needs Trivy to emit a merged document, or the workflow to merge two.
- **Context:** raised by a Codex review of PR #108 and rejected there with these reasons — release
  work is the wrong place to redesign the attestation strategy. The observation itself is correct
  and was confirmed by hand: both SBOM statements are `https://spdx.dev/Document/v2.3` on the same
  index subject, and the short predicate string matches them (verified against a real digest with
  `gh` 2.97).
- **Trigger:** DEP-2 is addressed (removing the manual-tag premise that currently makes this
  unreachable), or a consumer needs a provably complete per-release SBOM — a compliance question
  about what ships in the arm64 image is the likely shape.

### DEP-4: Nothing detects a weakened ruleset — DEFERRED (raised 2026-08-13, PR #118)
Three rulesets now carry real guarantees (see DEP-2), and `.github/rulesets/*.json` records what they
should be. **Nothing compares the two.** A repo admin can weaken or delete a ruleset, act, and
recreate it, and this repository will not notice — the only trace is the organisation audit log,
which nobody reads on a schedule.

That gap is what keeps the DEP-2 claim "bypassing requires an *audited* settings change" honest only
in the narrow sense that the audit log exists. It is not detection.

- **What:** a scheduled job that fetches the live rulesets, normalises them the way
  `scripts/export-rulesets.sh` does, and fails on any diff against the committed JSON.
- **Blocker, and it is the reason this is deferred rather than done — do not rediscover it:**
  **`administration` is not a valid `GITHUB_TOKEN` `permissions:` scope.** A workflow cannot read
  rulesets with the built-in token at all. Detection therefore needs a fine-grained PAT or a GitHub
  App with `administration: read`, provisioned and rotated. Verified against GitHub's own docs after
  a Codex review of PR #108 caught the claim that it was possible.
- **Also required, easy to miss:**
  - **A `schedule:` trigger.** Ruleset edits do not fire any workflow event, so a push-triggered job
    would only notice drift the next time somebody happened to push.
  - **The drift job must itself be a required check**, or a PR can land while it is red.
  - **Canonicalisation.** The API returns server-owned fields (`id`, `created_at`,
    `current_user_can_bypass`, …); `export-rulesets.sh` already strips exactly these, so reuse its
    filter rather than writing a second one that can disagree.
- **Cons:** a credential to hold and rotate, for a check that detects rather than prevents — an admin
  can disable the detector too. It raises the cost of quietly weakening a ruleset from "one settings
  change" to "two settings changes, one of which is a workflow edit visible in a diff".
- **Context:** scoped out of #113 deliberately. That PR delivered the reviewable, restorable record,
  which is most of the value with none of the credential cost. `.github/rulesets/README.md` states
  the missing piece so a reader does not assume drift is covered.
- **Trigger:** DEP-2 moves to an org-level ruleset or a reduced admin list (at which point the
  detector guards something that is actually a boundary), or a ruleset gets weakened once and nobody
  notices for a while.

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
- **DEP-1 — Publish an official container image.** DONE (epic #100; #101 CI build, #102 publish
  workflow, #103 adoption in 3.1.0). `ghcr.io/octoverse-id/octonomy` publishes multi-arch
  `:X.Y.Z` / `:X.Y` / `:latest` on a `vX.Y.Z` tag — smoke-tested per architecture, SLSA provenance
  and per-arch SPDX SBOM attested, anonymous-pull checked before any tag is promoted — plus
  `:edge` per green `main` build (amd64, unattested, **unsupported**). `deploy/` and
  `docs/deployment.md` pull by default; building it yourself stays documented.
  `make version-check` guards the example references. **Rebuild automation is won't-do** for now:
  it requires deciding what a rebuilt `:X.Y.Z` means, and different bytes under a shipped version
  breaks the one promise that cannot be walked back. Who may publish is DEP-2, still open.
- **NS-7 — Post-burn-in flag + index cleanup.** **Won't-do** (reframed). The five
  `OCTONOMY_NAMESPACE_*` flags and the E010–E016 dependency check are **permanent operator
  configuration**, not rollout scaffolding — each deployment uses them for its own staged rollout and
  kill-switch. There is no maintainer-side "remove the flags" task; dropping them after a burn-in is
  a local operator decision.
