"""v2 namespace read/write behaviour and isolation (issue #42)."""

from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.tags.models import Tag, TagAlias, Vocabulary
from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db

APP = "commerce"


def client_for(token, *, namespace_type=None, namespace_id=None):
    client = APIClient()
    creds = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_TENANT_ID": "tenant_a"}
    if namespace_type is not None:
        creds["HTTP_X_NAMESPACE_TYPE"] = namespace_type
    if namespace_id is not None:
        creds["HTTP_X_NAMESPACE_ID"] = namespace_id
    client.credentials(**creds)
    return client


@pytest.fixture
def scoped_tags(db):
    """One tag per scope, all sharing name+slug so ordering/dedup is exercised."""

    return {
        "global": make_tag(application_id=APP, slug="premium", name="Premium"),
        "merchant_a": make_tag(
            application_id=APP,
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="premium",
            name="Premium",
        ),
        "merchant_b": make_tag(
            application_id=APP,
            namespace_type="merchant",
            namespace_id="merchant_b",
            slug="premium",
            name="Premium",
        ),
    }


def list_ids(client, query=""):
    response = client.get(f"/api/v2/tags?application_id={APP}{query}")
    assert response.status_code == 200, response.data
    return [item["id"] for item in response.json()["data"]], response.json()["pagination"]


# --- reads: exclude-by-default, opt-in merge, isolation -----------------------


def test_merchant_list_excludes_global_by_default(wildcard_token, scoped_tags):
    # The wildcard grant *could* see global, but global is excluded unless asked.
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    ids, _ = list_ids(client)
    assert ids == [str(scoped_tags["merchant_a"].id)]


def test_merchant_list_include_global_merges(wildcard_token, scoped_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    ids, pagination = list_ids(client, "&include_global=true")
    assert set(ids) == {str(scoped_tags["merchant_a"].id), str(scoped_tags["global"].id)}
    assert str(scoped_tags["merchant_b"].id) not in ids
    assert pagination["count"] == 2


def test_include_global_is_fail_closed_for_exact_grant(merchant_token, scoped_tags):
    # An exact merchant grant is not authorized for global, so include_global is
    # a no-op rather than a leak.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    ids, _ = list_ids(client, "&include_global=true")
    assert ids == [str(scoped_tags["merchant_a"].id)]


def test_merged_pagination_is_stable_and_has_no_dedup(wildcard_token, scoped_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")

    def page_through():
        seen, offset = [], 0
        while True:
            ids, pagination = list_ids(client, f"&include_global=true&limit=1&offset={offset}")
            seen.extend(ids)
            offset += 1
            if offset >= pagination["count"]:
                break
        return seen

    first, second = page_through(), page_through()
    # Same-(name, slug) rows across scopes are distinct rows: two ids, no dedup,
    # and the id tiebreaker keeps the merged order stable across paginations.
    assert first == second
    assert len(first) == len(set(first)) == 2


def test_v2_detail_cannot_see_other_merchant(wildcard_token, scoped_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.get(f"/api/v2/tags/{scoped_tags['merchant_b'].id}?application_id={APP}")
    assert response.status_code == 404


def test_detail_read_stays_within_requested_application(tenant_wildcard_token):
    # A tenant-wide grant must not receive a commerce row for an application=cms
    # request. Reads are bound to the request-named application, like the list.
    commerce_tag = make_tag(
        application_id=APP, namespace_type="merchant", namespace_id="merchant_a", slug="commercerow"
    )
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    assert client.get(f"/api/v2/tags/{commerce_tag.id}?application_id=cms").status_code == 404
    assert client.get(f"/api/v2/tags/{commerce_tag.id}?application_id={APP}").status_code == 200


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_detail_delete_stays_within_requested_application(tenant_wildcard_token):
    commerce_tag = make_tag(
        application_id=APP, namespace_type="merchant", namespace_id="merchant_a", slug="commercedel"
    )
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    # Names cms but the row is commerce → not found, not deleted.
    assert client.delete(f"/api/v2/tags/{commerce_tag.id}?application_id=cms").status_code == 404
    assert Tag.objects.get(id=commerce_tag.id).is_active


def test_v2_detail_global_excluded_by_default_visible_with_opt_in(wildcard_token, scoped_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    path = f"/api/v2/tags/{scoped_tags['global'].id}?application_id={APP}"
    assert client.get(path).status_code == 404
    assert client.get(f"{path}&include_global=true").status_code == 200


def test_v1_detail_cannot_see_merchant_row(api_client, scoped_tags):
    response = api_client.get(f"/api/v1/tags/{scoped_tags['merchant_a'].id}")
    assert response.status_code == 404


def test_e2e_v1_global_only_v2_merchant_own_plus_opt_in_global(
    api_client, wildcard_token, scoped_tags
):
    v1_ids = {item["id"] for item in api_client.get("/api/v1/tags").json()["data"]}
    assert v1_ids == {str(scoped_tags["global"].id)}

    merchant = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    own, _ = list_ids(merchant)
    assert own == [str(scoped_tags["merchant_a"].id)]
    merged, _ = list_ids(merchant, "&include_global=true")
    assert set(merged) == {str(scoped_tags["merchant_a"].id), str(scoped_tags["global"].id)}


# --- usage_count side-channel -------------------------------------------------


def test_usage_count_is_namespace_scoped_in_v2(api_client, wildcard_token):
    tag = make_tag(application_id=APP, slug="premium", name="Premium")
    for scope, count in (
        ((None, None), 1),
        (("merchant", "merchant_a"), 2),
        (("merchant", "merchant_b"), 3),
    ):
        for i in range(count):
            TagAssignment.objects.create(
                tenant_id="tenant_a",
                application_id=APP,
                tag=tag,
                resource_type="product",
                resource_id=f"{scope[1] or 'global'}-{i}",
                namespace_type=scope[0],
                namespace_id=scope[1],
            )

    v1 = api_client.get(f"/api/v1/tags/{tag.id}").json()["data"]
    assert v1["usage_count"] == 6  # legacy tenant-wide count

    merchant = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    v2 = merchant.get(f"/api/v2/tags/{tag.id}?application_id={APP}&include_global=true").json()[
        "data"
    ]
    assert v2["usage_count"] == 3  # merchant_a (2) + global (1), excludes merchant_b


def test_resource_tag_usage_count_is_fail_closed_for_exact_grant(merchant_token):
    # An exact merchant grant is not authorized for global. It can see a global
    # tag it assigned inside its namespace (via resource-tags), but the tag's
    # usage_count must not include global-namespace assignments it cannot opt into.
    tag = make_tag(application_id=APP, slug="globalassigned", name="Global Assigned")
    for resource_id, ns_type, ns_id in (("p1", "merchant", "merchant_a"), ("p2", None, None)):
        TagAssignment.objects.create(
            tenant_id="tenant_a",
            application_id=APP,
            tag=tag,
            resource_type="product",
            resource_id=resource_id,
            namespace_type=ns_type,
            namespace_id=ns_id,
        )

    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    data = client.get(f"/api/v2/resources/product/p1/tags?application_id={APP}").json()["data"]
    assert len(data) == 1
    assert data[0]["tag"]["usage_count"] == 1  # merchant_a only; excludes the global assignment


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_null_body_application_id_on_namespaced_patch_is_rejected(merchant_token):
    # The create guard also applies to PATCH: an explicit null application_id on a
    # namespaced update is a 400, not a database/service conflict.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    ns = {"namespace_type": "merchant", "namespace_id": "merchant_a"}
    tag = make_tag(application_id=APP, slug="patchtag", **ns)
    vocab = make_vocabulary(application_id=APP, slug="patchvocab", **ns)
    alias = make_alias(tag=tag, application_id=APP, slug="patchalias", **ns)

    for path in (
        f"/api/v2/tags/{tag.id}",
        f"/api/v2/vocabularies/{vocab.id}",
        f"/api/v2/tag-aliases/{alias.id}",
    ):
        response = client.patch(
            f"{path}?application_id={APP}", {"application_id": None}, format="json"
        )
        assert response.status_code == 400, (path, response.data)
        assert response.json()["error"]["code"] == "validation_error", path


def test_v2_usage_count_stays_within_requested_application(wildcard_token):
    # The namespace layer is below application: a shared tag viewed as
    # application=commerce must not count assignments from another application that
    # merely shares the namespace id (cms/merchant_a).
    shared = make_tag(slug="sharedcount", name="Shared Count")

    def assign(application_id, namespace_type, namespace_id, resource_id):
        TagAssignment.objects.create(
            tenant_id="tenant_a",
            application_id=application_id,
            tag=shared,
            resource_type="product",
            resource_id=resource_id,
            namespace_type=namespace_type,
            namespace_id=namespace_id,
        )

    assign(APP, "merchant", "merchant_a", "a1")
    assign(APP, "merchant", "merchant_a", "a2")
    assign("cms", "merchant", "merchant_a", "b1")  # cross-application, same namespace id
    assign(APP, None, None, "g1")  # commerce global-namespace
    assign("cms", None, None, "g2")  # cms global-namespace

    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    data = client.get(f"/api/v2/tags/{shared.id}?application_id={APP}&include_global=true").json()[
        "data"
    ]
    # commerce merchant_a (2) + commerce global (1); excludes both cms assignments.
    assert data["usage_count"] == 3


# --- writes: gated off by default, scoped when enabled ------------------------


def test_namespaced_write_is_gated_off_by_default(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Private", "slug": "private", "type": "label"},
        format="json",
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "namespaced_writes_disabled"


def test_global_write_is_allowed_while_namespaced_writes_are_off(api_client):
    response = api_client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Shared", "slug": "shared", "type": "label"},
        format="json",
    )
    assert response.status_code == 201
    created = Tag.objects.get(id=response.json()["data"]["id"])
    assert created.namespace_type is None and created.namespace_id is None


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_write_targets_request_scope_when_enabled(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Private", "slug": "private", "type": "label"},
        format="json",
    )
    assert response.status_code == 201
    created = Tag.objects.get(id=response.json()["data"]["id"])
    assert (created.namespace_type, created.namespace_id) == ("merchant", "merchant_a")


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_write_cannot_mutate_global_row(wildcard_token, scoped_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    # Namespaced requests must name their application for authorization.
    response = client.delete(f"/api/v2/tags/{scoped_tags['global'].id}?application_id={APP}")
    assert response.status_code == 404
    assert Tag.objects.get(id=scoped_tags["global"].id).is_active


def test_global_form_encoded_create_is_accepted(api_client):
    # create_payload_with_scope must preserve a form/multipart QueryDict body
    # instead of flattening each field into a list ("Not a valid string").
    response = api_client.post(
        "/api/v1/tags",
        {"name": "Featured", "slug": "featured", "type": "label"},
        format="multipart",
    )
    assert response.status_code == 201, response.data
    assert response.json()["data"]["slug"] == "featured"


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_form_encoded_create_uses_query_application_id(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    for path, body, model, slug in (
        ("/api/v2/tags", {"name": "F", "slug": "formtag", "type": "label"}, Tag, "formtag"),
        ("/api/v2/vocabularies", {"name": "V", "slug": "formvocab"}, Vocabulary, "formvocab"),
    ):
        response = client.post(f"{path}?application_id={APP}", body, format="multipart")
        assert response.status_code == 201, response.data
        assert model.objects.get(slug=slug).application_id == APP


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_null_body_application_id_on_namespaced_create_is_rejected(tenant_wildcard_token):
    # Explicit null is valid for a global row but not a namespaced one; it must be
    # a 400 rather than the namespace check surfacing a misleading 409.
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")

    tag = client.post(
        f"/api/v2/tags?application_id={APP}",
        {"application_id": None, "name": "X", "slug": "xnull", "type": "label"},
        format="json",
    )
    assert tag.status_code == 400, tag.data
    assert tag.json()["error"]["code"] == "validation_error"
    assert not Tag.objects.filter(slug="xnull").exists()

    vocab = client.post(
        f"/api/v2/vocabularies?application_id={APP}",
        {"application_id": None, "name": "V", "slug": "vnull"},
        format="json",
    )
    assert vocab.status_code == 400
    assert not Vocabulary.objects.filter(slug="vnull").exists()

    canonical = make_tag(
        application_id=APP, namespace_type="merchant", namespace_id="merchant_a", slug="canon3"
    )
    alias = client.post(
        f"/api/v2/tag-aliases?application_id={APP}",
        {"application_id": None, "tag_id": str(canonical.id), "name": "A", "slug": "anull"},
        format="json",
    )
    assert alias.status_code == 400
    assert not TagAlias.objects.filter(slug="anull").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_create_uses_query_application_id_when_body_omits_it(merchant_token):
    # application_id only in the query string (authorization accepts it there). The
    # create must persist it so the namespaced row is valid, instead of writing a
    # NULL application and failing the namespace check as a misleading 409.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.post(
        f"/api/v2/tags?application_id={APP}",
        {"name": "Private", "slug": "private", "type": "label"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.json()["data"]["application_id"] == APP
    created = Tag.objects.get(id=response.json()["data"]["id"])
    assert (created.namespace_type, created.namespace_id) == ("merchant", "merchant_a")


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_blank_query_application_id_on_namespaced_create_is_rejected(tenant_wildcard_token):
    # A tenant-wide wildcard grant authorizes any request application_id, so the
    # query fallback must still pass the serializer's blank/whitespace validation
    # rather than persisting a blank application id on the namespaced row.
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.post(
        "/api/v2/tags?application_id=%20%20%20",
        {"name": "X", "slug": "x", "type": "label"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert not Tag.objects.filter(slug="x").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_overlong_query_application_id_on_namespaced_create_is_rejected(tenant_wildcard_token):
    # An over-100-char query application_id must be rejected as a structured 400
    # (serializer max_length) rather than reaching a varchar(100) insert as a 500.
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.post(
        f"/api/v2/tags?application_id={'c' * 101}",
        {"name": "X", "slug": "x", "type": "label"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert not Tag.objects.filter(slug="x").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_blank_body_application_id_is_not_overwritten_by_query_fallback(tenant_wildcard_token):
    # The query fallback applies only when the body omits application_id. An
    # explicit blank body value must still be rejected by serializer validation,
    # not silently replaced by the query value.
    client = client_for(tenant_wildcard_token, namespace_type="merchant", namespace_id="merchant_a")

    tag = client.post(
        f"/api/v2/tags?application_id={APP}",
        {"application_id": "", "name": "X", "slug": "x", "type": "label"},
        format="json",
    )
    assert tag.status_code == 400, tag.data
    assert tag.json()["error"]["code"] == "validation_error"
    assert not Tag.objects.filter(slug="x").exists()

    vocab = client.post(
        f"/api/v2/vocabularies?application_id={APP}",
        {"application_id": "", "name": "V", "slug": "v"},
        format="json",
    )
    assert vocab.status_code == 400
    assert not Vocabulary.objects.filter(slug="v").exists()

    canonical = make_tag(
        application_id=APP, namespace_type="merchant", namespace_id="merchant_a", slug="canon2"
    )
    alias = client.post(
        f"/api/v2/tag-aliases?application_id={APP}",
        {"application_id": "", "tag_id": str(canonical.id), "name": "A", "slug": "a2"},
        format="json",
    )
    assert alias.status_code == 400
    assert not TagAlias.objects.filter(slug="a2").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_vocabulary_and_alias_creates_use_query_application_id(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    vocab = client.post(
        f"/api/v2/vocabularies?application_id={APP}",
        {"name": "Labels", "slug": "labels"},
        format="json",
    )
    assert vocab.status_code == 201, vocab.data
    assert vocab.json()["data"]["application_id"] == APP

    canonical = make_tag(
        application_id=APP, namespace_type="merchant", namespace_id="merchant_a", slug="canon"
    )
    alias = client.post(
        f"/api/v2/tag-aliases?application_id={APP}",
        {"tag_id": str(canonical.id), "name": "Alias", "slug": "aliasslug"},
        format="json",
    )
    assert alias.status_code == 201, alias.data
    assert alias.json()["data"]["application_id"] == APP


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_exact_merchant_write_references_cannot_target_global_rows(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    global_tag = make_tag(application_id=APP, slug="global-write-ref")
    global_alias = make_alias(tag=global_tag, application_id=APP, slug="global-write-alias")
    global_vocabulary = make_vocabulary(application_id=APP, slug="global-write-vocab")

    requests = (
        (
            "/api/v2/tags",
            {
                "application_id": APP,
                "name": "Child",
                "slug": "blocked-parent",
                "type": "label",
                "parent_id": str(global_tag.id),
            },
        ),
        (
            "/api/v2/tags",
            {
                "application_id": APP,
                "name": "Vocab Child",
                "slug": "blocked-vocab",
                "type": "label",
                "vocabulary_id": str(global_vocabulary.id),
            },
        ),
        (
            "/api/v2/tag-aliases",
            {
                "application_id": APP,
                "name": "Blocked Alias",
                "slug": "blocked-alias",
                "tag_id": str(global_tag.id),
            },
        ),
        (
            "/api/v2/tag-assignments",
            {
                "application_id": APP,
                "tag_id": str(global_tag.id),
                "resource_type": "product",
                "resource_id": "blocked-single",
            },
        ),
        (
            "/api/v2/tag-assignments",
            {
                "application_id": APP,
                "alias_id": str(global_alias.id),
                "resource_type": "product",
                "resource_id": "blocked-alias-id",
            },
        ),
        (
            "/api/v2/tag-assignments",
            {
                "application_id": APP,
                "alias_slug": global_alias.slug,
                "resource_type": "product",
                "resource_id": "blocked-alias-slug",
            },
        ),
        (
            "/api/v2/tag-assignments/bulk-assign",
            {
                "application_id": APP,
                "tag_ids": [str(global_tag.id)],
                "resource_type": "product",
                "resource_id": "blocked-bulk",
            },
        ),
        (
            "/api/v2/tag-assignments/bulk-assign",
            {
                "application_id": APP,
                "alias_slugs": [global_alias.slug],
                "resource_type": "product",
                "resource_id": "blocked-bulk-alias",
            },
        ),
        (
            "/api/v2/resources/product/blocked-replace/tags",
            {"application_id": APP, "tag_ids": [str(global_tag.id)]},
        ),
    )

    for path, payload in requests:
        response = client.post(path, payload, format="json")
        assert response.status_code == 400, (path, response.data)

    assert not TagAssignment.objects.filter(
        tenant_id="tenant_a", namespace_type="merchant", namespace_id="merchant_a"
    ).exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_wildcard_grant_can_reference_global_tag_on_namespaced_write(wildcard_token):
    global_tag = make_tag(application_id=APP, slug="authorized-global-write-ref")
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")

    response = client.post(
        "/api/v2/tag-assignments",
        {
            "application_id": APP,
            "tag_id": str(global_tag.id),
            "resource_type": "product",
            "resource_id": "allowed-single",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.json()["data"]["namespace_type"] == "merchant"
    assert response.json()["data"]["namespace_id"] == "merchant_a"


def test_v2_responses_expose_namespace_identity_without_changing_v1(api_client, merchant_token):
    namespace = {"namespace_type": "merchant", "namespace_id": "merchant_a"}
    tag = make_tag(application_id=APP, slug="response-tag", **namespace)
    alias = make_alias(tag=tag, application_id=APP, slug="response-alias", **namespace)
    make_vocabulary(application_id=APP, slug="response-vocab", **namespace)
    assignment = TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        tag=tag,
        resource_type="product",
        resource_id="response-resource",
        **namespace,
    )
    AuditLog.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        action="test.response",
        entity_type="tag",
        entity_id=str(tag.id),
        tag_id=tag.id,
        **namespace,
    )
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    responses = (
        client.get(f"/api/v2/tags/{tag.id}?application_id={APP}").json()["data"],
        client.get(f"/api/v2/tag-aliases/{alias.id}?application_id={APP}").json()["data"],
        client.get(f"/api/v2/vocabularies?application_id={APP}").json()["data"][0],
        client.get(
            f"/api/v2/resources/product/{assignment.resource_id}/tags?application_id={APP}"
        ).json()["data"][0],
        client.get(f"/api/v2/tags/{tag.id}/resources?application_id={APP}").json()["data"][0],
        client.get(f"/api/v2/audit-logs?application_id={APP}").json()["data"][0],
    )
    for data in responses:
        assert data["namespace_type"] == "merchant"
        assert data["namespace_id"] == "merchant_a"

    nested_tag = responses[3]["tag"]
    assert nested_tag["namespace_type"] == "merchant"
    assert nested_tag["namespace_id"] == "merchant_a"

    global_tag = make_tag(application_id=APP, slug="v1-response-shape")
    v1_data = api_client.get(f"/api/v1/tags/{global_tag.id}").json()["data"]
    assert "namespace_type" not in v1_data
    assert "namespace_id" not in v1_data


# --- tag-resolution honours the fail-closed global contract -------------------


@pytest.fixture
def resolution_tags(db):
    return {
        "global": make_tag(application_id=APP, slug="globaldeal", name="Global Deal"),
        "merchant_a": make_tag(
            application_id=APP,
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="merchantdeal",
            name="Merchant Deal",
        ),
    }


def resolve(client, slug, query=""):
    return client.get(f"/api/v2/tag-resolution?slug={slug}&application_id={APP}{query}")


def test_resolution_exact_merchant_cannot_discover_global_by_default(
    merchant_token, resolution_tags
):
    # Regression: an exact merchant grant must not reach global tags through
    # tag-resolution's default global fallback.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    assert resolve(client, "globaldeal").status_code == 400


def test_resolution_exact_merchant_scope_global_is_fail_closed(merchant_token, resolution_tags):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    # scope=global must not force global visibility for an unauthorized grant.
    assert resolve(client, "globaldeal", "&scope=global").status_code == 400


def test_resolution_exact_merchant_resolves_own_scope(merchant_token, resolution_tags):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = resolve(client, "merchantdeal")
    assert response.status_code == 200
    assert response.json()["data"]["tag"]["id"] == str(resolution_tags["merchant_a"].id)


def test_resolution_global_visible_with_authorized_opt_in(wildcard_token, resolution_tags):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    response = resolve(client, "globaldeal", "&include_global=true")
    assert response.status_code == 200
    assert response.json()["data"]["tag"]["id"] == str(resolution_tags["global"].id)


def test_resolution_scope_global_authorizes_wildcard_without_include_global(
    wildcard_token, resolution_tags
):
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")
    response = resolve(client, "globaldeal", "&scope=global")
    assert response.status_code == 200, response.data
    assert response.json()["data"]["tag"]["id"] == str(resolution_tags["global"].id)


# --- detail lookups stay within the authorized application -------------------


@pytest.fixture
def cross_app_rows(db):
    # Same namespace_id (merchant_a) but a different application (cms) than the
    # commerce/merchant_a grant. Namespace sits below application, so these are a
    # distinct scope and must be unreachable by that grant.
    cms_tag = make_tag(
        application_id="cms", namespace_type="merchant", namespace_id="merchant_a", slug="cmsonly"
    )
    return {
        "cms_tag": cms_tag,
        "own_tag": make_tag(
            application_id=APP,
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="ownonly",
        ),
        "cms_alias": make_alias(
            tag=cms_tag,
            application_id="cms",
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="cmsalias",
        ),
        "cms_vocab": make_vocabulary(
            application_id="cms",
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="cmsvocab",
        ),
    }


def test_detail_lookups_do_not_cross_application_within_namespace(merchant_token, cross_app_rows):
    # Regression: a commerce/merchant_a grant must not reach a cms/merchant_a row
    # that merely shares the namespace id.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    for path in (
        f"/api/v2/tags/{cross_app_rows['cms_tag'].id}",
        f"/api/v2/tag-aliases/{cross_app_rows['cms_alias'].id}",
        f"/api/v2/vocabularies/{cross_app_rows['cms_vocab'].id}",
        f"/api/v2/tags/{cross_app_rows['cms_tag'].id}/aliases",
        f"/api/v2/tags/{cross_app_rows['cms_tag'].id}/resources",
    ):
        assert client.get(f"{path}?application_id={APP}").status_code == 404, path


def test_detail_lookup_resolves_own_application_row(merchant_token, cross_app_rows):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.get(f"/api/v2/tags/{cross_app_rows['own_tag'].id}?application_id={APP}")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(cross_app_rows["own_tag"].id)


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_write_cannot_reach_cross_application_row(merchant_token, cross_app_rows):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.delete(f"/api/v2/tags/{cross_app_rows['cms_tag'].id}?application_id={APP}")
    assert response.status_code == 404
    assert Tag.objects.get(id=cross_app_rows["cms_tag"].id).is_active


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_overlong_namespace_header_returns_structured_400_not_500(wildcard_token):
    # A 101-char namespace id would overflow the varchar(100) column on write;
    # it must be rejected as a structured 400 before persistence, never a 500.
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="m" * 101)
    response = client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "X", "slug": "x", "type": "label"},
        format="json",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "namespace_invalid"
    assert {"code", "message", "details", "request_id"} <= set(error)


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_patch_with_body_application_id_cannot_reach_cross_application_row(
    merchant_token, cross_app_rows
):
    # Regression: application_id lives only in the JSON body, not the query
    # string. Authorization reads the body, so the object lookup must too, or a
    # commerce/merchant_a grant can PATCH a cms/merchant_a row by id.
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")
    response = client.patch(
        f"/api/v2/tags/{cross_app_rows['cms_tag'].id}",
        {"application_id": APP, "name": "Hijacked"},
        format="json",
    )
    assert response.status_code == 404
    assert Tag.objects.get(id=cross_app_rows["cms_tag"].id).name != "Hijacked"
