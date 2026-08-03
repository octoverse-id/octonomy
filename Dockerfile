# syntax=docker/dockerfile:1

# Pin the base image by digest for reproducible builds; the same digest is reused for
# both stages via this global ARG. Dependabot's docker ecosystem
# (.github/dependabot.yml) keeps the digest fresh with security patches.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# --- Builder ---------------------------------------------------------------------
# Resolve and install ONLY the runtime dependencies (no [dev] extras) into an
# isolated virtualenv, driven by the committed uv.lock for reproducible builds.
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Pin uv itself so the build tool version cannot drift between builds.
RUN pip install --no-cache-dir uv==0.9.5

# Copy only the dependency manifests first so this layer caches across source edits.
# README.md is referenced by pyproject's project metadata.
COPY pyproject.toml uv.lock README.md ./

# --no-install-project: install just the locked dependencies into /opt/venv; the app
# itself runs from the source tree copied in the runtime stage (via manage.py / wsgi),
# so it does not need to be built as a wheel. --no-dev excludes dev-only tooling.
# The venv lives at /opt/venv (not /app/.venv) so a local docker-compose bind mount of
# the source over /app cannot shadow it.
RUN uv sync --frozen --no-install-project --no-dev

# --- Runtime ---------------------------------------------------------------------
# Carry only the venv + application source. No uv, no build toolchain, no dev extras.
FROM ${PYTHON_IMAGE} AS runtime

# DJANGO_DEBUG defaults to false in the image: a production container must never fall
# into debug mode if the deployment omits the variable (debug mode leaks tracebacks and
# bypasses the default-secret boot guards). docker-compose sets DJANGO_DEBUG=true
# explicitly to override this for local development.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=false \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Unprivileged runtime user so the image satisfies clusters that enforce
# runAsNonRoot. Fixed uid/gid for predictable file ownership.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv
COPY . .

# Stage the admin/Unfold static assets into STATIC_ROOT. The API is headless, so this
# only matters if OCTONOMY_ADMIN_ENABLED=true is later set; even then the app does NOT
# serve them itself (no WhiteNoise — an external server/CDN serves STATIC_ROOT per
# docs/operations.md). This just bakes the assets so that server has them. Run with
# DJANGO_DEBUG=true so the boot secret guards (which fire only when DEBUG=false, the
# image default above) do not trip during the build; collectstatic does not touch the DB.
# Files stay root-owned and world-readable — the runtime never writes to /app (sessions
# are DB-backed, PYTHONDONTWRITEBYTECODE stops .pyc writes), so the non-root user only
# needs read/execute. Leaving source read-only is the least-privilege posture.
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput

USER app

EXPOSE 8000

# Fail-closed startup: docker-entrypoint.sh runs `manage.py check` (Django's system
# checks — notably the namespace rollout flag-dependency contract in
# octonomy/core/checks.py, e.g. E013) before exec'ing the command below. Gunicorn serves
# the WSGI app directly and would otherwise skip the checks that `manage.py runserver`
# used to run at startup. Invoked via `sh` so it needs no executable bit.
# Migrations are NOT run here — they are an explicit deploy step (a separate migration
# Job/init-container in the k8s work).
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application"]
