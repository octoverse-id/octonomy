from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Protocol
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import Count, Min
from django.utils import timezone

from octonomy.core.metrics import OUTBOX_DISPATCH_SUMMARY, emit_metric
from octonomy.events.models import OutboxEvent

logger = logging.getLogger(__name__)


class EventTransport(Protocol):
    def publish(self, event: OutboxEvent) -> None: ...


def serialize_outbox_event(event: OutboxEvent) -> dict:
    return {
        "id": str(event.id),
        "tenant_id": event.tenant_id,
        "application_id": event.application_id,
        # Additive namespace fields (issue #43). Global rows serialize as null,
        # preserving the pre-namespace shape for existing consumers, which ignore
        # unknown keys. See docs/events.md for the consumer-compatibility contract.
        "namespace_type": event.namespace_type,
        "namespace_id": event.namespace_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "operation_id": str(event.operation_id) if event.operation_id else None,
        "request_id": event.request_id,
        "actor_id": event.actor_id,
        "tag_id": str(event.tag_id) if event.tag_id else None,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "payload": event.payload,
        "metadata": event.metadata,
    }


class LoggingEventTransport:
    def publish(self, event: OutboxEvent) -> None:
        logger.info(
            "outbox_event_published",
            extra={"event": serialize_outbox_event(event)},
        )


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_webhook_opener():
    return urllib_request.build_opener(_NoRedirectHandler)


class WebhookEventTransport:
    def __init__(
        self,
        *,
        url: str,
        signing_secret: str,
        timeout_seconds: int,
        opener=None,
    ) -> None:
        _validate_webhook_url(url)
        self.url = url
        self.signing_secret = signing_secret
        self.timeout_seconds = timeout_seconds
        self.opener = opener or _build_webhook_opener()

    def publish(self, event: OutboxEvent) -> None:
        body = json.dumps(
            serialize_outbox_event(event),
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = urllib_request.Request(
            self.url,
            data=body,
            headers=_webhook_headers(event, body, self.signing_secret),
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            if status_code < 200 or status_code >= 300:
                raise RuntimeError(f"webhook returned HTTP {status_code}")


def _validate_webhook_url(url: str) -> None:
    parsed_url = urllib_parse.urlparse(url)
    if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.netloc:
        raise ImproperlyConfigured("OCTONOMY_WEBHOOK_URL must be an absolute http(s) URL.")


def _webhook_headers(event: OutboxEvent, body: bytes, signing_secret: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "octonomy-outbox",
        "X-Octonomy-Event-ID": str(event.id),
        "X-Octonomy-Event-Type": event.event_type,
        "X-Octonomy-Tenant-ID": event.tenant_id,
        "X-Octonomy-Signature": _webhook_signature(body, signing_secret),
    }
    if event.request_id:
        headers["X-Octonomy-Request-ID"] = event.request_id
    return headers


def _webhook_signature(body: bytes, signing_secret: str) -> str:
    digest = hmac.new(signing_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def transport_from_settings() -> EventTransport:
    transport_name = settings.OUTBOX_TRANSPORT.lower()
    if transport_name == "logging":
        return LoggingEventTransport()
    if transport_name == "webhook":
        if not settings.OUTBOX_WEBHOOK_URL:
            raise ImproperlyConfigured(
                "OCTONOMY_WEBHOOK_URL must be set when OCTONOMY_OUTBOX_TRANSPORT=webhook."
            )
        if not settings.OUTBOX_WEBHOOK_SIGNING_SECRET:
            raise ImproperlyConfigured(
                "OCTONOMY_WEBHOOK_SIGNING_SECRET must be set when "
                "OCTONOMY_OUTBOX_TRANSPORT=webhook."
            )
        if settings.OUTBOX_CLAIM_TIMEOUT_SECONDS <= settings.OUTBOX_WEBHOOK_TIMEOUT_SECONDS:
            raise ImproperlyConfigured(
                "OCTONOMY_OUTBOX_CLAIM_TIMEOUT_SECONDS must be greater than "
                "OCTONOMY_WEBHOOK_TIMEOUT_SECONDS when OCTONOMY_OUTBOX_TRANSPORT=webhook."
            )
        return WebhookEventTransport(
            url=settings.OUTBOX_WEBHOOK_URL,
            signing_secret=settings.OUTBOX_WEBHOOK_SIGNING_SECRET,
            timeout_seconds=settings.OUTBOX_WEBHOOK_TIMEOUT_SECONDS,
        )
    raise ImproperlyConfigured(f"Unsupported OCTONOMY_OUTBOX_TRANSPORT: {transport_name}")


def dispatch_outbox_events(
    *,
    limit: int = 100,
    retry_failed: bool = False,
    transport: EventTransport | None = None,
    max_attempts: int | None = None,
    retry_base_seconds: int | None = None,
    retry_max_seconds: int | None = None,
    claim_timeout_seconds: int | None = None,
) -> dict[str, int]:
    transport = transport or transport_from_settings()
    limit = max(0, limit)
    max_attempts = max(
        1,
        max_attempts if max_attempts is not None else settings.OUTBOX_MAX_ATTEMPTS,
    )
    retry_base_seconds = max(
        1,
        retry_base_seconds
        if retry_base_seconds is not None
        else settings.OUTBOX_RETRY_BASE_SECONDS,
    )
    retry_max_seconds = max(
        retry_base_seconds,
        retry_max_seconds if retry_max_seconds is not None else settings.OUTBOX_RETRY_MAX_SECONDS,
    )
    claim_timeout_seconds = max(
        1,
        claim_timeout_seconds
        if claim_timeout_seconds is not None
        else settings.OUTBOX_CLAIM_TIMEOUT_SECONDS,
    )
    if (
        isinstance(transport, WebhookEventTransport)
        and claim_timeout_seconds <= transport.timeout_seconds
    ):
        raise ImproperlyConfigured(
            "claim_timeout_seconds must be greater than the webhook timeout_seconds."
        )
    summary = {"published": 0, "failed": 0, "dead_lettered": 0, "recovered": 0}

    for _ in range(limit):
        event = _claim_next_event(
            retry_failed=retry_failed,
            claim_timeout_seconds=claim_timeout_seconds,
        )
        if event is None:
            break

        result = _publish_claimed_event(
            event=event,
            transport=transport,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )
        if result is None:
            continue
        summary[result] += 1

    recovery_summary = _recover_expired_claims(
        limit=limit,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
    )
    # An expired claim is re-queued for redelivery, not recorded as a delivery
    # failure, so it counts only under "recovered" — never conflated into "failed".
    summary["recovered"] += recovery_summary["recovered"]

    _emit_dispatch_metric(summary)
    return summary


def _emit_dispatch_metric(summary: dict[str, int]) -> None:
    """Structured heartbeat: run totals plus deliverable backlog lag per namespace.

    Emitted once per run (no request to ride on). ``lag_by_namespace_type`` reports,
    for each namespace type with a due backlog, how many events are waiting and how
    long the oldest has been due — the "outbox lag by namespace type" signal that
    flags a merchant namespace falling behind independently of global traffic.
    """

    now = timezone.now()
    rows = (
        OutboxEvent.objects.filter(
            status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED],
            available_at__lte=now,
        )
        .values("namespace_type")
        .annotate(backlog=Count("id"), oldest=Min("available_at"))
    )
    lag = {
        (row["namespace_type"] or "global"): {
            "backlog": row["backlog"],
            "oldest_pending_seconds": round((now - row["oldest"]).total_seconds(), 2),
        }
        for row in rows
    }
    emit_metric(
        OUTBOX_DISPATCH_SUMMARY,
        published=summary["published"],
        failed=summary["failed"],
        dead_lettered=summary["dead_lettered"],
        recovered=summary["recovered"],
        lag_by_namespace_type=lag,
    )


def _recover_expired_claims(
    *,
    limit: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> dict[str, int]:
    summary = {"recovered": 0}

    for _ in range(limit):
        with transaction.atomic():
            event = _next_expired_claim()
            if event is None:
                break
            result = _mark_recovered(
                event,
                "outbox claim expired before publish completed",
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        summary[result] += 1

    return summary


def _next_expired_claim() -> OutboxEvent | None:
    queryset = OutboxEvent.objects.filter(
        status=OutboxEvent.Status.PROCESSING,
        claim_expires_at__lte=timezone.now(),
    ).order_by("claim_expires_at", "created_at", "id")
    if getattr(connection.features, "has_select_for_update_skip_locked", False):
        queryset = queryset.select_for_update(skip_locked=True)
    return queryset.first()


def _claim_next_event(
    *,
    retry_failed: bool,
    claim_timeout_seconds: int,
) -> OutboxEvent | None:
    with transaction.atomic():
        event = _next_event(retry_failed=retry_failed)
        if event is None:
            return None

        event.status = OutboxEvent.Status.PROCESSING
        event.claim_id = uuid.uuid4()
        event.claimed_at = timezone.now()
        event.claim_expires_at = event.claimed_at + timezone.timedelta(
            seconds=claim_timeout_seconds
        )
        event.save(
            update_fields=[
                "status",
                "claim_id",
                "claimed_at",
                "claim_expires_at",
                "updated_at",
            ]
        )
        return event


def _publish_claimed_event(
    *,
    event: OutboxEvent,
    transport: EventTransport,
    max_attempts: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> str | None:
    claim_id = event.claim_id
    try:
        transport.publish(event)
    except Exception as exc:
        with transaction.atomic():
            claimed_event = _claimed_event(event.id, claim_id)
            if claimed_event is None:
                logger.warning(
                    "outbox_claim_lost",
                    extra={
                        "event_id": str(event.id),
                        "claim_id": str(claim_id) if claim_id else None,
                    },
                )
                return None
            return _mark_failed(
                claimed_event,
                exc,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )

    with transaction.atomic():
        claimed_event = _claimed_event(event.id, claim_id)
        if claimed_event is None:
            logger.error(
                "outbox_delivered_but_claim_lost",
                extra={
                    "event_id": str(event.id),
                    "claim_id": str(claim_id) if claim_id else None,
                },
            )
            return None
        _mark_published(claimed_event)
    return "published"


def _next_event(*, retry_failed: bool) -> OutboxEvent | None:
    statuses = [OutboxEvent.Status.PENDING]
    if retry_failed:
        statuses.append(OutboxEvent.Status.FAILED)

    queryset = OutboxEvent.objects.filter(
        status__in=statuses, available_at__lte=timezone.now()
    ).order_by("available_at", "created_at", "id")
    if getattr(connection.features, "has_select_for_update_skip_locked", False):
        queryset = queryset.select_for_update(skip_locked=True)
    return queryset.first()


def _claimed_event(event_id, claim_id) -> OutboxEvent | None:
    queryset = OutboxEvent.objects.filter(
        id=event_id,
        claim_id=claim_id,
        status=OutboxEvent.Status.PROCESSING,
    )
    if getattr(connection.features, "has_select_for_update_skip_locked", False):
        queryset = queryset.select_for_update(skip_locked=True)
    return queryset.first()


def _mark_published(event: OutboxEvent) -> None:
    event.status = OutboxEvent.Status.PUBLISHED
    event.attempts += 1
    event.last_error = ""
    event.published_at = timezone.now()
    event.claim_id = None
    event.claimed_at = None
    event.claim_expires_at = None
    event.save(
        update_fields=[
            "status",
            "attempts",
            "last_error",
            "published_at",
            "claim_id",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )


def _mark_failed(
    event: OutboxEvent,
    exc: Exception,
    *,
    max_attempts: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> str:
    event.attempts += 1
    event.last_error = str(exc)
    event.claim_id = None
    event.claimed_at = None
    event.claim_expires_at = None

    if event.attempts >= max_attempts:
        event.status = OutboxEvent.Status.DEAD_LETTER
        event.save(
            update_fields=[
                "status",
                "attempts",
                "last_error",
                "claim_id",
                "claimed_at",
                "claim_expires_at",
                "updated_at",
            ]
        )
        return "dead_lettered"

    event.status = OutboxEvent.Status.FAILED
    event.available_at = timezone.now() + timezone.timedelta(
        seconds=_retry_delay_seconds(
            attempt_number=event.attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )
    )
    event.save(
        update_fields=[
            "status",
            "attempts",
            "last_error",
            "available_at",
            "claim_id",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )
    return "failed"


def _mark_recovered(
    event: OutboxEvent,
    note: str,
    *,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> str:
    # An expired claim means the worker vanished before its completion landed; we
    # cannot tell whether delivery happened. Under at-least-once semantics the
    # event is re-queued for redelivery (status PENDING) rather than recorded as a
    # delivery failure — so a successfully-delivered-but-claim-expired event is
    # never left FAILED. Recovery bumps `recoveries` (observability), never
    # `attempts`, and therefore never dead-letters: a claim expiry is not a
    # delivery attempt. Backoff grows with the recovery count to avoid hammering a
    # row whose worker keeps dying.
    event.recoveries += 1
    event.last_error = note
    event.status = OutboxEvent.Status.PENDING
    # Back off on the recovery count, but never schedule sooner than the failure
    # backoff the row already earned from its delivery `attempts`. Otherwise an
    # event that failed several times (large backoff) whose retry claim then
    # expires would be re-queued at the small first-recovery delay and hammer a
    # still-failing destination. attempts is a floor, never incremented here.
    event.available_at = timezone.now() + timezone.timedelta(
        seconds=max(
            _retry_delay_seconds(
                attempt_number=event.recoveries,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            ),
            _retry_delay_seconds(
                attempt_number=event.attempts,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            ),
        )
    )
    event.claim_id = None
    event.claimed_at = None
    event.claim_expires_at = None
    event.save(
        update_fields=[
            "status",
            "recoveries",
            "last_error",
            "available_at",
            "claim_id",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )
    return "recovered"


def _retry_delay_seconds(
    *,
    attempt_number: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> int:
    return min(retry_max_seconds, retry_base_seconds * (2 ** max(attempt_number - 1, 0)))
