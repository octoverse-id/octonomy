"""Gunicorn configuration for the production container image.

Auto-loaded by Gunicorn from the working directory (/app) when the process is
started as `gunicorn config.wsgi:application`. Every value is overridable via an
environment variable so the same image can be tuned per deployment without a
rebuild. Local development does not use this file — docker-compose overrides the
container command to run Django's autoreloading dev server (see docker-compose.yml).
"""

from __future__ import annotations

import os
import sys

# Gunicorn loads this config file by path, so /app (its directory) is not guaranteed on
# sys.path when logconfig_dict below is processed. Insert it so the app's JsonFormatter
# is importable here; without this Gunicorn aborts with "Unable to configure root logger".
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from octonomy.core.logging import JsonFormatter  # noqa: E402  (import needs the path insert above)

# Bind to all interfaces on 8000 — the port the image EXPOSEs and that a Kubernetes
# Service / container runtime targets.
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")

# Worker processes. WEB_CONCURRENCY is the conventional knob (Gunicorn honours it as
# the default too); size it to the pod's CPU/latency profile, e.g. ~2*cores+1.
workers = int(os.getenv("WEB_CONCURRENCY", "2"))

# Worker class + threads. Default sync workers suit this DB-backed API; switch to
# "gthread" (with GUNICORN_THREADS > 1) for more concurrency per worker if needed.
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "sync")
threads = int(os.getenv("GUNICORN_THREADS", "1"))

# Recycle workers periodically to bound memory growth; jitter avoids a restart herd.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# Timeouts. graceful_timeout lets in-flight requests drain on a rolling deploy / pod
# termination before the worker is force-killed. Keep it a few seconds UNDER the pod's
# terminationGracePeriodSeconds (k8s default 30s) so Gunicorn finishes draining and the
# master exits cleanly before the kubelet sends SIGKILL — otherwise a slow shutdown
# races the grace window and in-flight requests are cut off.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "30"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "25"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Logging. The whole stream stays structured JSON to honor the service's logging
# contract: Gunicorn's own records (lifecycle messages + worker-crash tracebacks) are
# routed through the app's JsonFormatter via logconfig_dict below, so nothing plain-text
# leaks into the stream. Gunicorn's per-request ACCESS log is suppressed by default (a
# null handler): the app's RequestContextMiddleware already emits a richer JSON line per
# request (logger octonomy.requests, "request_completed", with
# request_id/method/path/status_code/duration_ms), so a gunicorn.access line would only
# duplicate it. Set GUNICORN_ACCESS_LOG to a truthy value (1/true/yes/on) to also emit
# Gunicorn's access log as JSON (e.g. for debugging). No log files inside the container.
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
# Match the app's env_bool truthy set so "0"/"false"/"no" read as OFF, not "any non-empty".
_access_on = os.getenv("GUNICORN_ACCESS_LOG", "").strip().lower() in {"1", "true", "yes", "on"}
# The logconfig_dict handler below is the actual on/off switch (null vs JSON), because
# setting logconfig_dict already satisfies Gunicorn's access() emit gate. Mirror the
# state in the accesslog option too so the two never disagree and intent is explicit;
# the dictConfig pass runs last and owns the final gunicorn.access handler either way.
accesslog = "-" if _access_on else None
_access_handlers = ["json_stderr"] if _access_on else ["null"]

# Reuse the application's JsonFormatter (stdlib-only, no Django import) for Gunicorn's
# gunicorn.error / gunicorn.access loggers so their output matches the app's JSON shape.
# propagate=False keeps these records on this handler alone (no duplication up to root,
# which each worker configures from Django settings when it loads the WSGI app).
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # Pass the class object (not a dotted-path string) so dictConfig uses it
        # directly and never re-imports it at logging-setup time.
        "json": {"()": JsonFormatter},
    },
    "handlers": {
        "json_stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "json",
        },
        # Drops Gunicorn's redundant per-request access records by default (see above).
        "null": {"class": "logging.NullHandler"},
    },
    # Gunicorn shallow-merges this over its defaults, so we must supply "root" too —
    # otherwise Gunicorn's default root keeps pointing at its (now-replaced) plain-text
    # handler and setup aborts with "Unable to configure root logger". Each worker later
    # reconfigures root from Django settings (also JSON) when it loads the WSGI app.
    "root": {"handlers": ["json_stderr"], "level": loglevel.upper()},
    "loggers": {
        "gunicorn.error": {
            "handlers": ["json_stderr"],
            "level": loglevel.upper(),
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": _access_handlers,
            "level": loglevel.upper(),
            "propagate": False,
        },
    },
}
