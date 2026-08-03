#!/bin/sh
# Fail-closed startup: run Django's system checks before serving.
#
# Gunicorn runs the WSGI app directly (config.wsgi -> get_wsgi_application), which runs
# django.setup() but NOT Django's system checks — unlike `manage.py runserver`, which
# ran them at startup. Without this step an invalid namespace rollout flag combination
# (e.g. OCTONOMY_NAMESPACE_WRITE_ENABLED=true with OCTONOMY_NAMESPACE_READ_ENABLED=false)
# that octonomy/core/checks.py declares unbootable (E013) would boot into an unsafe
# state instead of being rejected.
#
# `manage.py check` reads settings only — no database, no --deploy — so it is safe to
# run before migrations apply (the DB-dependent E016 stays a deploy-pipeline concern,
# `manage.py check --deploy`). On a check error this exits non-zero via `set -e`, so the
# container never starts serving. `exec` hands PID 1 to the real process so it receives
# SIGTERM directly for graceful shutdown.
#
# On success the check writes a plain-text "no issues" banner to stdout; we drop it
# (>/dev/null) so the running log stream stays all-JSON (Gunicorn + app). A failure
# raises SystemCheckError to stderr (kept) and exits non-zero, so problems are still
# surfaced and the container never serves.
set -e
python manage.py check >/dev/null
exec "$@"
