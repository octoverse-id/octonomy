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

**These are two different problems and one of them cannot be solved by polling.** Getting that
backwards is how this entry gets implemented into a false sense of security:

| Case | What it looks like | Caught by |
| --- | --- | --- |
| **Persistent drift** | someone weakens a ruleset and leaves it that way, or forgets to re-export after a legitimate change | a scheduled live-vs-JSON diff |
| **Transient weaken → act → restore** | the DEP-2 residual case: relax the rule, publish, put it back | **only** event or audit-log monitoring |

A snapshot comparison **cannot** see a change that reverts before the next sample, and nothing forces
the actor to wait. So a scheduled diff alone does not address the case in this entry's own opening
paragraph. Caught by a Codex review of PR #119, which was right: the first draft here proposed the
schedule and then claimed it covered delete/act/recreate.

- **What, part 1 — persistent drift:** a job that fetches the live rulesets, normalises them the way
  `scripts/export-rulesets.sh` does, and fails on any diff against the committed JSON.
- **What, part 2 — transient changes:** subscribe to the **`repository_ruleset`** webhook
  (`created` / `edited` / `deleted`) or poll the organisation audit log, and alert on any event whose
  actor is not an expected release action. This is the half that actually covers the DEP-2 residual.
  Confirmed the event exists in GitHub's webhook reference; note that subscribing to it *also*
  requires a GitHub App with **Administration** read, so it shares the blocker below rather than
  routing around it.
- **Blocker, and it is the reason this is deferred rather than done — do not rediscover it:**
  **`administration` is not a valid `GITHUB_TOKEN` `permissions:` scope.** A workflow cannot read
  rulesets with the built-in token at all. Detection therefore needs a fine-grained PAT or a GitHub
  App with `administration: read`, provisioned and rotated. Verified against GitHub's own docs after
  a Codex review of PR #108 caught the claim that it was possible.
- **Also required, easy to miss:**
  - **A `schedule:` trigger** for part 1. Ruleset edits fire no *workflow* event, so a push-triggered
    job would only notice drift the next time somebody happened to push.
  - **Do NOT make a schedule-only job a required status check.** A scheduled workflow never reports a
    check run against a pull request's head commit, so requiring it would block **every** PR forever
    waiting for a check that cannot arrive. Either give it a `pull_request` trigger as well — which
    is independently useful, since it verifies a PR that edits `.github/rulesets/*.json` against
    live — or leave the scheduled run as an alert and do not require it. A `pull_request` trigger also
    means the credential must not be exposed to fork PRs.
  - **Canonicalisation.** The API returns server-owned fields (`id`, `created_at`,
    `current_user_can_bypass`, …); `export-rulesets.sh` already strips exactly these, so reuse its
    filter rather than writing a second one that can disagree.
- **Cons:** a credential to hold and rotate, for something that detects rather than prevents — an
  admin can disable the detector too. Be honest about the ceiling: **it does not meaningfully raise
  the cost of a deliberate bypass**, because the actor can act inside the polling window (part 1) or
  disable the webhook (part 2). Its real value is catching *accidents* and *drift left behind*, which
  is a smaller and more truthful claim than the one this entry originally made.
- **Context:** scoped out of #113 deliberately. That PR delivered the reviewable, restorable record,
  which is most of the value with none of the credential cost. `.github/rulesets/README.md` states
  the missing piece so a reader does not assume drift is covered.
- **Trigger:** DEP-2 moves to an org-level ruleset or a reduced admin list (at which point the
  detector guards something that is actually a boundary), or a ruleset gets weakened once and nobody
  notices for a while.

### DEP-5: The docs UI bundles add ~35 MB to the image — ACCEPTED (raised 2026-09-03, issue #146)
Self-hosting Swagger UI and Redoc (#146) grew the image from 191 MB to 226 MB uncompressed
(~13 MB on the wire). Roughly 15 MB of the collected-static half is source maps and their hashed +
gzipped copies: `swagger-ui-bundle.js.map` (1.9 MB), `redoc.standalone.js.map` (3.7 MB),
`swagger-ui.css.map` and `swagger-ui-standalone-preset.js.map`.

**Trimming them is not the one-liner it looks like.** `collectstatic --ignore '*.map'` fails the
build: `swagger-ui.css` and `redoc.standalone.js` carry `sourceMappingURL` references, and Django's
manifest post-processor resolves those, raising `ValueError` for a target that was not collected.
Only the two *unreferenced* maps (`swagger-ui-bundle.js.map`, `swagger-ui-standalone-preset.js.map`,
2.2 MB raw) could be dropped safely — and an `--ignore` in the `Dockerfile` alone would give the
container and VPS channels different manifests, which is the channel divergence
`config/settings_pytest.py` exists to warn about. Doing it properly means a project
`collectstatic` override, i.e. real machinery for a partial saving.

- **Decision:** ship all of it. The supply-chain and air-gap wins are the point of #146; 35 MB on a
  191 MB base is not what makes this image expensive to move.
- **Effort:** S (human ~2h) for the command override. **Priority:** P4.
- **Trigger:** an operator reports image size as a real constraint (edge/IoT registry quotas,
  metered pull bandwidth), or drf-spectacular-sidecar's maps grow materially past their current
  ~6 MB.

### DEP-6: The version stamps are hand-written, and their gate is untested shell — DEFERRED (raised 2026-09-04, issue #160)
Cutting a release means editing the version in 16 places across 11 files. 3.2.0 closed the part
that was genuinely dangerous — `.env.example`, `SECURITY.md` and `uv.lock` were verified by nothing
and now are — but it closed it with ~20 more lines of untested inline shell in the `version-check`
Makefile target, and it did not remove a single hand edit.

- **What:** Two halves, and they can land independently.
  1. **Extract the verifier.** Move `version-check`'s inline shell into
     `scripts/check-version-stamps.sh` with a `tests/tooling/test_check_version_stamps.py`,
     following `check-image-refs.sh` and its test exactly. The target keeps its name and its
     place in `release-check`.
  2. **Add a writer.** `make version-bump X.Y.Z` that *writes* every stamp the verifier checks,
     so the release step becomes one command plus a review of the diff.
- **Why:** The verifier half is the one with real risk. `version-check` now performs eleven
  distinct assertions and **not one of them is covered by a test** — only `check-image-refs.sh`,
  the script it delegates to, is. That shell guards a one-way door: the `release tags: never move`
  ruleset has no bypass, so a gate that silently stops asserting something is discovered after the
  bytes are immutable. The writer half is friction rather than risk, but it is where the hand
  edits actually go away, and this release proved the hand-maintained list drifts — `docs/release.md`
  step 2 was missing the `refs/tags/vX.Y.Z` site the gate had been enforcing all along.
- **Pros:** Brings the release gate up to the same bar as every other script in `scripts/`. The
  writer and the verifier stay separate programs, so the writer never certifies its own output —
  which is what keeps `version-check` meaningful. Makes prerelease behaviour testable; today the
  PEP 440 / SemVer split (`uv.lock` holds `3.2.0rc1`, `.env.example` holds `3.2.0-rc.1`) is
  asserted only by whoever last cut a prerelease.
- **Cons:** Zero user-visible benefit; it is entirely release-process work. The repo's
  release-tooling ratio puts this at roughly 150 script lines and 250 test lines
  (`check-image-refs.sh` 92/151, `check-release-merge.sh` 106/182, `resolve-latest-tag.sh` 111/168,
  `check-tag-unpublished.sh` 144/223). A writer is also the one place a bug produces a
  *consistent* wrong answer that the verifier then happily passes — the prerelease branch is the
  likeliest spot for that, which is an argument for doing the verifier's tests first.
- **Context:** Start from `Makefile` `version-check` (the full contract) and mirror
  `scripts/check-image-refs.sh`, which already models per-file presence, tag classification and
  refusing a permissive default. The verifier and any future writer must derive their file lists
  from one place, or the new script reintroduces exactly the drift it exists to remove. Note the
  two version *forms* in play: `uv.lock` and `pyproject.toml` carry PEP 440, everything else
  carries SemVer, and `version-check` converts between them.
- **Effort:** M (human ~1 day / CC ~1h) for both halves; S for the verifier extraction alone.
  **Priority:** P3. **Depends on:** nothing.
- **Trigger:** the next release where a stamp is missed, **or** the next file added to any
  `version-check` file list — that is the moment the lists can silently disagree. Deliberately not
  a `release-trigger:` token: this is friction and test debt, not a correctness gap, and the gate
  does hold meanwhile.

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

### CFG-2: Secret boot guard does not judge strength — DEFERRED (raised 2026-09-01, issue #147 CLOSED; residual tracked here)
`config/settings.py` guards `DJANGO_SECRET_KEY` and `SERVICE_TOKEN_PEPPER` at import time, and those
two `raise ImproperlyConfigured` statements are the only **boot-time** enforcement these two values
get. Each tests exactly two things: the value is non-empty, and it is not the local-dev literal.
Nothing else. So `DJANGO_SECRET_KEY=thisisthedjangomostsecretkey` boots with `DJANGO_DEBUG=false`,
and so does a whitespace-only `" "` (a non-empty string, so it passes `not SECRET_KEY`). Sub-issue
#148 shipped the documentation half (PR #152): the docs now describe the guard that exists instead
of promising a strength check that never existed. This entry is the half that was not shipped.

- **What:** One `secret_is_weak()` predicate consumed by **both** boot guards in `config/settings.py`
  **and** `_check_secret_key` / `_check_service_token_pepper` in `octonomy/core/checks.py` — testing
  length, distinct characters, Django's `django-insecure-` prefix, and `.strip()`-blank — with an
  `OCTONOMY_ALLOW_WEAK_SECRETS` escape hatch. That hatch must bypass **only** the new strength
  heuristics, for an operator whose value is genuinely random but short. It must never bypass the
  two tests that exist today: an empty value and the local-dev literal stay unbootable regardless.
  Decide explicitly whether it also suppresses `octonomy.E001`/`E002` under `check --deploy` — see
  Context; the answer determines whether those two branches are reachable at all.
- **Why:** The gap was observed in practice, not hypothesised: a working `deploy/docker/.env` in this
  project carried `DJANGO_SECRET_KEY=thisisthedjangomostsecretkey` and started normally. A guessable
  `SECRET_KEY` undermines everything Django signs with it. Scope it honestly before prioritising:
  this project exposes no password-reset or signed-cookie surface, `SESSION_ENGINE` is the database
  backend so a forged cookie would still need a matching `django_session` row, and the REST API uses
  no sessions at all. The concretely reachable impact today is admin session data, which is signed
  with `SECRET_KEY` — and the admin is off by default in production. That narrowness is part of why
  deferring was defensible; it is not an argument that the guard should stay as it is. Note also
  that a weak `SERVICE_TOKEN_PEPPER` is the *lesser* problem despite
  keying every token hash, because `generate_service_token` mints `secrets.token_urlsafe(32)` — a
  known pepper does not make 256-bit tokens recoverable, and it needs a database leak first.
- **Pros:** One home for a rule that currently has four, so the next patch cannot drift; catches the
  padding case (`"a" * 60`) that a bare length rule misses; forces a decision on `octonomy.E001` /
  `E002`, which are dead today (see Context).
- **Cons:** Every short secret fixture in the repo must be lengthened first, across four files. Via
  the boot guards: `_PROD_BASE` in `tests/admin/test_admin_settings.py` (18 chars, and it feeds
  *every* `DEBUG=false` subprocess in that file), the two docker-smoke blocks in
  `.github/workflows/ci.yml`, and the two published-image smoke blocks in
  `.github/workflows/publish-image.yml` — that last one is the release-tag gate, so a break there is
  discovered at tag time. Via `production_settings_check`, if the predicate is shared as proposed:
  the two `override_settings` fixtures in `tests/core/test_checks.py` that pass `release-secret` /
  `release-pepper` (14 chars each). The job-level `SERVICE_TOKEN_PEPPER: ci-pepper` in `ci.yml` is
  *not* affected — those jobs set `DJANGO_DEBUG: "true"`, so neither path runs. Count the call sites
  from the source when the work starts rather than trusting a number here. Without the escape hatch
  this could also refuse to start a deployment already running a short-but-random secret.
- **Context:** Absorbs three residuals that are the same two lines and belong in the same PR: the
  `.strip()` whitespace check; extracting `_check_secret_key` / `_check_service_token_pepper`, the
  helpers behind `production_settings_check`. That wrapper is the registered check and carries
  `deploy=True`, so none of it runs at boot; it still does useful work under `manage.py check
  --deploy` through `_check_allowed_hosts` and `_check_database_engine`. But its two *secret*
  branches — `octonomy.E001` and `E002` — are **unreachable in any real configuration**, because the
  settings import raises on the same conditions first. Verified both ways: with `DEBUG=false` and the
  local-dev default, `check --deploy` never gets to run; with `DEBUG=true` the wrapper returns `[]`
  on its first line. `tests/core/test_checks.py` reaches them only via `override_settings`: a test
  proving dead branches work.

  **Sharing the predicate does not by itself revive them, and the plan must say how it resolves that**
  (raised in review of PR #154). The reachable case is narrow. Under `DEBUG=false`, `E001` can only be
  emitted when *all* of these hold: the value passes the empty and local-dev-literal tests (the hatch
  never bypasses those), it fails **only** the new strength heuristics,
  `OCTONOMY_ALLOW_WEAK_SECRETS` is set so boot proceeds, the *other* secret also passes its own boot
  guard — otherwise that one raises first and the check is never reached — and the deploy check
  ignores the hatch. Three ways to resolve it, pick one deliberately:
    1. **The hatch unblocks boot but does not silence the check** (preferred). `E001`/`E002` then mean
       something precise — "you opted out of the block, and this is still weak" — and they are the
       only place that survives to say it, since `security.W009` is a warning that covers `SECRET_KEY`
       alone. Smallest change; keeps a deploy-pipeline signal for an operator who used the hatch.
    2. **Delete `E001`/`E002`** and their `override_settings` tests as genuine duplication, leaving
       the boot guard as the single enforcement point.
    3. **Move enforcement out of settings-import into a non-deploy-tagged system check**, the
       `namespace_flag_dependencies` pattern whose docstring already states the rule. Cleanest on
       paper, but it weakens the guarantee. The raise currently fires on *any* import of settings,
       including the serving path. A system check does not: Django runs checks before most management
       commands (`requires_system_checks` defaults to `"__all__"`), but `gunicorn config.wsgi` imports
       the application without running any. Both shipped channels happen to be covered —
       `docker-entrypoint.sh` runs `manage.py check` before `exec`, and the systemd unit runs it in
       `ExecStartPre` — so the exposure is anyone serving without those: a bypassed entrypoint, or
       gunicorn invoked directly. Do not take this option without closing that hole.

  Also absorbed: a `DJANGO_SECRET_KEY` rotation runbook,
  which is missing while the pepper's is documented. Rotation is cheaper here than operators expect:
  it invalidates admin sessions, because the database session backend signs session data with
  `SECRET_KEY`, but it does **not** touch CSRF tokens (`CSRF_USE_SESSIONS` is false and Django's CSRF
  secret is independent of `SECRET_KEY`) and does not touch any service token, which are peppered
  HMACs. The REST API uses no sessions at all. Reuse
  Django's own constants from `django.core.checks.security.base` (`SECRET_KEY_MIN_LENGTH`,
  `SECRET_KEY_MIN_UNIQUE_CHARACTERS`, `SECRET_KEY_INSECURE_PREFIX`) behind a parity test, so a Django
  upgrade that moves the floor is caught rather than silently diverging; follow the
  `_NAMESPACE_FLAG_RULES` table shape in `octonomy/core/checks.py` and the hermetic
  `_import_settings` harness. Note that `security.W009` already reports *some* weak `SECRET_KEY`
  shapes under `manage.py check --deploy`, but it is a warning, is deploy-tagged, and covers
  `SECRET_KEY` alone.
- **Effort:** M (human ~1 day). **Priority:** P2. **Depends on:** nothing.
- **Trigger:** `release-trigger: 4.0.0` — checked by the "Deferred work triggered by this release"
  step in `docs/release.md`. That token is on one line on purpose: the step greps for it, and a
  trigger phrased only in prose stops being greppable the moment the sentence rewraps. It also fires
  on the next edit to either boot guard in `config/settings.py` for any reason, whichever comes
  first — the `gstack-shortcut` markers sit on those exact lines, and a third patch to this guard
  inside one minor version means extract the predicate rather than add another clause. Deliberately
  **not** "another incident": one already happened, and it is what #147 reports. A trigger already
  satisfied on the day it is written is how the DEP-2 entry above rotted.

  **Moved from `3.2.0` to `4.0.0` on 2026-09-04, while cutting 3.2.0 (issue #160).** The reason is
  semver, not scheduling. This entry's own Cons note the guard "could refuse to start a deployment
  already running a short but random secret" — and a deployment that boots today, then does not
  boot after upgrading unless its operator acts, is exactly what [`versioning.md`](docs/versioning.md)
  defines as a breaking runtime/deployment requirement: a **major**. Landing CFG-2 in 3.2.0 would
  have made 3.2.0 a major by the project's own policy without anyone deciding that, so the token now
  names the release where the bump is already on the table. Two consequences for whoever picks this
  up. The escape hatch does not rescue a minor — requiring an operator to set
  `OCTONOMY_ALLOW_WEAK_SECRETS` before the service will start *is* the breaking action. And shipping
  it warning-only stays a legitimate alternative, trading the boot guarantee for the smaller bump.

### CFG-3: Webhook signing secret accepts the shipped `CHANGE_ME` placeholder — DEFERRED (raised 2026-09-01, found while reviewing #147)
`OCTONOMY_WEBHOOK_SIGNING_SECRET` has no boot guard **and** no system check — unlike
`DJANGO_SECRET_KEY` and `SERVICE_TOKEN_PEPPER`, which at least reject an empty or default value at
import. (It is not the only unguarded credential the repo ships a placeholder for — `DATABASE_URL`
and `POSTGRES_PASSWORD` carry `CHANGE_ME` too — but it is the one whose placeholder silently
weakens a signature rather than failing to connect.)
`transport_from_settings` in `octonomy/events/dispatch.py` rejects an empty value, but only
inside the dispatcher process, so a misconfigured deployment serves happily and fails later — and on
Kubernetes the dispatcher is a CronJob, so that surfaces as a failed job rather than a failed deploy.

- **What:** Validate the webhook triple when `OCTONOMY_OUTBOX_TRANSPORT=webhook`, via a system check
  registered **without** `deploy=True` — follow `namespace_flag_dependencies` in
  `octonomy/core/checks.py`, whose docstring already states the rule — so it fires at boot. Reject
  the published `CHANGE_ME` placeholder. Reuse CFG-2's `secret_is_weak()` if that lands first.
- **Why:** `CHANGE_ME` is published in `deploy/.env.production.example` and
  `deploy/kubernetes/secret.example.yaml`. It is non-empty, so `transport_from_settings` accepts it
  and `_webhook_signature` HMACs every event body with a publicly-known string. `X-Octonomy-Signature`
  becomes decorative, and a consumer verifying it would accept forged events.
- **Pros:** Closes the same class of hole as #147 on the webhook signing secret; a boot-time check
  means a misconfigured webhook deployment never starts serving.
- **Cons:** A new check plus its tests, inert for every deployment on the default `logging` transport.
- **Context:** Found during the `/plan-ceo-review` of #147 while mapping which secrets are guarded.
  It is a different secret and a different subsystem, so it was kept out of #147's scope rather than
  widening that fix.
- **Effort:** S (human ~3h). **Priority:** P3. **Depends on:** benefits from CFG-2's predicate, not
  blocked by it.
- **Trigger:** the first deployment to set `OCTONOMY_OUTBOX_TRANSPORT=webhook`, or CFG-2 landing —
  fold it in there.

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
