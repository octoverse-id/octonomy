# Release Process

## Current Target

Octonomy's REST API is served on two live surfaces: `/api/v2` (the primary, advertised surface,
which adds the namespace axis) and `/api/v1` (global-only, **still supported — not deprecated**).
Both follow Semantic Versioning. GraphQL, external broker transports, persisted counters, and
external JWT or API gateway auth remain future phases.

## Versioning

Octonomy follows Semantic Versioning. Bug fixes are a patch, backward-compatible additions are a
minor, and breaking changes ship a new parallel URL-versioned surface plus a major bump — as
`/api/v2` did, keeping `/api/v1` supported. See [`versioning.md`](versioning.md) for the full
policy and what counts as breaking.

- Package metadata uses PEP 440 (`X.Y.Z`, and `X.Y.ZrcN` for a prerelease); OpenAPI and
  user-facing docs use SemVer (`X.Y.Z`, and `X.Y.Z-rc.N`). The two differ only for prereleases,
  which is the conversion `make version-check` performs before comparing them.
- Set `OCTONOMY_API_VERSION` when a deployment should expose a different schema version string.

## Release Checklist

Before cutting a release, run the pre-release gate:

```bash
make install
make lint
make check
make migration-check
make test
make openapi-check
```

For PostgreSQL-backed verification:

```bash
make db-up
DATABASE_URL=postgres://octonomy:octonomy@localhost:5432/octonomy make test
```

For SQLite compatibility coverage:

```bash
make test-sqlite
```

Before tagging a release, regenerate the checked OpenAPI artifact if the project chooses to publish
one from the repository:

```bash
make openapi
```

## Cutting a Release

Routine releases are cut manually. Pick the bump (`PATCH` / `MINOR` / `MAJOR`) per
[`versioning.md`](versioning.md), then:

1. Branch `release/<version>` (e.g. `release/1.1.0`).
2. Bump the version everywhere it is stamped:
   - `pyproject.toml` `version`
   - `config/settings.py` `API_VERSION` default
   - `.env.example` (`OCTONOMY_API_VERSION`)
   - regenerate both schemas with `make openapi` (updates `info.version` in `docs/openapi.yaml`
     **and** `docs/openapi-v2.yaml` — `make version-check` only inspects the v1 file, but the
     OpenAPI drift gate in CI regenerates and diffs both)
   - refresh the lock with `uv lock` (the project's own `version` in `uv.lock`)
   - the example image tags, which have no default to fall back on:
     `deploy/kubernetes/{deployment,migrate-job,dispatcher-cronjob}.yaml` (one `image:` each),
     `deploy/docker/compose.yaml` (**three** `image:` lines — migrate, app, dispatcher),
     `docs/deployment.md`, and `README.md` (the quickstart's `docker pull`).

     `make version-check` enforces **exactly those files** via `scripts/check-image-refs.sh`,
     including that a file has not silently *lost* its reference, so a missed tag is a red gate
     rather than a stale example someone finds in production. It matches
     `ghcr.io/octoverse-id/octonomy` only, so adding a new file that names the published image
     means adding it to the `version-check` list too — nothing detects a file the gate was never
     pointed at. The `your-registry.example.com` build-it-yourself example is deliberately
     *not* on the list: it reads the version out of `pyproject.toml` instead of hardcoding one,
     so there is nothing there to stamp.

     **Prereleases are the exception, and it is a deliberate one.** This step applies to final
     `X.Y.Z` releases. `publish-image.yml` only publishes exact `vX.Y.Z` tags, so there is no
     `3.2.0rc1` image for an example to point at — `version-check` therefore skips the image
     gate entirely for a prerelease version and says so in its output. On a prerelease branch,
     **leave the example tags on the last published final release**; do not bump them to the
     prerelease version, which would document a pull that cannot work. They get stamped when the
     final release is cut.
   - `SECURITY.md` — only when the supported line changes (a patch inside the same `x.y` line
     does not move it)

   Deliberately **not** stamped: `deploy/.env.production.example` and
   `deploy/kubernetes/configmap.yaml` leave `OCTONOMY_API_VERSION` unset so the application default
   tracks the release on its own. Nothing validates `deploy/`, so a value pinned there would go
   stale silently.
3. Update `CHANGELOG.md`: move the `[Unreleased]` entries under `## [<version>] - <date>`, add the
   `[<version>]` compare link, and reset `[Unreleased]` to `compare/v<version>...HEAD`.
4. Run the gates, then open the PR with `Closes #<issue>`:

   ```bash
   make release-check   # lint, checks, migrations, tests, openapi drift, audit, version-check
   ```

5. After merge and green CI, tag the merge commit. Pushing the tag is what triggers
   `publish-image.yml`, so this is the step that builds and publishes the container image:

   ```bash
   git tag -a v<version> -m "Octonomy <version>" <merge-commit>
   git push origin v<version>
   ```

   **A prerelease publishes no image, so it skips step 6 entirely — go straight to step 7.**
   `publish-image.yml`'s trigger is the exact glob `v[0-9]+.[0-9]+.[0-9]+`, so a prerelease tag
   (`v1.0.0-rc.1`) does not start it. That is deliberate and matches step 2's stamping exception:
   `:X.Y.Z` is a promise of immutable bytes, and a prerelease is not something anyone should be
   pinning a deployment to. Say so in the release notes rather than leaving readers to discover
   that `docker pull` has nothing to fetch.

6. **Verify the image before announcing the release. This gate is mandatory for a final
   `X.Y.Z` release** (prereleases skip it — see step 5).

   A GitHub release is the announcement; the image is the artifact people actually run. Publishing
   the announcement first means telling people to `docker pull` a tag that may not exist — and
   `docs/deployment.md`, the `deploy/` examples, and the README quickstart all name `:<version>`
   the moment the release is public. **A release is not published until its image is.**

   The publish run can also fail for reasons that have nothing to do with your code: a queued run
   cancelled by a concurrent release (see ["When A Publish Run Does Not
   Appear"](#when-a-publish-run-does-not-appear)), or a first-ever publish against a private
   package (see ["First Publish To GHCR"](#first-publish-to-ghcr)).

   **Save it to a file and run it** (`bash verify-release.sh`) rather than pasting it into your
   shell. Every check below must fail loudly rather than print something you are expected to
   read, and `set -euo pipefail` plus the explicit string comparisons are what make that true —
   neither behaves the same way interactively, where `exit 1` would also close your terminal.
   The comparisons are not decoration: `check-tag-unpublished.sh` exits **0** and prints
   `absent` for a tag that does not exist, so its exit status alone proves nothing.

   ```bash
   set -euo pipefail
   VERSION=<version>                                # e.g. 3.1.0, no leading v
   REPO=octoverse-id/octonomy
   IMAGE=ghcr.io/$REPO

   # a. A publish run for THIS tagged commit finished green.
   #    Matched on head_sha, not the tag name. `event != "workflow_run"` excludes the :edge
   #    deliveries for the same commit, while still accepting a workflow_dispatch — which is
   #    how the recovery path below re-publishes a cancelled release.
   sha=$(git rev-list -n1 "v$VERSION")
   green=$(gh api "repos/$REPO/actions/workflows/publish-image.yml/runs?per_page=100" \
     --jq "[.workflow_runs[]
            | select(.head_sha == \"$sha\" and .event != \"workflow_run\"
                     and .conclusion == \"success\")] | length")
   [ "${green:-0}" -ge 1 ] || { echo "no successful publish run for v$VERSION"; exit 1; }

   # b. :X.Y.Z resolves. Capture the digest every other tag has to agree with.
   #    check-tag-unpublished.sh exits 0 and prints `absent` for a tag that does not exist,
   #    so the OUTPUT is what proves publication — never the exit status alone.
   result=$(./scripts/check-tag-unpublished.sh "$IMAGE" "$VERSION")
   case "$result" in
     present\ *) digest=${result#present } ;;
     *) echo "no image published for $VERSION (got: $result)"; exit 1 ;;
   esac
   echo "$IMAGE:$VERSION -> $digest"

   # c. Every tag this version should own points at that SAME digest — including :X.Y and
   #    :latest when this is the newest release. resolve-latest-tag.sh is the same script
   #    the workflow promotes with, so this asserts exactly what it should have written.
   #    Assigned before the loop on purpose: `for tag in $(cmd)` does NOT trip `set -e` when
   #    cmd fails, so a failed resolve would silently iterate over nothing and pass.
   tags=$(./scripts/resolve-latest-tag.sh "$VERSION")
   [ -n "$tags" ] || { echo "resolve-latest-tag.sh produced no tags for $VERSION"; exit 1; }
   for tag in $tags; do
     got=$(./scripts/check-tag-unpublished.sh "$IMAGE" "$tag" "$digest")
     [ "$got" = "match $digest" ] || { echo "$IMAGE:$tag -> $got"; exit 1; }
     echo "$IMAGE:$tag -> match"
   done

   # d. It is pullable by someone who is NOT logged in — the whole point of publishing it.
   #    A fresh DOCKER_CONFIG drops your credentials without logging you out. Assigned on its
   #    own line because in `VAR=$(cmd) docker ...` the status is docker's, so a failed mktemp
   #    would leave DOCKER_CONFIG empty — and Docker then falls back to your REAL config,
   #    quietly turning this into an authenticated pull that proves nothing.
   anon_config=$(mktemp -d)
   DOCKER_CONFIG="$anon_config" docker pull "$IMAGE@$digest"

   # e. Both attestations verify, and the RELEASE WORKFLOW is what signed them.
   #    Verify the DIGEST, not the tag: it is the thing (b) and (c) just pinned.
   #    --predicate-type: a bare verify passes with the SBOM missing entirely.
   #    --signer-workflow: --repo alone accepts a predicate signed by ANY workflow here.
   #    --source-ref/--source-digest: bind it to this tag and commit (gh 2.68+).
   for predicate in https://slsa.dev/provenance/v1 https://spdx.dev/Document; do
     gh attestation verify "oci://$IMAGE@$digest" \
       --repo "$REPO" \
       --signer-workflow "$REPO/.github/workflows/publish-image.yml" \
       --source-ref "refs/tags/v$VERSION" \
       --source-digest "$sha" \
       --predicate-type "$predicate"
   done
   echo "v$VERSION is published, promoted, pullable and attested"
   ```

   Step (e) needs **`gh` 2.68 or newer** as written (`gh attestation` arrived in 2.49,
   `--signer-workflow` in 2.51, `--source-ref`/`--source-digest` in 2.68); on 2.51–2.67 drop the
   two `--source-*` lines. The `gh 2.4.0` packaged by Debian/Ubuntu has no `attestation` command
   at all (`unknown command "attestation"`) — install from [cli.github.com](https://cli.github.com).
   Steps (a)–(d) work on any `gh` that has `gh api`.

   An old `gh` is not a reason to skip the gate: `publish-image.yml` runs the same verification,
   with the same signer and source pins, for both predicate types, **before** it promotes any
   tag — so a green run in step (a) already proves the attestations verified. On an older `gh`,
   confirm it in the run log instead:

   ```bash
   gh run view <run-id> --log | grep -A3 "Verify provenance and SBOM"
   ```

   If any of these fail, **stop and fix the publish before continuing** — do not create the
   release. The version tag in git is already pushed, which is fine: re-publishing the same
   version re-promotes the digest it already published and refuses if the bytes differ, so the
   recovery path is safe to re-run.

7. Only once step 6 is fully green, publish the release:

   ```bash
   gh release create v<version> --title "v<version>" --notes-file <notes>
   ```

8. Close the tracking issue and delete the merged branch.

> `gh` caveats (observed on `gh 2.4.0`): `release create` has no `--latest` / `--verify-tag` — a
> published, non-prerelease release is "Latest" by default. `gh issue close` has no `--comment`;
> post the comment separately with `gh issue comment` before closing.

## First Publish To GHCR

**The very first run of `publish-image.yml` is expected to fail, and it is not a defect.** GHCR
creates a brand-new package as **private**, even when the repository is public. The workflow's last
gate before promoting tags is an anonymous pull — the check that the published image is actually
usable by someone who is not logged in — so that gate fails on the first publish, by design.

This happens minutes after the publish workflow first lands on `main`: merging it makes the next
green CI run publish `:edge`.

Recovery is one manual step, once, for the lifetime of the package:

1. Open **Packages** on the organisation or repository page and select `octonomy`.
2. **Package settings → Danger Zone → Change visibility → Public**.
3. Re-run the failed workflow run.

While the package is private, the failure looks like a `docker pull` returning `denied` or
`unauthorized`. Nothing else in the workflow needs changing, and nothing was published under a
release tag — the image is pushed by digest and only promoted to `:X.Y.Z` / `:X.Y` / `:latest`
**after** every check passes, so a failure at this gate leaves an untagged, prunable digest rather
than a broken release.

Also confirm, once, that **Inherit access from repository** stays enabled in the same settings page,
so `GITHUB_TOKEN` keeps its push permission for subsequent releases.

## When A Publish Run Does Not Appear

Release publishes share one concurrency group so they cannot promote `:X.Y` / `:latest` over each
other. GitHub keeps at most one run *pending* per group, so if several releases are cut inside one
build window, a queued one can be cancelled before it starts — its version is then tagged in git
with no image published.

Check the Actions tab after tagging. To publish a version whose run was cancelled, dispatch
`Publish image` **from that version's tag** with `dry_run` unchecked. That path is idempotent: if
the digest is already published it re-promotes it rather than rebuilding, and it refuses outright
if the version resolves to different bytes.

## Dependency Audit

CI scans the locked runtime dependencies for known vulnerabilities (the `security` job; run
locally with `make audit`). The gate fails closed: a newly disclosed runtime CVE — or a transient
advisory-service or network outage — blocks merges until resolved. To accept a known, triaged
advisory, suppress it explicitly by its ID in the audit command (`Makefile` `audit` target and the
CI step):

```bash
pip-audit --no-deps -r /dev/stdin --ignore-vuln GHSA-xxxx-xxxx-xxxx
```

## Release Compatibility Criteria

Every release should satisfy these checks:

- All public REST endpoints are documented by generated OpenAPI schema.
- Existing request and response shapes remain backwards compatible unless the change fixes a
  release-blocking correctness or security issue.
- Tenant isolation, application grants, idempotency, and soft-delete behavior are covered by tests.
- New migrations are committed and `makemigrations --check --dry-run` is clean.
- Health endpoints remain unauthenticated.
- Service-token auth remains required for tenant-owned API paths.
- Outbox dispatch has a documented retry path.

## Production Configuration Checklist

Set these environment variables explicitly outside local development:

- `DJANGO_DEBUG=false`
- `DJANGO_SECRET_KEY=<non-default-secret>`
- `DATABASE_URL=postgres://...`
- `ALLOWED_HOSTS=<comma-separated-hostnames>`
- `SERVICE_TOKEN_PEPPER=<non-default-secret-pepper>`
- `OCTONOMY_API_VERSION` — **leave unset.** It defaults to the version the build was cut from, so it
  tracks upgrades on its own. Set it only to deliberately advertise a different schema version.
- `LOG_LEVEL=INFO`
- `MAX_BULK_TAGS=200` or a deployment-specific cap

Run Django deploy checks as part of deployment validation:

```bash
python manage.py check --deploy
```

## Migration And Rollback Notes

- Apply migrations before serving the new application version:

```bash
python manage.py migrate
```

- Back up PostgreSQL before release migrations in shared environments.
- Rollbacks should restore both application code and database state when migrations are not safely
  reversible.
- Service tokens are shown only once at creation time. Rotate by creating a replacement token,
  updating callers, and revoking the old prefix.

## Smoke Test

After deployment, run these minimum checks:

```bash
curl -f https://<host>/health/live
curl -f https://<host>/health/ready
curl -f https://<host>/api/schema/        # v2 (default/advertised)
curl -f https://<host>/api/v1/schema/     # v1 (still supported)
```

Then use a real service token to verify tenant-scoped reads and a non-production tenant mutation.
