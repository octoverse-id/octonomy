from __future__ import annotations

import pytest

from octonomy.core.auth import (
    GLOBAL_SCOPE,
    ScopeContext,
    authorized_scope_contexts,
    grant_authorizes,
)
from octonomy.service_auth.models import ServiceClientGrant
from octonomy.service_auth.services import create_service_client_token, grant_allows

MERCHANT_A = ScopeContext("merchant", "merchant_a")
MERCHANT_B = ScopeContext("merchant", "merchant_b")


def grant(
    *,
    application_id: str | None,
    namespace_type: str | None = None,
    namespace_id: str | None = None,
    namespace_wildcard: bool = False,
    tenant_id: str = "tenant_a",
    scopes: list[str] | None = None,
) -> ServiceClientGrant:
    return ServiceClientGrant(
        tenant_id=tenant_id,
        application_id=application_id,
        namespace_type=namespace_type,
        namespace_id=namespace_id,
        namespace_wildcard=namespace_wildcard,
        scopes=scopes or ["tags:read"],
    )


@pytest.mark.parametrize(
    ("grant_record", "expected"),
    [
        (grant(application_id=None), (True, False, False)),
        (grant(application_id="commerce"), (True, False, False)),
        (
            grant(
                application_id="commerce",
                namespace_type="merchant",
                namespace_id="merchant_a",
            ),
            (False, True, False),
        ),
        (
            grant(
                application_id="commerce",
                namespace_type="merchant",
                namespace_id="merchant_b",
            ),
            (False, False, True),
        ),
        (grant(application_id="commerce", namespace_wildcard=True), (True, True, True)),
        (grant(application_id=None, namespace_wildcard=True), (True, True, True)),
    ],
    ids=[
        "tenant-global",
        "application-global",
        "merchant-a",
        "merchant-b",
        "application-wildcard",
        "tenant-wildcard",
    ],
)
def test_grant_authorization_matrix_for_application(grant_record, expected):
    actual = tuple(
        grant_authorizes(
            grant_record,
            tenant_id="tenant_a",
            application_id="commerce",
            scope_context=scope_context,
            required_scope="tags:read",
        )
        for scope_context in (GLOBAL_SCOPE, MERCHANT_A, MERCHANT_B)
    )

    assert actual == expected


@pytest.mark.parametrize(
    ("grant_record", "expected"),
    [
        (grant(application_id=None), (True, False)),
        (grant(application_id="commerce"), (False, False)),
        (
            grant(
                application_id="commerce",
                namespace_type="merchant",
                namespace_id="merchant_a",
            ),
            (False, False),
        ),
        (grant(application_id="commerce", namespace_wildcard=True), (False, False)),
        (grant(application_id=None, namespace_wildcard=True), (True, False)),
    ],
    ids=[
        "tenant-global",
        "application-global",
        "merchant-a",
        "application-wildcard",
        "tenant-wildcard",
    ],
)
def test_grant_authorization_matrix_when_application_is_omitted(grant_record, expected):
    actual = tuple(
        grant_authorizes(
            grant_record,
            tenant_id="tenant_a",
            application_id=None,
            scope_context=scope_context,
            required_scope="tags:read",
        )
        for scope_context in (GLOBAL_SCOPE, MERCHANT_A)
    )

    assert actual == expected


def test_grant_authorization_checks_tenant_application_and_required_scope_together():
    merchant_grant = grant(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
    )

    assert not grant_authorizes(
        merchant_grant,
        tenant_id="tenant_b",
        application_id="commerce",
        scope_context=MERCHANT_A,
        required_scope="tags:read",
    )
    assert not grant_authorizes(
        merchant_grant,
        tenant_id="tenant_a",
        application_id="cms",
        scope_context=MERCHANT_A,
        required_scope="tags:read",
    )
    assert not grant_authorizes(
        merchant_grant,
        tenant_id="tenant_a",
        application_id="commerce",
        scope_context=MERCHANT_A,
        required_scope="tags:write",
    )


def test_authorized_scope_set_intersects_include_global_candidates():
    grants = [
        grant(
            application_id="commerce",
            namespace_type="merchant",
            namespace_id="merchant_a",
        ),
        grant(application_id="commerce"),
    ]

    authorized = authorized_scope_contexts(
        grants,
        tenant_id="tenant_a",
        application_id="commerce",
        requested_scopes=[MERCHANT_A, GLOBAL_SCOPE],
        required_scope="tags:read",
    )

    assert authorized == frozenset({MERCHANT_A, GLOBAL_SCOPE})


def test_exact_namespace_grant_does_not_implicitly_authorize_global_fallback():
    authorized = authorized_scope_contexts(
        [
            grant(
                application_id="commerce",
                namespace_type="merchant",
                namespace_id="merchant_a",
            )
        ],
        tenant_id="tenant_a",
        application_id="commerce",
        requested_scopes=[MERCHANT_A, GLOBAL_SCOPE],
        required_scope="tags:read",
    )

    assert authorized == frozenset({MERCHANT_A})


def test_wildcard_grant_authorizes_requested_namespace_and_global_fallback():
    authorized = authorized_scope_contexts(
        [grant(application_id="commerce", namespace_wildcard=True)],
        tenant_id="tenant_a",
        application_id="commerce",
        requested_scopes=[MERCHANT_A, GLOBAL_SCOPE],
        required_scope="tags:read",
    )

    assert authorized == frozenset({MERCHANT_A, GLOBAL_SCOPE})


def test_scope_context_requires_both_namespace_fields():
    with pytest.raises(ValueError):
        ScopeContext(namespace_type="merchant")

    with pytest.raises(ValueError):
        ScopeContext(namespace_id="merchant_a")


@pytest.mark.parametrize(
    ("namespace_type", "namespace_id"),
    [
        ("", "merchant_a"),
        (" ", "merchant_a"),
        ("global", "merchant_a"),
        ("merchant", ""),
        ("merchant", " "),
    ],
)
def test_scope_context_rejects_invalid_namespace_values(namespace_type, namespace_id):
    with pytest.raises(ValueError):
        ScopeContext(namespace_type=namespace_type, namespace_id=namespace_id)


@pytest.mark.django_db
def test_legacy_null_grant_allows_global_but_denies_merchant_request():
    _, client = create_service_client_token(
        name="svc-legacy-global",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": None,
                "scopes": ["tags:read"],
            }
        ],
    )

    assert grant_allows(
        client,
        tenant_id="tenant_a",
        application_id="commerce",
        scope="tags:read",
    )
    assert not grant_allows(
        client,
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        scope="tags:read",
    )
