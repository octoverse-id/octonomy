from __future__ import annotations

import json
import logging

# Structured fields that carry request/operation context or metric dimensions.
# Included in the JSON payload whenever present (and non-null), so one log stream
# doubles as the metrics source: version + namespace type dimension request
# counts, duration_ms is endpoint latency, and error_code is the 4xx/deny reason.
CONTEXT_FIELDS = (
    "request_id",
    "operation_id",
    "tenant_id",
    "version",
    "namespace_requested",
    "namespace_type",
    "namespace_id",
    "method",
    "path",
    "status_code",
    "error_code",
    "duration_ms",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        # Metric events (octonomy.core.metrics.emit_metric) carry a metric name and
        # a flat dict of dimensions/values. Merging them keeps each metric a single
        # queryable JSON line without the formatter knowing each metric's shape.
        metric = getattr(record, "metric", None)
        if metric is not None:
            payload["metric"] = metric
            payload.update(getattr(record, "metric_fields", {}) or {})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
