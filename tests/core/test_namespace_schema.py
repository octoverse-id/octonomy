from __future__ import annotations

import uuid
from collections.abc import Callable
from io import StringIO

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from octonomy.service_auth.models import ServiceClient, ServiceClientGrant
from octonomy.service_auth.services import create_service_client_token
from octonomy.tags.models import Tag, TagAlias, Vocabulary

pytestmark = pytest.mark.django_db


def assert_integrity_error(callback: Callable[[], object]) -> None:
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            callback()


def make_service_client() -> ServiceClient:
    suffix = uuid.uuid4().hex
    return ServiceClient.objects.create(
        name=f"svc-{suffix}",
        key_prefix=suffix[:8],
        hashed_key=suffix,
    )


def create_vocabulary(**overrides) -> Vocabulary:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "name": "Labels",
        "slug": "labels",
        "metadata": {},
        "is_active": True,
    }
    data.update(overrides)
    return Vocabulary.objects.create(**data)


def create_tag(**overrides) -> Tag:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "name": "Premium",
        "slug": "premium",
        "type": "label",
        "metadata": {},
        "is_active": True,
    }
    data.update(overrides)
    return Tag.objects.create(**data)


def create_alias(**overrides) -> TagAlias:
    tag = overrides.pop("tag", None)
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "tag": tag or create_tag(slug=f"tag-{uuid.uuid4().hex[:8]}"),
        "name": "Promoted",
        "slug": "promoted",
        "metadata": {},
        "is_active": True,
    }
    data.update(overrides)
    return TagAlias.objects.create(**data)


def create_assignment(**overrides) -> TagAssignment:
    tag = overrides.pop("tag", None)
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "tag": tag or create_tag(slug=f"assignment-tag-{uuid.uuid4().hex[:8]}"),
        "resource_type": "product",
        "resource_id": "prod_123",
    }
    data.update(overrides)
    return TagAssignment.objects.create(**data)


def create_audit_log(**overrides) -> AuditLog:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "action": "tag.created",
        "entity_type": "tag",
        "entity_id": "tag_123",
    }
    data.update(overrides)
    return AuditLog.objects.create(**data)


def create_outbox_event(**overrides) -> OutboxEvent:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "event_type": "tag.created",
        "aggregate_type": "tag",
        "aggregate_id": "tag_123",
        "payload": {},
        "metadata": {},
    }
    data.update(overrides)
    return OutboxEvent.objects.create(**data)


def create_grant(**overrides) -> ServiceClientGrant:
    data = {
        "service_client": make_service_client(),
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "scopes": ["tags:read"],
    }
    data.update(overrides)
    return ServiceClientGrant.objects.create(**data)


@pytest.mark.parametrize(
    "model",
    [Tag, Vocabulary, TagAlias, TagAssignment, AuditLog, OutboxEvent, ServiceClientGrant],
)
def test_namespace_fields_exist_on_all_scoped_models(model):
    assert model._meta.get_field("namespace_type").max_length == 100
    assert model._meta.get_field("namespace_id").max_length == 100


@pytest.mark.parametrize(
    "factory",
    [
        create_tag,
        create_vocabulary,
        create_alias,
        create_assignment,
        create_audit_log,
        create_outbox_event,
        create_grant,
    ],
)
def test_namespaced_rows_require_application_scope(factory):
    assert_integrity_error(
        lambda: factory(
            application_id=None,
            namespace_type="merchant",
            namespace_id="merchant_a",
        )
    )


@pytest.mark.parametrize("field", ["namespace_type", "namespace_id"])
def test_namespace_scope_requires_type_and_id_together(field):
    kwargs = {
        "namespace_type": "merchant",
        "namespace_id": "merchant_a",
    }
    kwargs[field] = None

    assert_integrity_error(lambda: create_tag(**kwargs))


@pytest.mark.parametrize("namespace_type", ["", "global"])
def test_data_rows_reject_reserved_or_blank_namespace_type(namespace_type):
    assert_integrity_error(
        lambda: create_tag(
            namespace_type=namespace_type,
            namespace_id="merchant_a",
        )
    )


def test_namespace_scoped_model_full_clean_accepts_global_and_namespaced_rows():
    Tag(
        tenant_id="tenant_a",
        application_id=None,
        name="Featured",
        slug="featured",
        type="label",
        metadata={"source": "test"},
    ).full_clean()
    Tag(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        name="Premium",
        slug="premium",
        type="label",
        metadata={"source": "test"},
    ).full_clean()


@pytest.mark.parametrize(
    "overrides,field",
    [
        (
            {
                "application_id": None,
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
            },
            "application_id",
        ),
        (
            {
                "application_id": "commerce",
                "namespace_type": "global",
                "namespace_id": "merchant_a",
            },
            "namespace_type",
        ),
        (
            {
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "",
            },
            "namespace_id",
        ),
    ],
)
def test_namespace_scoped_model_full_clean_rejects_invalid_scope(overrides, field):
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "name": "Premium",
        "slug": "premium",
        "type": "label",
        "metadata": {"source": "test"},
    }
    data.update(overrides)

    with pytest.raises(DjangoValidationError) as exc_info:
        Tag(**data).full_clean()

    assert field in exc_info.value.message_dict


def test_tag_slug_uniqueness_is_split_by_namespace():
    first = create_tag(namespace_type="merchant", namespace_id="merchant_a")
    second = create_tag(namespace_type="merchant", namespace_id="merchant_b")

    assert first.slug == second.slug
    assert first.id != second.id
    assert_integrity_error(lambda: create_tag(namespace_type="merchant", namespace_id="merchant_a"))


def test_vocabulary_slug_uniqueness_is_split_by_namespace():
    first = create_vocabulary(namespace_type="merchant", namespace_id="merchant_a")
    second = create_vocabulary(namespace_type="merchant", namespace_id="merchant_b")

    assert first.slug == second.slug
    assert first.id != second.id
    assert_integrity_error(
        lambda: create_vocabulary(namespace_type="merchant", namespace_id="merchant_a")
    )


def test_alias_slug_uniqueness_is_split_by_namespace():
    merchant_a_tag = create_tag(namespace_type="merchant", namespace_id="merchant_a")
    merchant_b_tag = create_tag(namespace_type="merchant", namespace_id="merchant_b")
    first = create_alias(
        tag=merchant_a_tag,
        namespace_type="merchant",
        namespace_id="merchant_a",
    )
    second = create_alias(
        tag=merchant_b_tag,
        namespace_type="merchant",
        namespace_id="merchant_b",
    )

    assert first.slug == second.slug
    assert first.id != second.id
    assert_integrity_error(
        lambda: create_alias(
            tag=merchant_a_tag,
            namespace_type="merchant",
            namespace_id="merchant_a",
        )
    )


def test_assignment_uniqueness_is_split_by_namespace():
    tag = create_tag(slug="assignable")
    create_assignment(tag=tag, namespace_type="merchant", namespace_id="merchant_a")
    create_assignment(tag=tag, namespace_type="merchant", namespace_id="merchant_b")
    create_assignment(tag=tag)

    assert TagAssignment.objects.count() == 3
    assert_integrity_error(lambda: create_assignment(tag=tag))
    assert_integrity_error(
        lambda: create_assignment(
            tag=tag,
            namespace_type="merchant",
            namespace_id="merchant_a",
        )
    )


def test_service_grant_wildcard_is_explicit_and_collision_proof():
    service_client = make_service_client()
    global_grant = ServiceClientGrant.objects.create(
        service_client=service_client,
        tenant_id="tenant_a",
        application_id="commerce",
        scopes=["tags:read"],
    )
    wildcard_grant = ServiceClientGrant.objects.create(
        service_client=service_client,
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_wildcard=True,
        scopes=["tags:read"],
    )

    assert global_grant.namespace_type is None
    assert global_grant.namespace_id is None
    assert global_grant.namespace_wildcard is False
    assert wildcard_grant.namespace_type is None
    assert wildcard_grant.namespace_id is None
    assert wildcard_grant.namespace_wildcard is True

    assert_integrity_error(
        lambda: ServiceClientGrant.objects.create(
            service_client=service_client,
            tenant_id="tenant_a",
            application_id="commerce",
            namespace_wildcard=True,
            scopes=["tags:read"],
        )
    )
    assert_integrity_error(
        lambda: ServiceClientGrant.objects.create(
            service_client=service_client,
            tenant_id="tenant_a",
            application_id="commerce",
            namespace_type="merchant",
            namespace_id="merchant_a",
            namespace_wildcard=True,
            scopes=["tags:read"],
        )
    )


def test_service_grant_exact_namespace_uniqueness_is_split_from_global():
    service_client = make_service_client()
    ServiceClientGrant.objects.create(
        service_client=service_client,
        tenant_id="tenant_a",
        application_id="commerce",
        scopes=["tags:read"],
    )
    ServiceClientGrant.objects.create(
        service_client=service_client,
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        scopes=["tags:read"],
    )

    assert ServiceClientGrant.objects.count() == 2
    assert_integrity_error(
        lambda: ServiceClientGrant.objects.create(
            service_client=service_client,
            tenant_id="tenant_a",
            application_id="commerce",
            namespace_type="merchant",
            namespace_id="merchant_a",
            scopes=["tags:read"],
        )
    )


def test_service_token_creation_can_create_wildcard_grant_without_autopromoting_nulls():
    _, service_client = create_service_client_token(
        name="svc-namespace",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_wildcard": True,
                "scopes": ["tags:read"],
            },
            {
                "tenant_id": "tenant_b",
                "application_id": "commerce",
                "scopes": ["tags:read"],
            },
        ],
    )

    wildcard = service_client.grants.get(tenant_id="tenant_a")
    legacy = service_client.grants.get(tenant_id="tenant_b")

    assert wildcard.namespace_wildcard is True
    assert wildcard.namespace_type is None
    assert wildcard.namespace_id is None
    assert legacy.namespace_wildcard is False
    assert legacy.namespace_type is None
    assert legacy.namespace_id is None


def test_verify_namespace_scope_command_reports_counts():
    create_tag()
    create_tag(namespace_type="merchant", namespace_id="merchant_a")
    create_grant(namespace_wildcard=True)
    out = StringIO()

    call_command("verify_namespace_scope", stdout=out)

    output = out.getvalue()
    assert "table=tags global=1 namespaced=1 violations=0" in output
    assert "table=service_client_grants global=0 namespaced=0 wildcard=1 violations=0" in output
    assert "total_violations=0" in output
