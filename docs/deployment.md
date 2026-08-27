# Deployment

This guide walks through deploying Octonomy to **your own infrastructure**. Pick the path that
fits how you run software:

- [Docker Compose](#option-a--docker-compose-single-host) — one host, simplest.
- [Kubernetes](#option-b--kubernetes) — a cluster, with manifests you can adapt.
- [VPS / bare server (systemd)](#option-c--vps--bare-server-systemd) — a plain Linux box.

Ready-to-edit example files for each path live under [`deploy/`](../deploy). This guide is the
narrative; [operations.md](operations.md) is the runbook for what happens *after* you are live
(logging, the namespace rollout, the outbox, backups).

---

## The mental model: three processes, one build

However you deploy, Octonomy is one Django application backed by PostgreSQL, and it needs **three
things running** off the same build — a container image for Docker Compose and Kubernetes, or a
source checkout and virtualenv on the VPS path:

1. **The API server** — Gunicorn serving `/api/*` and the health probes on port `8000`. Scale it
   horizontally; it is stateless (all state is in PostgreSQL).
2. **Database migrations** — a **one-shot** `python manage.py migrate` you run on first install and
   before each upgrade. It is deliberately **not** run at container start, so restarts and extra
   replicas never touch the schema.
3. **The outbox dispatcher** — a scheduled/looping `python manage.py dispatch_outbox_events`.
   **If you skip this, events are written to the outbox but never delivered.** It runs from the same
   build and environment as the API.

A fourth concern is optional: the **admin console** ships off in production. If you turn it on, you
also serve its static assets (see [operations.md, "Admin console"](operations.md#admin-console)).

---

## Prerequisites

- **PostgreSQL** — a reachable database (managed or self-run). CI and the example Compose file use
  PostgreSQL 16; any currently-supported major works. SQLite is rejected in production.
- **The container image** (Docker/Kubernetes) **or Python 3.12+** (VPS). Official images are
  published to GHCR at `ghcr.io/octoverse-id/octonomy` and the package is public, so no registry
  of your own and no pull credentials are needed (see below). Building it yourself from the repo's
  `Dockerfile` stays supported.
- A TLS-terminating proxy/ingress in front of the app for anything internet-facing.

---

## Configuration and secrets

Octonomy is configured entirely through environment variables, read at startup. The template
[`deploy/.env.production.example`](../deploy/.env.production.example) documents every one; each option
below shows where the filled-in copy goes (a Compose `.env`, a Kubernetes `Secret`, or a systemd
`EnvironmentFile`). It ships the two secrets **empty** on purpose — see the note below.

**Required in production** (with `DJANGO_DEBUG=false` the app refuses to boot if either secret is
empty or a known default — the template ships them empty on purpose, so a half-edited file fails fast
instead of running on a weak or publicly-known secret):

| Variable | What it is |
| --- | --- |
| `DJANGO_DEBUG` | Must be `false`. The container image already defaults it to `false`. |
| `DJANGO_SECRET_KEY` | Unique 64-char secret. `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `SERVICE_TOKEN_PEPPER` | Keying secret for service-token hashes. Generate the same way. Changing it invalidates every existing token. |
| `DATABASE_URL` | `postgres://user:password@host:5432/octonomy` |
| `ALLOWED_HOSTS` | Comma-separated real hostnames; keep `127.0.0.1` too so container-local health probes are accepted. Never `*`. |

Common tuning: `WEB_CONCURRENCY` (Gunicorn workers, ~`2 * cores + 1`), `LOG_LEVEL`, `MAX_BULK_TAGS`.
The namespace rollout flags and outbox settings have safe defaults — the full reference is in the
[env template](../deploy/.env.production.example) and [operations.md](operations.md). Logs are
structured JSON on stdout/stderr; point your log collector at the container/process output.

---

## The container image

Octonomy publishes an official image to GHCR. The package is **public**: no account, no
`docker login`, and no `imagePullSecrets`.

```bash
docker pull ghcr.io/octoverse-id/octonomy:3.1.1
```

Releases are built for **`linux/amd64` and `linux/arm64`**, and each architecture is started and
smoke-tested before the version tag is promoted onto it.

The image runs Gunicorn as a **non-root** user, validates configuration on start (`manage.py check`,
which fail-closes on an invalid namespace flag combination), and logs JSON. It does **not** run
migrations — that is your one-shot step.

### Which tag to use

| Tag | Moves? | Use it for |
| --- | --- | --- |
| `:X.Y.Z` | **Never.** A given `X.Y.Z` always resolves to the same bytes. | Production. This is what the examples in `deploy/` pin. |
| `:X.Y` | Moves to the newest `X.Y.Z` in that line. | Picking up patch fixes automatically. |
| `:latest` | Moves to the newest release. | Trying it out. Not recommended for production — an upgrade arrives whenever you restart. |
| `:edge` | Moves on every green build of `main`. | **Unsupported**, and unattested. Testing unreleased work only. |

`:X.Y` and `:latest` only ever move *forward*: they are recomputed against every existing release
tag at publish time, so a backport released after a newer version cannot drag them backward onto
older code.

```bash
docker pull ghcr.io/octoverse-id/octonomy:latest   # newest release
docker pull ghcr.io/octoverse-id/octonomy:edge     # tip of main — unsupported, unattested
```

Republishing different bytes under a shipped `X.Y.Z` is refused by the publish workflow — there is
no overwrite switch. A fix ships as a new patch version.

### Verifying what you pulled

Every release carries SLSA build provenance and a per-architecture SPDX SBOM, signed via Sigstore
and attached to the image. Verify both, and pin **who** signed them:

```bash
gh attestation verify oci://ghcr.io/octoverse-id/octonomy:3.1.1 \
  --repo octoverse-id/octonomy \
  --signer-workflow octoverse-id/octonomy/.github/workflows/publish-image.yml \
  --predicate-type https://slsa.dev/provenance/v1

gh attestation verify oci://ghcr.io/octoverse-id/octonomy:3.1.1 \
  --repo octoverse-id/octonomy \
  --signer-workflow octoverse-id/octonomy/.github/workflows/publish-image.yml \
  --predicate-type https://spdx.dev/Document
```

Both extra flags carry weight, and dropping either weakens the check in a way that still exits 0:

- **`--predicate-type`** — a bare `gh attestation verify` proves *an* attestation exists, so it
  passes with the SBOM missing entirely. Naming each predicate is what checks both are present.
- **`--signer-workflow`** — `--repo` alone only validates the certificate's *source repository*, so
  it accepts a matching predicate signed by **any** workflow here. Pinning the workflow path is
  what establishes that the release workflow built it. GitHub's own documentation calls `--repo`
  the minimum and recommends this flag on top of it.

Together these say the image was built by this repository's publish workflow and carries both
predicates. To additionally bind the image to the **release tag** it claims to come from, add
`--source-ref refs/tags/v3.1.1` (needs `gh` 2.68+). `:edge` is **not** attested and fails all of
this by design.

Needs **`gh` 2.51+** as written — `gh attestation` arrived in 2.49 and `--signer-workflow` in 2.51.
The `gh 2.4.0` in Debian/Ubuntu's repositories has no `attestation` command at all
(`unknown command "attestation"`); install from [cli.github.com](https://cli.github.com) instead.
The same verification, with the same signer pin, also runs inside the publish workflow before any
release tag is promoted, so an image carrying a `:X.Y.Z` tag has already passed it.

### Building it yourself

Still fully supported — for an air-gapped registry, a patched base image, or a local change:

```bash
# Docker Compose / single host — a local tag is enough:
docker build -t octonomy:local .

# Kubernetes — the cluster must be able to pull it, so tag for your registry and push.
# Read the version out of the checkout rather than typing it, so the tag always names
# the release you are actually building:
version=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
docker build -t "your-registry.example.com/octonomy:$version" .
docker push "your-registry.example.com/octonomy:$version"
```

Then replace the `image:` values in the example configs — three lines in
`deploy/docker/compose.yaml`, and one each in `deploy/kubernetes/deployment.yaml`,
`migrate-job.yaml`, and `dispatcher-cronjob.yaml`.

---

## Option A — Docker Compose (single host)

Best for a single VM or a small self-hosted box. The example
[`deploy/docker/compose.yaml`](../deploy/docker/compose.yaml) runs PostgreSQL, a one-shot migration,
the API, and the dispatcher.

There is nothing to build — the compose file pulls the published image.

```bash
# 1. Configure. Copy the template next to the compose file and lock it down (it holds
#    secrets). Set DJANGO_SECRET_KEY, SERVICE_TOKEN_PEPPER, ALLOWED_HOSTS, POSTGRES_PASSWORD,
#    and DATABASE_URL with host "db": postgres://octonomy:<POSTGRES_PASSWORD>@db:5432/octonomy
#    POSTGRES_PASSWORD ships commented out (it is Compose-only) — uncomment it.
cd deploy/docker
cp ../.env.production.example .env
chmod 600 .env
$EDITOR .env

# 2. Start it from deploy/docker/ so compose auto-loads .env (for ${...} interpolation AND
#    as the containers' env). migrate runs first, then app + dispatcher come up.
#    --wait returns only once migrate has exited 0 and the API reports healthy, so a
#    failure surfaces here instead of in the next command.
docker compose up -d --wait

# 3. Confirm the state it settled into
docker compose ps
docker compose logs -f app
```

The API listens on `127.0.0.1:8000` (loopback). Put a TLS proxy (nginx, Caddy, Traefik) on the host
in front of it for public traffic, or change the port mapping to `8000:8000` to reach it directly on
a trusted network.

**Upgrades:** bump the version on **all three** `image:` lines in `compose.yaml` (migrate, app,
dispatcher — leave the dispatcher behind and it runs old code against the migrated schema), then
from `deploy/docker/`:

```bash
docker compose pull
docker compose up -d --wait
```

The migrate service re-runs (a no-op when already applied) and the app/dispatcher restart. If you
build your own image, rebuild from the repo root instead of pulling.

---

## Option B — Kubernetes

Manifests are in [`deploy/kubernetes/`](../deploy/kubernetes). They create an `octonomy` namespace, a
`ConfigMap` (non-secret config), a `Secret` (secrets), a migration `Job`, the API `Deployment`
(probes wired to `/health/live` and `/health/ready`, non-root, read-only root filesystem), a
`Service`, an `Ingress` (TLS), and the dispatcher `CronJob`.

The manifests reference the published image, so they apply **as-is** — no registry of your own, no
`imagePullSecrets`, and no edit to any `image:` field.

**Before applying:** edit `configmap.yaml` (`ALLOWED_HOSTS`) and set the host in `ingress.yaml`.
That is the whole list. (If you build your own image, also repoint the `image:` field in
`deployment.yaml`, `migrate-job.yaml`, and `dispatcher-cronjob.yaml`.)

```bash
# 1. Namespace + non-secret config
kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl apply -f deploy/kubernetes/configmap.yaml

# 2. Secrets — create imperatively (preferred over committing a manifest), or adapt
#    deploy/kubernetes/secret.example.yaml with a sealed-secrets / external-secrets tool.
kubectl -n octonomy create secret generic octonomy-secrets \
  --from-literal=DJANGO_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(64))')" \
  --from-literal=SERVICE_TOKEN_PEPPER="$(python -c 'import secrets;print(secrets.token_urlsafe(64))')" \
  --from-literal=DATABASE_URL="postgres://octonomy:PASSWORD@db-host:5432/octonomy"

# 3. Migrate (run to completion before the app rolls out)
kubectl -n octonomy delete job octonomy-migrate --ignore-not-found
kubectl -n octonomy apply -f deploy/kubernetes/migrate-job.yaml
kubectl -n octonomy wait --for=condition=complete job/octonomy-migrate --timeout=300s

# 4. App + Service + Ingress + dispatcher
kubectl -n octonomy apply -f deploy/kubernetes/deployment.yaml
kubectl -n octonomy apply -f deploy/kubernetes/service.yaml
kubectl -n octonomy apply -f deploy/kubernetes/ingress.yaml
kubectl -n octonomy apply -f deploy/kubernetes/dispatcher-cronjob.yaml

# 5. Verify the rollout
kubectl -n octonomy rollout status deploy/octonomy
```

This assumes an ingress controller (the example targets ingress-nginx) and, for automatic TLS,
cert-manager with a `ClusterIssuer`. Point `DATABASE_URL` at your PostgreSQL (a managed service, or
an in-cluster operator — this repo does not ship a database chart).

**Upgrades:** bump the image tag in **all three** workloads that use it — `deployment.yaml`,
`migrate-job.yaml`, and `dispatcher-cronjob.yaml` (miss the CronJob and the dispatcher keeps running
old code against the migrated schema). Re-run the migrate Job (step 3), then re-apply the workloads:

```bash
kubectl -n octonomy apply -f deploy/kubernetes/deployment.yaml \
  -f deploy/kubernetes/dispatcher-cronjob.yaml -f deploy/kubernetes/service.yaml \
  -f deploy/kubernetes/ingress.yaml
# (also configmap.yaml if it changed; update the Secret separately — never re-apply
#  secret.example.yaml, its empty values would blank your real secrets)
```

A rolling update keeps the old pods serving until the new ones pass their readiness probe.

---

## Option C — VPS / bare server (systemd)

For a plain Linux host with no container runtime. Example units are in
[`deploy/systemd/`](../deploy/systemd). Beyond Python 3.12+, you'll need: PostgreSQL
(installed and reachable), [`uv`](https://docs.astral.sh/uv/) to build the venv, and
`nginx` + `certbot` for TLS (Debian/Ubuntu: `sudo apt install -y nginx certbot`).

```bash
# 1. System user + source
sudo useradd --system --home-dir /opt/octonomy --shell /usr/sbin/nologin octonomy
sudo git clone https://github.com/octoverse-id/octonomy /opt/octonomy
sudo chown -R octonomy:octonomy /opt/octonomy

# 2. Runtime dependencies into a venv (installs Gunicorn too). uv: https://docs.astral.sh/uv/
cd /opt/octonomy
sudo -u octonomy uv sync --frozen --no-install-project --no-dev
# creates /opt/octonomy/.venv

# 3. Environment file (readable only by the service user)
sudo mkdir -p /etc/octonomy
sudo cp deploy/.env.production.example /etc/octonomy/octonomy.env
sudo $EDITOR /etc/octonomy/octonomy.env          # secrets, DATABASE_URL, ALLOWED_HOSTS
sudo chown octonomy:octonomy /etc/octonomy/octonomy.env
sudo chmod 600 /etc/octonomy/octonomy.env

# 4. Install the units
sudo cp deploy/systemd/octonomy.service deploy/systemd/octonomy-migrate.service \
        deploy/systemd/octonomy-dispatcher.service deploy/systemd/octonomy-dispatcher.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload

# 5. Migrate, then start the API and the dispatcher timer
sudo systemctl start octonomy-migrate
systemctl status octonomy-migrate            # confirm it exited 0
sudo systemctl enable --now octonomy
sudo systemctl enable --now octonomy-dispatcher.timer

# 6. TLS reverse proxy (the unit binds Gunicorn to 127.0.0.1:8000). Get the certificate
#    FIRST — the nginx config references cert files that must already exist, or `nginx -t`
#    fails. certbot --standalone needs port 80 free, so stop nginx while it runs.
sudo systemctl stop nginx 2>/dev/null || true
sudo certbot certonly --standalone -d api.example.com
sudo cp deploy/systemd/nginx-octonomy.conf /etc/nginx/sites-available/octonomy
sudo ln -s /etc/nginx/sites-available/octonomy /etc/nginx/sites-enabled/octonomy
sudo rm -f /etc/nginx/sites-enabled/default    # drop the distro default vhost if present
sudo nginx -t && sudo systemctl restart nginx

# 7. Renewals: issuing via --standalone needs port 80 free, but nginx now holds it. Add
#    hooks that stop/start nginx around each automated renewal, then verify with a dry run.
sudo mkdir -p /etc/letsencrypt/renewal-hooks/pre /etc/letsencrypt/renewal-hooks/post
printf '#!/bin/sh\nsystemctl stop nginx\n'  | sudo tee /etc/letsencrypt/renewal-hooks/pre/nginx.sh  >/dev/null
printf '#!/bin/sh\nsystemctl start nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/post/nginx.sh >/dev/null
sudo chmod +x /etc/letsencrypt/renewal-hooks/pre/nginx.sh /etc/letsencrypt/renewal-hooks/post/nginx.sh
sudo certbot renew --dry-run
```

**Upgrades:** `git pull` in `/opt/octonomy` (as the `octonomy` user), re-run
`uv sync --frozen --no-install-project --no-dev`, `sudo systemctl start octonomy-migrate`, then
`sudo systemctl restart octonomy`. `ExecReload` (`systemctl reload octonomy`) triggers a zero-downtime
Gunicorn worker reload when no migration is involved.

---

## Post-deploy verification

Run these against any topology once it is up.

**1. Config + flag contract is valid** (also checks the constraint swap when namespaced writes are
enabled — see operations.md). Run it where the app runs:

- **Compose** (from `deploy/docker/`): `docker compose exec app python manage.py check --deploy`
- **Kubernetes**: `kubectl -n octonomy exec deploy/octonomy -- python manage.py check --deploy`
- **VPS**: `sudo -u octonomy bash -c 'set -a; . /etc/octonomy/octonomy.env; set +a; cd /opt/octonomy && .venv/bin/python manage.py check --deploy'`

**2. Readiness and a smoke test:**

```bash
# 200 = app up and DB reachable; 503 = DB unreachable
curl -fsS https://api.example.com/health/ready

# Smoke test with a service token (see below), scoped to a tenant:
curl -fsS "https://api.example.com/api/v2/tags?application_id=commerce" \
  -H "Authorization: Bearer <service-token>" -H "X-Tenant-ID: tenant_demo"
```

See [operations.md, "Post-deploy verification checklist"](operations.md#post-deploy-verification-checklist)
for the full list, including the log signals to confirm and the namespace smoke tests.

---

## Bootstrapping access

Clients authenticate with **service tokens**. Create one (the raw token prints once — store it
immediately; Octonomy keeps only a keyed hash):

```bash
python manage.py create_service_token \
  --name svc-catalog --tenant tenant_demo --application commerce \
  --scope tags:read --scope tags:write --scope audit:read
```

Run it where the code runs — the command needs the production environment:

- **Compose** (from `deploy/docker/`): `docker compose exec app python manage.py create_service_token ...`
- **Kubernetes**: `kubectl -n octonomy exec deploy/octonomy -- python manage.py create_service_token ...`
- **VPS**: run as the `octonomy` user with the env file sourced *inside* that context — plain `sudo`
  strips the variables, and the `0600` file is only readable as `octonomy`:
  `sudo -u octonomy bash -c 'set -a; . /etc/octonomy/octonomy.env; set +a; cd /opt/octonomy && .venv/bin/python manage.py create_service_token --name svc-catalog --tenant tenant_demo --application commerce --scope tags:read --scope tags:write'`

To use the optional admin console, also create a superuser (`python manage.py createsuperuser`) and
enable it deliberately — see [operations.md, "Admin console"](operations.md#admin-console).

---

## Upgrades and backups

- **Back up PostgreSQL before running migrations** in any shared environment — the database holds all
  canonical state.
- Enabling the merchant-namespace layer at scale takes a brief table lock during the constraint swap.
  Size it first; see [operations.md, "Constraint-swap lock window"](operations.md#constraint-swap-lock-window-ns-6).
- The namespace rollout/rollback ladder, the outbox webhook transport, dashboards, and restore drills
  are all documented in [operations.md](operations.md). Most deployments run the safe defaults and
  never touch the rollout flags.
