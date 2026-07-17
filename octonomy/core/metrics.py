"""Namespace observability as structured log events.

Octonomy ships no metrics backend; observability is structured JSON logs (see
``octonomy.core.logging.JsonFormatter``). Dashboards and alerts are built by
aggregating these events in the log pipeline. Most namespace signals ride the
``request_completed`` line already emitted by ``RequestContextMiddleware`` (requests
by version + namespace type, endpoint latency, 4xx-by-reason). ``emit_metric`` is for
signals that do not map cleanly onto that line: duplicate-key collisions on the new
namespace constraints (``namespace_conflict``) and outbox lag by namespace type
(``outbox_dispatch_summary``). The generic ``error_code="conflict"`` on the request
line is deliberately NOT used for the duplicate-key signal — it also covers
business-rule conflicts (e.g. scope-move-blocked), so it would conflate the two;
``namespace_conflict`` is emitted only from the actual uniqueness-violation branches.

Each call is one JSON line: ``{"metric": "<name>", ...dimensions/values}``. Keep
field names stable — dashboards key off them. See docs/operations.md
"Namespace Rollout & Operations" for the field reference and example queries.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("octonomy.metrics")

# Metric event names. Centralised so producers and the runbook cannot drift.
NAMESPACE_CONFLICT = "namespace_conflict"
OUTBOX_DISPATCH_SUMMARY = "outbox_dispatch_summary"


def emit_metric(name: str, **fields) -> None:
    """Emit one structured metric event.

    ``fields`` are the metric's dimensions and values (e.g. ``entity="tag"``,
    ``namespace_type="merchant"``, ``count=1``). They are serialised verbatim into
    the JSON log line, so pass primitives dashboards can group and sum.
    """

    logger.info(name, extra={"metric": name, "metric_fields": fields})


def emit_namespace_conflict(entity: str, scope_context) -> None:
    """Record a duplicate-key collision on a namespace-aware unique constraint.

    Emitted only from the ``IntegrityError`` branches that raise a 409 for a real
    uniqueness violation (never from business-rule conflicts), so the count is a
    clean "duplicate-key errors on the new constraints" signal. ``namespace_type``
    is null for a global-scope collision, so dashboards can split global vs merchant.
    """

    emit_metric(
        NAMESPACE_CONFLICT,
        entity=entity,
        namespace_type=scope_context.namespace_type,
        namespace_id=scope_context.namespace_id,
    )
