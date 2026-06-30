from __future__ import annotations

from types import SimpleNamespace

import pytest
from rest_framework.exceptions import NotFound

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext, request_include_global
from octonomy.tags.views import get_tag_or_404
from tests.factories import make_tag

pytestmark = pytest.mark.django_db

MERCHANT_A = ScopeContext("merchant", "merchant_a")


def test_request_include_global_requires_global_in_authorized_scopes():
    # An exact merchant grant excludes GLOBAL_SCOPE from the authorized set, so
    # the request must not fall back to tenant-wide rows.
    merchant_only = SimpleNamespace(authorized_scope_contexts=frozenset({MERCHANT_A}))
    assert request_include_global(merchant_only) is False

    with_global = SimpleNamespace(authorized_scope_contexts=frozenset({MERCHANT_A, GLOBAL_SCOPE}))
    assert request_include_global(with_global) is True

    # Legacy / global-only requests (and direct service calls) have no authorized
    # scope set; default to including global to preserve v1 behaviour.
    assert request_include_global(SimpleNamespace()) is True


def test_exact_scope_detail_lookup_hides_global_rows_from_namespaced_writes():
    global_tag = make_tag(slug="global-governed")
    merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-governed",
    )

    # A namespaced write (exact scope) must not resolve a tenant-wide tag, so a
    # merchant caller cannot PATCH / DELETE / deactivate a global row.
    with pytest.raises(NotFound):
        get_tag_or_404("tenant_a", global_tag.id, MERCHANT_A, include_global=False)

    found = get_tag_or_404("tenant_a", merchant_tag.id, MERCHANT_A, include_global=False)
    assert found.id == merchant_tag.id

    # An authorized read fallback still resolves the global row.
    found_global = get_tag_or_404("tenant_a", global_tag.id, MERCHANT_A, include_global=True)
    assert found_global.id == global_tag.id
