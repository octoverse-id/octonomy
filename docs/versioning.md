# Versioning Policy

Octonomy follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). This document is the
source of truth for **how a change maps to a version bump** and how the REST contract evolves.
`/api/v2` is the primary, advertised surface; `/api/v1` remains **fully supported — not
deprecated**.

## Version surfaces

There are three version strings, and they are kept in sync by design:

| Surface | Where | Meaning |
| ------- | ----- | ------- |
| **Package version** | `pyproject.toml` `version` (and `uv.lock`) | Canonical SemVer for the release and CHANGELOG. |
| **API schema version** | OpenAPI `info.version` | Mirrors the package version. Generated from `API_VERSION` (`config/settings.py`), which defaults to the package version and is overridable via `OCTONOMY_API_VERSION`. |
| **URL contract version** | the `/api/<version>/` path prefix (`config/urls.py`) | The **contract major**. Decoupled from the package version — it only moves when a backward-incompatible contract is introduced. |

The package version is the one you bump. The schema version follows it automatically; the URL
contract version is independent of it.

Both `/api/v1` and `/api/v2` are live, served by one view tree via a version shim
(`NamespaceURLPathVersioning`, `ALLOWED_VERSIONS=["v1","v2"]`). `/api/v2` adds the namespace
surface (`X-Namespace-*` headers, `include_global`); `/api/v1` stays global-only and unchanged.
v2 is the **advertised default**: the un-versioned `/api/schema/`, `/api/docs/redoc/`, and the
Swagger dropdown's default definition resolve to v2, while v1 stays browsable at `/api/v1/schema/`
and `/api/docs/v1/redoc/`. The contract is generated per version — `docs/openapi.yaml` (v1) and
`docs/openapi-v2.yaml` (v2) — and both are held by the drift gate. Both schemas mirror the package
version in `info.version`; the `v1`/`v2` distinction lives in the path prefix, not the version
string.

## Bump rules

Decide the bump from the **most significant** change in the release:

### PATCH — `1.0.x`
Backward-compatible **bug fixes**. No change to the API contract.
- Examples: fix a race in assignment writes, correct an error payload, tighten an internal query.
- The OpenAPI schemas are unchanged (or only descriptive text changes).

### MINOR — `1.x.0`
Backward-compatible **additions**. Existing clients keep working unchanged.
- Examples: a new endpoint, a new **optional** request field or query parameter, a new response
  field, a new tag type, a new optional capability.
- The OpenAPI schemas grow **additively** — the OpenAPI drift gate will show an additive diff.
- Does not introduce a new URL-versioned surface; the addition applies to the live surfaces
  (`/api/v1` and/or `/api/v2`).

### MAJOR — `x.0.0`
Backward-**incompatible** changes to a live contract. **Not done in place.**
- Policy: introduce a parallel URL-versioned surface (a new `/api/<next>` include in
  `config/urls.py`), make it the advertised default, and **keep the prior surface serving and
  supported** — a major bump does not deprecate it.
- `2.0.0` is exactly this: `/api/v2` shipped as the primary surface and `/api/v1` stays fully
  supported. Integrators adopt `/api/v2` when they want the namespace axis; there is no forced
  migration off `/api/v1`.

### What counts as breaking
Any of these requires a major bump (and therefore a new parallel `/api/<next>` surface, not an
in-place change to a live contract):
- Removing or renaming a field, endpoint, or query parameter.
- Changing a field's type, or making an optional field required.
- Tightening validation so previously-accepted requests now fail.
- Changing default behavior, error codes, or response semantics that callers rely on.

## The drift gate is the compatibility tripwire

CI regenerates `docs/openapi.yaml` (v1) and `docs/openapi-v2.yaml` (v2) and fails on any uncommitted
change (`make openapi-check`, the `checks` job). Treat every schema diff in a PR as a decision:

- **Additive diff** (new optional paths/fields) ⇒ this is at least a **minor**.
- **Breaking diff** on any live contract (`/api/v1` or `/api/v2`) ⇒ **not allowed**. Ship the change
  on a new parallel `/api/<next>` surface instead.
- **No diff** ⇒ a patch (or a non-API change).

## Deprecation

**`/api/v1` is not deprecated.** It remains a fully supported surface; the process below applies
only *if* a specific field or endpoint is ever put on a removal path — it does not describe the
current state of v1.

When something on a live surface is on a path to removal in a future major:
- Mark it `deprecated: true` in the OpenAPI schema.
- Add a `### Deprecated` entry to the CHANGELOG with the intended sunset.
- Keep it working for the documented window; only remove it in the next major that ships a
  replacement surface.

A deployment can pin the advertised schema string with `OCTONOMY_API_VERSION` if it needs to
expose a specific version independent of the package default.

## Where this shows up

- **Per PR:** keep `CHANGELOG.md` `[Unreleased]` current; classify any `docs/openapi.yaml` diff
  with the rules above. Version numbers are **not** bumped in feature/fix PRs.
- **At release time:** the version bump and tag happen in a dedicated release PR — see the
  "Cutting a Release" runbook in [`release.md`](release.md).
