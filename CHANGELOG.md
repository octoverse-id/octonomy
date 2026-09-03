# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation
- **Corrected the operator runbooks for static assets** (#145). The VPS/systemd runbook never
  mentioned `collectstatic`, on install or on upgrade. The upgrade omission was the one that
  bit: an upgrade moving Django or django-unfold ships new templates against the old assets,
  `STATIC_ROOT` is still non-empty so `octonomy.W002` stays quiet, and nothing warns. Both
  steps are now explicit, with the ordering spelled out — collecting *after* the restart
  leaves the running workers disagreeing with each other (measured: ten requests to
  `/admin/login/` on a two-worker container returned five 500s, two 200s, then three more
  500s).
- Added an **Enabling the admin console** recipe covering flag → superuser → verify on all
  three channels, so the journey is one section instead of three spread across two documents.
  Post-deploy verification now includes static delivery, and checks the *page* as well as the
  asset — a stale collect shows up as a 500 while the asset path still answers 200.
- Recorded on the 3.0.0 entry that its "no WhiteNoise" decision has since been reversed, and
  noted in `deploy/kubernetes/deployment.yaml` that static needs no sidecar, static
  Deployment, shared volume or Ingress path.

### Changed
- **Removed the `location /static/` alias from `deploy/systemd/nginx-octonomy.conf`.** Static
  now falls through to the app on the VPS channel too, which puts all three channels on one
  caching contract: WhiteNoise stamps `immutable` on content-addressed filenames and a
  conservative 60s on the unhashed originals, and negotiates the pre-compressed files
  `collectstatic` writes. The alias was removed rather than retuned because a single `expires`
  value cannot serve both filename kinds — `7d` under-caches the hashed assets, and
  `max`/`immutable` pins the unhashed paths in browser caches indefinitely so the next upgrade
  never reaches those clients. Fronting `/static/` from nginx or a CDN remains supported as an
  optional override, and such a block still wins over the app; the file explains what taking
  that on now requires.

### Added
- **CI now asserts that a real image actually serves its static assets.** The bug fixed
  above survived a full release because no deploy channel was ever booted and probed end to
  end. The existing `docker` job already builds the production image and polls
  `/health/live`; it now also starts it with `OCTONOMY_ADMIN_ENABLED=true` and runs
  `scripts/assert-static-served.sh`, which renders `/admin/login/`, extracts a **hashed**
  asset URL from that HTML and fetches it, checks the browsable API's own asset, and
  asserts that no `Access-Control-Allow-Origin` header is present at all. Probing a known
  unhashed path would prove nothing — `collectstatic` writes both names, so it answers 200
  even with a broken manifest. What this establishes is that the built image carries a
  usable production manifest and that those two surfaces render; it does not render every
  admin page, and the test suite cannot cover this class because it runs the plain
  staticfiles backend.
- `make static-check` (`scripts/check_static_serving.py`), a drift gate over the deploy
  channels: every channel file must still declare how static is served, and no Compose or
  Kubernetes manifest may mount anything over `/app` or `/app/staticfiles`, which would hide
  the assets baked into the image. Mounts are found by parsing the YAML rather than matching
  lines, so `volumes`, `tmpfs`, short and long syntax, flow style and anonymous volumes are
  all covered. It proves nothing about an HTTP response — that is the CI job's job — so it
  is labelled accordingly in its own header.

### Fixed
- **Static assets are now served by the app itself.** With `DJANGO_DEBUG=false` nothing in
  the Octonomy process answered `/static/*`, so every request 404'd: the admin console
  rendered unstyled and unusable, and DRF's browsable API — which is enabled by default on
  every view, not opt-in — lost its own CSS/JS. The assets were already baked into the
  container image; they were simply unreachable, on all three production channels
  (`deploy/docker`, `deploy/kubernetes`, and `deploy/systemd` on a clean install).
  `whitenoise.middleware.WhiteNoiseMiddleware` now runs directly after `SecurityMiddleware`
  and serves `STATIC_ROOT`, with `STORAGES["staticfiles"]` set to WhiteNoise's hashed,
  compressed `CompressedManifestStaticFilesStorage`.

  This **reverses** the "Octonomy bundles no static-serving middleware — collect
  `STATIC_ROOT` and serve it externally" decision recorded under 3.0.0 below. That
  instruction could not be carried out on the container channels at all: the assets live
  inside the image with no volume, no export step, a non-root user, and a read-only root
  filesystem. Static responses carry no `Access-Control-Allow-Origin` header
  (`WHITENOISE_ALLOW_ALL_ORIGINS = False`). The `location /static/` alias that
  `deploy/systemd/nginx-octonomy.conf` shipped at the time answered first on that channel;
  it has since been removed (see Changed above), so all three channels now reach WhiteNoise
  by default and an external static route is an operator-added override. REST
  responses are unchanged — WhiteNoise only ever acts on paths under `STATIC_URL`.

### Added
- System check `octonomy.W002`: a non-blocking warning at boot when the admin is enabled
  with `DEBUG=false` but `STATIC_ROOT` cannot back it — missing, empty, holding no readable
  file (an interrupted `collectstatic`, or a tree the service account cannot read), or
  lacking the `staticfiles.json` the manifest backend needs. A host that never ran
  `collectstatic` is told at startup instead of failing on an operator's first page load.
  Registered as an ordinary check, not a deploy check, so the plain `manage.py check` that
  `docker-entrypoint.sh` and the systemd `ExecStartPre` already run will surface it. It
  stays silent for a populated-but-stale `STATIC_ROOT`; that case is a runbook fix (#145).
- `OCTONOMY_STATIC_URL` and `OCTONOMY_FORCE_SCRIPT_NAME` for subpath deployments (the app
  mounted at `/octonomy` rather than `/`). `STATIC_URL` is now absolute by default
  (`/static/`) instead of relative. A relative value is script-prefixed by Django on first
  read and then cached for the life of the process, so which prefix got baked in depended
  on which request arrived first — and a Kubernetes health probe reaches the pod with no
  prefix at all. Set both variables together to serve a subpath correctly; the effective
  value is unchanged for every deployment that is not on a subpath. Both are validated at
  boot: a relative `OCTONOMY_STATIC_URL`, or an `OCTONOMY_FORCE_SCRIPT_NAME` carrying a
  scheme, host, query or fragment, refuses to start. Django prepends `FORCE_SCRIPT_NAME`
  to every `reverse()` result verbatim, so a value like `https://evil.example` would
  otherwise render the admin login form posting to another host. A second check,
  `octonomy.W003`, warns when `FORCE_SCRIPT_NAME` is set but `STATIC_URL` is not under it —
  the half-configured subpath that links assets outside the app's own mount. A warning
  rather than a refusal, because whether it breaks depends on proxy routing the process
  cannot see.

### Security
- **The deployment docs no longer claim the service rejects a weak secret — because it never
  did.** `deploy/.env.production.example` and `docs/deployment.md` stated that a half-edited
  production env file "fails fast instead of running on a weak/publicly-known secret". The boot
  guard in `config/settings.py` tests two things per secret: the value is non-empty, and it is not
  the local-dev literal. It has never judged strength, so
  `DJANGO_SECRET_KEY=thisisthedjangomostsecretkey` starts normally with `DJANGO_DEBUG=false`, and so
  does a whitespace-only `" "`. An operator who filled the template with an obvious placeholder was
  told they were protected when they were not.

  **Runtime behavior is unchanged — this release corrects the documentation, not the guard.** No
  deployment that boots today will stop booting. Both files now name the exact rejected values
  (`local-dev-secret`, `local-dev-service-token-pepper`) and say plainly that a placeholder or a
  whitespace-only value is accepted. They also document Django's `security.W009` with its real
  limits rather than as a strength check: it tests three syntactic conditions (under 50 characters,
  fewer than 5 distinct characters, a `django-insecure-` prefix), measures neither entropy nor
  secrecy, so a long publicly-known string passes it and the absence of a warning is not evidence
  of a good key. Being a warning it does not on its own make `manage.py check --deploy` exit
  nonzero, so the post-deploy step now says to read the output rather than trust the exit status.
  `SERVICE_TOKEN_PEPPER` has no equivalent check anywhere and is described separately instead of
  sharing a paragraph that implied parity.

  This corrects wording that has stood since 3.0.1 below, which added the empty-`DJANGO_SECRET_KEY`
  rejection and described the guard more broadly than it behaves. Hardening the guard was
  considered and deliberately deferred — the same change would break every short secret fixture in
  the CI workflows and the test suite, and could refuse to start a deployment already running a
  short but random secret. That work
  is tracked as `CFG-2` in `TODOS.md`, with `CFG-3` covering the related
  `OCTONOMY_WEBHOOK_SIGNING_SECRET` gap. **Operators who supplied a hand-written secret should
  rotate it to a generated one** (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).

## [3.1.1] - 2026-08-26

A **patch** release: both live REST surfaces keep the same paths, fields, validation, and runtime
behavior. The OpenAPI changes restore scope fields that the API already always emitted and document
the existing `is_active=true` default; they do not add a capability. Python 3.12 remains supported,
there is no database migration, and rollback is a redeploy of 3.1.0.

### Security
- Updated the lockfile to Django 5.2.17 for PYSEC-2026-3717 and sqlparse 0.6.0 for
  CVE-2026-71491, CVE-2026-59894, CVE-2026-59893, and CVE-2026-54284. The declared Django and
  Python support ranges are unchanged.
- SHA-pinned the Docker build actions that can populate the publish cache, bounded every CI job
  with an explicit timeout, and expanded Dependabot coverage to include the privileged image-
  publishing composite action. Major GitHub Actions updates are now isolated for review instead of
  being bundled with security and maintenance updates.

### Fixed
- Restored `application_id`, `namespace_type`, and `namespace_id` as required, length-bounded
  fields in generated tag, alias, and vocabulary response schemas after drf-spectacular 0.30 began
  honoring serializer requiredness more precisely. The runtime payloads never stopped emitting
  these nullable scope fields.
- The publish workflow now refuses a version tag unless it targets the release merge where that
  version first appears. This prevents a later commit carrying the same package version from being
  published under the immutable release image tag.
- Corrected Dependabot to read the native `uv.lock`, detect the literal pinned Docker base image,
  use the correct `github-actions` ecosystem identifier in the auto-merge gate, and require review
  for base-image updates.

### Changed
- The official container image now ships **Python 3.14** (was 3.12). The supported runtime floor is
  unchanged — `requires-python` is still `>=3.12`, and CI continues to test both 3.12 and 3.14 — so
  this changes what you get when you *pull the image*, not what the project supports. It arrived as
  Dependabot PR #134, the first base image update Dependabot was ever able to see: the pin had been
  hidden behind an interpolated `ARG` that its Docker parser cannot resolve (#132).
- Refreshed locked runtime and development dependencies within their existing declared ranges,
  including Django REST framework, drf-spectacular, django-unfold, dj-database-url, pytest,
  pytest-cov, pytest-django, pip-audit, python-dotenv, and Ruff.
- Made repository protections reviewable and recoverable by recording the live main-branch and
  release-tag rulesets in the repository with a deterministic export script. The live GitHub
  rulesets remain the enforcement source of truth.
- Hardened release recovery guidance around immutable tags, draining in-flight publish runs, image
  verification, and attestation/source binding before a GitHub release is announced.

## [3.1.0] - 2026-08-11

A **minor** release: the REST contract is unchanged on both surfaces, and no request or response
shape moves. What is new is how you *get* Octonomy — there is now an official container image, and
the example deployments use it.

### Added
- Container images are now published to
  [GHCR](https://github.com/octoverse-id/octonomy/pkgs/container/octonomy). Tagging `vX.Y.Z`
  builds `linux/amd64` + `linux/arm64`, smoke-tests the pushed digest on **both** architectures,
  attaches SLSA provenance and a per-architecture SPDX SBOM as separate attestations, checks the
  image is anonymously pullable, and only then promotes the digest to `:X.Y.Z` — plus `:X.Y` and
  `:latest`, each only when no newer release claims it, so a backport or a re-run can never move a
  moving tag backward. Every green CI run on `main` also publishes `:edge` — amd64 only,
  unattested, and **unsupported**. Verify a release with:

  ```bash
  gh attestation verify oci://ghcr.io/octoverse-id/octonomy:<version> \
    --repo octoverse-id/octonomy \
    --signer-workflow octoverse-id/octonomy/.github/workflows/publish-image.yml \
    --predicate-type https://slsa.dev/provenance/v1
  ```

  `--signer-workflow` is not optional detail: `--repo` alone only validates the certificate's
  source *repository*, so it would accept a predicate signed by any workflow here. Needs `gh`
  2.51+. See [deployment.md](docs/deployment.md#verifying-what-you-pulled) for the SBOM predicate
  and the tag-binding option.

  A version tag is immutable: re-running a publish re-promotes the digest already published under
  that version (which is how a partially-promoted release recovers) and **fails** if the version
  resolves to different bytes. There is no overwrite switch — republishing different bytes under a
  shipped version is what a patch release is for.

- The example deployments now **pull** that image instead of asking you to build one.
  `deploy/kubernetes/` applies verbatim — no registry of your own, no `imagePullSecrets`, and no
  edit to any `image:` field — and `deploy/docker/compose.yaml` starts without a build step.
  Building it yourself is still documented, now as the alternative rather than the only path.

  Kubernetes was the reason this mattered: Compose users only ever needed `docker build` on the
  host they were already deploying to, but a cluster needs a registry it can pull from, which is
  an infrastructure prerequisite rather than a command.

### Changed
- `make version-check` now also verifies every published-image reference in the example configs,
  `docs/deployment.md`, and the `README.md` quickstart via `scripts/check-image-refs.sh`, so a
  release cannot ship examples pointing at the previous version — or at a typo'd tag. The gate
  asserts per-file presence, so a file that loses its reference entirely fails rather than
  passing silently, and it additionally rejects a **moving** tag (`:latest`, `:edge`) in the
  example deployments, which must pin an immutable `X.Y.Z`.
- `docs/release.md` gains a **mandatory verification gate** between pushing the version tag and
  publishing the GitHub release: the release is not announced until its image is confirmed
  published, anonymously pullable, and attested.

## [3.0.1] - 2026-08-05

A **patch** release: the REST contract is untouched (no path or schema diff on either surface), and
the OpenAPI `info.version` is the only version string that advances. `/api/v2` stays primary and
`/api/v1` stays supported.

It ships the self-hosting deployment guide and, with it, the boot guard the guide's templates depend
on. `deploy/.env.production.example` ships both required secrets **empty** so a half-edited file
fails closed — but on 3.0.0 only `SERVICE_TOKEN_PEPPER` actually rejected an empty value. An operator
who copied the template, hit the pepper error, set the pepper, and started the service would have
been running on a blank signing key. Both guards now fail closed, so the template's promise holds.

**Upgrade action:** if a deployment is currently running with an empty `DJANGO_SECRET_KEY` and
`DJANGO_DEBUG=false`, set a real value before upgrading or the service will not start. Generate one
with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. No database migration is
required.

**Rollback:** redeploy 3.0.0. No schema or data change to reverse.

### Added
- Self-hosting [deployment guide](docs/deployment.md) covering Docker Compose, Kubernetes, and a
  VPS/systemd box, with ready-to-edit example configs under [`deploy/`](deploy) — env template,
  Compose stack, Kubernetes manifests (namespace, ConfigMap, Secret template, migrate `Job`,
  `Deployment`, `Service`, `Ingress`, dispatcher `CronJob`), and systemd units plus an nginx reverse
  proxy. Every topology runs the same three processes — the Gunicorn API, a one-shot `migrate`, and
  the outbox dispatcher — off one build: a container image for Docker/Kubernetes, or a source
  checkout and virtualenv on the VPS path.

### Changed
- **An empty `DJANGO_SECRET_KEY` now refuses to boot when `DJANGO_DEBUG=false`**, matching the
  existing `SERVICE_TOKEN_PEPPER` guard. Previously only the literal `local-dev-secret` default was
  rejected, so a blank signing key could start the service — the container entrypoint runs plain
  `manage.py check`, which imports settings and so enforces this. **Operator action:** a deployment
  that was running with an empty `DJANGO_SECRET_KEY` will now fail to start; set a real value
  (`python -c "import secrets; print(secrets.token_urlsafe(64))"`) before upgrading.

## [3.0.0] - 2026-08-03

This release ships the optional, superuser-only [django-unfold](https://unfoldadmin.com/) admin
console and raises the runtime floor to **Python 3.12+** and the **Django 5.2 LTS** line
(`>=5.2.8,<5.3`). The runtime/deployment requirement is the breaking trigger for the package
**major**: Python 3.10/3.11 and Django 5.0/5.1 deployments cannot upgrade in place. The **REST
contract is unchanged** — `/api/v2` stays primary, `/api/v1` stays supported, and there is **no**
`/api/v3`; only the OpenAPI `info.version` advances to `3.0.0`.

There is **no migration to Octonomy's taxonomy data**. Enabling the admin creates Django's built-in
`auth`/`admin`/`sessions`/`contenttypes` tables via the standard `migrate`, and the admin is **off by
default in production** — it mounts only when `OCTONOMY_ADMIN_ENABLED` is true (defaulting to
`DJANGO_DEBUG`) and admits only active superusers. Serving the admin with `DEBUG=false` needs
`make collectstatic`.

**Rollback:** redeploy the prior 2.x application code/runtime if necessary (the built-in
admin/session tables may remain unused). As the immediate admin-surface rollback, set
`OCTONOMY_ADMIN_ENABLED=false`. Do **not** remove or mutate tenant taxonomy data as part of rollback.

### Added
- Optional, superuser-only Django admin console themed with
  [django-unfold](https://unfoldadmin.com/), mounted at `/admin/`. It is a thin operator
  interface over the headless REST service and is **off by default in production**: it mounts only
  when `OCTONOMY_ADMIN_ENABLED` is true (which defaults to `DJANGO_DEBUG`), and even then admits
  only active superusers. Bootstrap access with `python manage.py createsuperuser`.
- Service-backed taxonomy administration in the admin console. Vocabularies, tags, and tag aliases
  can be created, updated, deactivated (soft delete), and reactivated, and tag assignments can be
  idempotently created and removed — every write routes through the existing domain services, so it
  inherits tenant/application/namespace isolation, the namespaced-write kill-switch, soft-deletion
  and cascade semantics, idempotency, and the audit-log + outbox side effects exactly as the REST
  API does. Each mutation is attributed to `admin:<username>`. Domain rejections (cross-scope
  relationships, duplicate active slugs, disabled namespaced writes) surface as admin errors instead
  of HTTP 500s; scope fields are immutable after creation; and the stock hard-delete routes are
  disabled in favour of the auditable Deactivate/Reactivate actions.
- Read-only diagnostics in the admin console for audit logs, outbox events, and service
  clients/grants (view/list/search/filter only, with index-aligned filters). Service-token secrets
  are never exposed: the `ServiceClient` view uses a safe-field allowlist, so `hashed_key` (and any
  future sensitive column) never appears. Token creation and revocation remain in the existing
  management-command workflow.
- Curated the admin sidebar into domain groups — **Taxonomy**, collapsible **Diagnostics**, and
  collapsible **Access** — instead of Django's default per-app list, with per-model icons and a
  search box (`UNFOLD["SIDEBAR"]`). Also set `verbose_name_plural` on `Vocabulary` and `TagAlias`
  so the admin shows "Vocabularies"/"Tag aliases" instead of the naive "Vocabularys"/"Tag aliass".
- Added a site-header dropdown (`UNFOLD["SITE_DROPDOWN"]`) with quick links to the GitHub
  repository and the Swagger and ReDoc API docs, each opening in a new tab.
- Deploy system check `octonomy.W001`: a non-blocking warning when the admin is enabled with
  `DEBUG=false`, reminding operators it is a trusted development/operator interface rather than a
  public surface.
- `make collectstatic` target and `STATIC_URL`/`STATIC_ROOT` settings for serving the admin's
  static assets in production (no WhiteNoise or external service introduced). **Superseded:**
  that decision was reversed under [Unreleased] above — nothing served `STATIC_ROOT` on any
  container channel, so every asset 404'd with `DEBUG=false`. The app bundles WhiteNoise and
  serves them itself now.

### Changed
- **Runtime floor raised.** Minimum Python is now **3.12** (was 3.10) and Django is pinned to the
  **5.2 LTS line** (`>=5.2.8,<5.3`), the baseline required by the Unfold release. PostgreSQL CI now
  runs on Python 3.12 and 3.14 (3.10 dropped). This is a breaking runtime/deployment requirement and
  will drive the next package **major**, but the REST contract is unchanged: `/api/v2` stays primary
  and `/api/v1` stays supported — there is no `/api/v3`.
- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` now default to true when `DEBUG=false` (each
  overridable via its own environment variable for unusual deployments).
- Enabled Django's standard `AUTH_PASSWORD_VALIDATORS` (the admin is the first password
  surface); `createsuperuser` enforces them even on the non-interactive
  `DJANGO_SUPERUSER_PASSWORD` + `--noinput` bootstrap, which stock Django skips.
- Added `django-unfold` as a runtime dependency so operators can enable the admin in production
  without a development-only extra.

## [2.0.0] - 2026-07-29

v2 is now the primary, advertised API surface. The default `/api/schema/`, `/api/docs/redoc/`, and
the Swagger UI's default "Select a definition" entry now resolve to **v2** (previously v1) — the
breaking trigger for this major bump. **v1 is unchanged and still fully supported** (not deprecated),
now browsable at the new `/api/v1/schema/` and `/api/docs/v1/redoc/` routes. No database migration;
rollback is a redeploy of 1.x.

### Added
- `/api/v2` API surface via a version shim (`NamespaceURLPathVersioning`), adding the
  merchant/sub-tenant namespace axis. v2 callers select a namespace with `X-Namespace-Type` /
  `X-Namespace-ID` headers (absent type = global); v1 stays global-only and rejects those headers
  with a named `400 namespace_not_supported`.
- v2 merchant reads exclude global rows by default with an `include_global=true` fail-closed opt-in;
  merged merchant+global result sets order deterministically (existing ordering + `id` tiebreaker).
- Namespace-scoped `usage_count` on v2 reads (v1/global keep the legacy tenant-wide count).
- `Vary: Authorization, X-Tenant-ID, X-Namespace-Type, X-Namespace-ID` on cacheable reads.
- Per-version OpenAPI contracts: `docs/openapi.yaml` (v1) and `docs/openapi-v2.yaml` (v2), both
  held by the drift gate; namespace headers and `include_global` documented on v2 only.
- Outbox webhook transport with HMAC-signed delivery, configurable timeout, and event
  correlation headers.
- Outbox retry backoff, expired-claim recovery tracking, and dead-letter handling for failed
  deliveries.
- Namespace propagation through audit and outbox: audit rows and outbox events carry the mutated
  row's `namespace_type`/`namespace_id`, so a merchant mutation never emits a namespace-blind
  (global) audit row or event. Global mutations stay `null`/`null`.
- Audit list/read endpoints are namespace-filtered: a merchant-restricted grant reads only its own
  namespace slice and global rows fail closed (an exact merchant grant cannot opt into global even
  with `include_global=true`).
- `docs/events.md`: the outbox consumer contract — event envelope, per-event payload schemas,
  namespace routing guidance, and at-least-once replay/redelivery semantics.
- Namespace rollout control plane: env-backed flags `OCTONOMY_NAMESPACE_SCHEMA_ENABLED`,
  `OCTONOMY_NAMESPACE_READ_ENABLED`, `OCTONOMY_NAMESPACE_AUTH_ENFORCED`, and
  `OCTONOMY_NAMESPACE_V2_API_ENABLED` (all default on), joining the existing
  `OCTONOMY_NAMESPACE_WRITE_ENABLED`. A Django system check enforces the dependency contract at boot
  (`octonomy.E010`–`E016`), so an invalid combination — notably v2 accepting namespaced writes that
  no read path can return (`WRITE` on with `READ` off) — refuses to start. The `WRITE`-requires-swap
  gate (E016) is deploy-tagged so it never blocks `manage.py migrate`.
- `NAMESPACE_V2_API_ENABLED=false` (rollback step 1) refuses namespaced v2 requests with
  `503 namespace_api_disabled` while global v1/v2 traffic continues; the 503 and its shared
  `ErrorResponse` envelope are documented on every namespaced v2 operation in `docs/openapi-v2.yaml`.
- Namespace observability via structured logs: `request_completed` now carries `version`,
  `namespace_type`/`namespace_id`, `error_code`, and `duration_ms` (requests by version + namespace,
  endpoint latency, 4xx/deny reasons). Requests rejected during scope resolution (namespace headers
  on v1, a malformed v2 pair) log the *requested* namespace, so mismatch/format rejects stay on the
  namespace dashboards instead of logging a null scope. A dedicated `namespace_conflict` metric counts duplicate-key
  collisions on the namespace-aware unique constraints (entity + namespace, emitted only from the
  actual uniqueness branches), and the outbox dispatcher emits an `outbox_dispatch_summary` metric
  with run totals and `lag_by_namespace_type`.
- `docs/operations.md`: "Namespace Rollout & Operations" runbook — flag reference, dependency
  contract, rollout/rollback ladders, dashboards/metrics reference, `namespace_mismatch`-spike
  response, backfill verification, post-deploy checklist, curl smoke tests, and staging rehearsal.

### Tests
- Registry-driven namespace isolation sweep (`tests/isolation/`): walks the live v2 URL registry
  and asserts a `merchant_b` caller can never see a `merchant_a` row on any read endpoint, with a
  non-vacuous positive control per endpoint. A newly registered v2 read endpoint without a fixture
  mapping fails CI loudly, keeping isolation coverage at 100% of the read surface.
- Semantic-leak, flag-chaos, conflict/concurrency, and duplicate-slug resolution-order suites:
  bulk partial-failure atomicity and non-disclosure, per-namespace audit/outbox event partitioning,
  read-after-write durability across `NAMESPACE_WRITE_ENABLED` flips, same-slug 409 conflict
  envelopes scoped per namespace with a deactivate→recreate→reactivate matrix, and deterministic
  merchant-before-global resolution (single and bulk paths).

### Changed
- Default OpenAPI schema and docs now advertise v2: the un-versioned `/api/schema/`,
  `/api/docs/redoc/`, and the Swagger UI's default "Select a definition" entry resolve to v2, with
  new `/api/v1/schema/` and `/api/docs/v1/redoc/` routes keeping v1 fully browsable. This flip of the
  default `/api/schema/` artifact from v1 to v2 is the breaking trigger for the major bump; no v1
  data-API behavior changed.
- Outbox event payloads gain additive `namespace_type`/`namespace_id` JSON fields (`null` for
  global). Existing consumers ignore the new keys; every pre-existing field is unchanged, so the
  serialized shape stays backward compatible.
- `NAMESPACE_WRITE_ENABLED` (env `OCTONOMY_NAMESPACE_WRITE_ENABLED`, default off) gates namespaced
  writes: v2 reads are namespace-aware, but a write carrying a namespace scope returns
  `403 namespaced_writes_disabled` until the flag is enabled. Global writes are unaffected. The kill
  switch is now enforced in the domain-service layer as well as HTTP routing, so management commands
  and any programmatic writer are gated too (raw ORM writes — test factories, data migrations — are
  intentionally not gated). The outbox dispatcher is gated as well: while writes are off it claims,
  publishes, and recovers global outbox rows only, so namespaced events stay `pending` (never lost)
  until writes are re-enabled while global delivery continues.
- Outbox dispatch now claims rows before publishing so network delivery happens outside the
  row-locking transaction.

### Fixed
- Bulk tag assignment no longer distinguishes a tag in another namespace from a nonexistent one:
  both are rejected identically as `Unknown tag ids`, closing a cross-namespace existence oracle
  (previously an out-of-scope tag returned `Tag was not found` while a missing id returned
  `Unknown tag ids`, letting a caller probe whether an id named a real tag in another namespace).
- Outbox dispatcher no longer conflates an expired processing claim with a delivery failure. A
  recovered claim is re-queued for redelivery (`pending`) instead of being marked `failed`, so the
  recovery itself never records a successfully-delivered-but-claim-expired event as failed, and
  recoveries count only under `recovered` (previously each recovery was counted as both `failed` and
  `recovered`). Recovery still never increments `attempts`, so a repeatedly-recovered event is
  retried rather than dead-lettered. (A genuine failure of the redelivery still follows the normal
  failed/dead-letter path.)

## [1.0.0] - 2026-06-08

First stable release. The Octonomy REST v1 API contract is now stable and follows Semantic
Versioning; breaking changes to v1 are avoided unless they fix a correctness or security issue.

### Added
- OpenAPI contract drift gate: CI regenerates `docs/openapi.yaml` and fails on any uncommitted
  change, keeping the published v1 contract authoritative.
- Test-coverage threshold enforced in CI via `pytest-cov` (`--cov-fail-under`).
- Dependency vulnerability scan (`pip-audit`) over the locked runtime dependencies, in CI and via
  `make audit`.

### Changed
- Project metadata now reports package version `1.0.0`.
- Generated OpenAPI metadata and the `OCTONOMY_API_VERSION` default now report `1.0.0`.
- Security policy now tracks the `1.0.x` line as supported.
- README, architecture, and release documentation now describe the stable `1.0.0` posture.

## [1.0.0-rc.1] - 2026-06-04

Release candidate for the Octonomy REST v1 API contract.

### Added
- Release readiness documentation, including deployment checks, smoke tests, rollback notes, and
  operational runbooks.
- CI jobs for Django system checks, migration drift checks, OpenAPI schema generation, SQLite
  tests, and PostgreSQL tests across supported Python versions.
- Production readiness Django system checks for default secrets, missing token pepper, wildcard
  hosts, and SQLite usage when `DJANGO_DEBUG=false`.

### Changed
- Project metadata now reports package version `1.0.0rc1`.
- Generated OpenAPI metadata now defaults to API version `1.0.0-rc.1`.
- README project status now describes the v1 release candidate stabilization posture.

## [0.1.0] - 2026-06-04

Initial public release.

### Added
- Tag management service: shared and application-scoped tags with `JSONB` metadata.
- Tag vocabularies for grouping tags.
- Tag aliases and synonym resolution.
- Tag assignments for external resources, including bulk replace endpoints.
- Audit logs and usage counts.
- Transactional event outbox with an outbox dispatch management command.
- Service API key authentication: hashed, peppered tokens scoped by tenant, application, and
  scopes, with create/revoke management commands.
- Multi-tenant and multi-application isolation enforced via `X-Tenant-ID` and `application_id`.
- OpenAPI schema and Swagger/ReDoc docs via drf-spectacular.
- Apache License 2.0.

[Unreleased]: https://github.com/octoverse-id/octonomy/compare/v3.1.1...HEAD
[3.1.1]: https://github.com/octoverse-id/octonomy/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/octoverse-id/octonomy/compare/v3.0.1...v3.1.0
[3.0.1]: https://github.com/octoverse-id/octonomy/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/octoverse-id/octonomy/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/octoverse-id/octonomy/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/octoverse-id/octonomy/compare/v1.0.0-rc.1...v1.0.0
[1.0.0-rc.1]: https://github.com/octoverse-id/octonomy/compare/v0.1.0...v1.0.0-rc.1
[0.1.0]: https://github.com/octoverse-id/octonomy/releases/tag/v0.1.0
