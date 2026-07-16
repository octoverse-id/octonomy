"""Registry-driven cross-namespace isolation sweep (issue #44).

The sweep walks the versioned URL registry, and for every GET (read) endpoint it
requires a fixture spec here describing how to seed a ``merchant_a`` row (plus a
global row) and how a ``merchant_b`` caller reaches the same surface. The runner
then asserts none of ``merchant_a``'s row identifiers (uuid, slug, name) ever
appear in ``merchant_b``'s response, and — as a non-vacuous guard — that
``merchant_a`` *can* see its own row.

Adding a new v2 read endpoint without a spec fails
``test_every_v2_read_endpoint_has_a_fixture_spec`` loudly, which is the
"unmapped endpoint breaks CI" contract from #44. A versioned route the walk
cannot classify (unnamed, or a non-DRF callback with no ``.cls``) fails
``test_no_unclassifiable_versioned_routes`` — such a route could be a read
endpoint that silently escapes the sweep. That guard walks the live URL
resolver, so it cannot be satisfied by editing a hand-maintained list.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from tests.factories import make_alias, make_tag, make_vocabulary

APP = "commerce"
NS_A = {"namespace_type": "merchant", "namespace_id": "merchant_a"}
NS_B = {"namespace_type": "merchant", "namespace_id": "merchant_b"}

# The URL prefix that carries the version segment. Every namespaced API route
# resolves beneath it; unversioned routes (health, schema) do not.
_VERSIONED_PREFIX = "api/<version>/"


# --- URL-registry walk --------------------------------------------------------


def _walk(patterns, prefix: str = "") -> Iterator[tuple[str, URLPattern]]:
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _walk(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry


def _versioned_leaves() -> Iterator[tuple[str, URLPattern]]:
    for route, pattern in _walk(get_resolver().url_patterns):
        if _VERSIONED_PREFIX in route:
            yield route, pattern


def v2_read_endpoint_names() -> set[str]:
    """Names of every versioned endpoint whose view allows GET.

    Derived from the live resolver (not a static list) so a newly registered
    read endpoint is picked up automatically and must be given a fixture spec.
    """

    names: set[str] = set()
    for _route, pattern in _versioned_leaves():
        if not pattern.name:
            continue
        view_cls = getattr(pattern.callback, "cls", None)
        methods = getattr(view_cls, "http_method_names", ())
        if "get" in methods:
            names.add(pattern.name)
    return names


def unclassifiable_versioned_routes() -> list[str]:
    """Versioned leaf routes the sweep cannot classify.

    A route with no ``name`` (nothing to key a spec to) or whose callback has no
    DRF ``.cls`` (no readable ``http_method_names``) might be a read endpoint
    that escapes the read-name set entirely. Surfacing them lets the guard fail
    loudly instead of silently skipping.
    """

    unclassifiable: list[str] = []
    for route, pattern in _versioned_leaves():
        view_cls = getattr(pattern.callback, "cls", None)
        methods = getattr(view_cls, "http_method_names", None)
        if not pattern.name or methods is None:
            unclassifiable.append(route)
    return unclassifiable


def duplicate_versioned_route_names() -> list[str]:
    """Route names that map to more than one versioned path.

    The read-name set is a ``set``, so a duplicated name would collapse two paths
    into one spec requirement and let one of them go unswept.
    """

    seen: dict[str, int] = {}
    for _route, pattern in _versioned_leaves():
        if pattern.name:
            seen[pattern.name] = seen.get(pattern.name, 0) + 1
    return sorted(name for name, count in seen.items() if count > 1)


# --- scenario model -----------------------------------------------------------


@dataclass
class Scenario:
    """A seeded isolation scenario for one read endpoint.

    ``path`` is the request path (query string allowed) that both merchants call;
    ``application_id`` is appended by the runner. ``forbidden`` are
    ``merchant_a``-owned tokens (uuid, slug, name) that must never surface to
    ``merchant_b``. ``positive_id`` is the ``merchant_a`` identifier that must
    appear when ``merchant_a`` itself calls ``path`` — the guard against a
    vacuously green sweep (an endpoint that 404s for everyone would otherwise
    "pass").
    """

    path: str
    forbidden: set[str]
    positive_id: str
    b_status: tuple[int, ...] = (200, 400, 404)


def _tokens(*rows) -> set[str]:
    """Identifying tokens for merchant_a rows: uuid plus slug/name when present.

    Aggregate rows (assignments, audit logs) have no slug/name; they contribute
    just their uuid.
    """

    out: set[str] = set()
    for row in rows:
        out.add(str(row.id))
        for attr in ("slug", "name"):
            value = getattr(row, attr, None)
            if value:
                out.add(value)
    return out


def _audit(*, namespace: dict, tag=None, resource_type=None, resource_id=None, action="test.event"):
    return AuditLog.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        action=action,
        entity_type="tag" if tag is not None else "resource",
        entity_id=str(tag.id) if tag is not None else (resource_id or "res"),
        tag_id=tag.id if tag is not None else None,
        resource_type=resource_type,
        resource_id=resource_id,
        **namespace,
    )


def _assignment(*, tag, resource_id, resource_type="product", namespace: dict):
    return TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        tag=tag,
        resource_type=resource_type,
        resource_id=resource_id,
        **namespace,
    )


# --- per-endpoint fixture specs -----------------------------------------------
#
# Each seed function creates merchant_a + global (+ a merchant_b decoy for list
# endpoints) rows and returns the Scenario the runner drives. Slugs differ per
# scope only for readability; the scope columns are what make them distinct rows.


def _seed_tags_collection() -> Scenario:
    a = make_tag(application_id=APP, slug="a-tag", **NS_A)
    make_tag(application_id=APP, slug="global-tag")
    make_tag(application_id=APP, slug="b-tag", **NS_B)
    return Scenario("/api/v2/tags", _tokens(a), str(a.id))


def _seed_tag_detail() -> Scenario:
    a = make_tag(application_id=APP, slug="a-detail", **NS_A)
    make_tag(application_id=APP, slug="global-detail")
    return Scenario(f"/api/v2/tags/{a.id}", _tokens(a), str(a.id), b_status=(404,))


def _seed_tag_aliases() -> Scenario:
    a = make_tag(application_id=APP, slug="a-aliasparent", **NS_A)
    alias = make_alias(tag=a, application_id=APP, slug="a-alias", **NS_A)
    make_tag(application_id=APP, slug="global-aliasparent")
    return Scenario(
        f"/api/v2/tags/{a.id}/aliases", _tokens(a, alias), str(alias.id), b_status=(404,)
    )


def _seed_tag_resources() -> Scenario:
    a = make_tag(application_id=APP, slug="a-resparent", **NS_A)
    resource_id = "a-tag-resource"
    assignment = _assignment(tag=a, resource_id=resource_id, namespace=NS_A)
    make_tag(application_id=APP, slug="global-resparent")
    return Scenario(
        f"/api/v2/tags/{a.id}/resources",
        _tokens(a, assignment) | {resource_id},
        resource_id,
        b_status=(404,),
    )


def _seed_tag_audit_logs() -> Scenario:
    a = make_tag(application_id=APP, slug="a-auditparent", **NS_A)
    log = _audit(namespace=NS_A, tag=a, action="tag.updated")
    make_tag(application_id=APP, slug="global-auditparent")
    # No parent-tag 404 here: the view filters logs by tag_id within scope, so a
    # merchant_b caller gets an empty 200 rather than a not-found.
    return Scenario(
        f"/api/v2/tags/{a.id}/audit-logs", _tokens(a, log), str(log.id), b_status=(200,)
    )


def _seed_vocabularies_collection() -> Scenario:
    a = make_vocabulary(application_id=APP, slug="a-vocab", **NS_A)
    make_vocabulary(application_id=APP, slug="global-vocab")
    make_vocabulary(application_id=APP, slug="b-vocab", **NS_B)
    return Scenario("/api/v2/vocabularies", _tokens(a), str(a.id))


def _seed_vocabulary_detail() -> Scenario:
    a = make_vocabulary(application_id=APP, slug="a-vocabdetail", **NS_A)
    make_vocabulary(application_id=APP, slug="global-vocabdetail")
    return Scenario(f"/api/v2/vocabularies/{a.id}", _tokens(a), str(a.id), b_status=(404,))


def _seed_tag_aliases_collection() -> Scenario:
    a_tag = make_tag(application_id=APP, slug="a-aliaslisttag", **NS_A)
    a = make_alias(tag=a_tag, application_id=APP, slug="a-listalias", **NS_A)
    b_tag = make_tag(application_id=APP, slug="b-aliaslisttag", **NS_B)
    make_alias(tag=b_tag, application_id=APP, slug="b-listalias", **NS_B)
    return Scenario("/api/v2/tag-aliases", _tokens(a), str(a.id))


def _seed_tag_alias_detail() -> Scenario:
    a_tag = make_tag(application_id=APP, slug="a-aliasdetailtag", **NS_A)
    a = make_alias(tag=a_tag, application_id=APP, slug="a-detailalias", **NS_A)
    return Scenario(f"/api/v2/tag-aliases/{a.id}", _tokens(a), str(a.id), b_status=(404,))


def _seed_tag_resolution() -> Scenario:
    a = make_tag(application_id=APP, slug="a-resolveslug", name="A Resolve Target", **NS_A)
    make_tag(application_id=APP, slug="global-resolveslug")
    # merchant_b resolving a slug that exists only in merchant_a's scope must not
    # discover it: a 404/400, never merchant_a's tag. The slug is in the request
    # URL, so the forbidden token is the tag's uuid + name, which the response
    # body must never echo.
    return Scenario(
        "/api/v2/tag-resolution?slug=a-resolveslug",
        {str(a.id), "A Resolve Target"},
        str(a.id),
        b_status=(400, 404),
    )


def _seed_audit_logs() -> Scenario:
    log = _audit(namespace=NS_A, action="tag.created")
    _audit(namespace={"namespace_type": None, "namespace_id": None}, action="tag.created")
    return Scenario("/api/v2/audit-logs", {str(log.id)}, str(log.id))


def _seed_resource_tags() -> Scenario:
    a_tag = make_tag(application_id=APP, slug="a-restagstag", **NS_A)
    resource_id = "shared-resource-rt"
    assignment = _assignment(tag=a_tag, resource_id=resource_id, namespace=NS_A)
    # merchant_b queries the *same* external resource id but must see none of
    # merchant_a's assignments on it.
    return Scenario(
        f"/api/v2/resources/product/{resource_id}/tags",
        _tokens(a_tag, assignment),
        str(a_tag.id),
        b_status=(200,),
    )


def _seed_resource_audit_logs() -> Scenario:
    resource_id = "shared-resource-ral"
    log = _audit(namespace=NS_A, resource_type="product", resource_id=resource_id)
    return Scenario(
        f"/api/v2/resources/product/{resource_id}/audit-logs",
        {str(log.id)},
        str(log.id),
        b_status=(200,),
    )


# name (URL registry) -> seed function. The keys must exactly equal
# v2_read_endpoint_names(); the guard test enforces that.
FIXTURE_SPECS = {
    "tags-collection": _seed_tags_collection,
    "tag-detail": _seed_tag_detail,
    "tag-aliases": _seed_tag_aliases,
    "tag-resources": _seed_tag_resources,
    "tag-audit-logs": _seed_tag_audit_logs,
    "vocabularies-collection": _seed_vocabularies_collection,
    "vocabulary-detail": _seed_vocabulary_detail,
    "tag-aliases-collection": _seed_tag_aliases_collection,
    "tag-alias-detail": _seed_tag_alias_detail,
    "tag-resolution": _seed_tag_resolution,
    "audit-logs": _seed_audit_logs,
    "resource-tags": _seed_resource_tags,
    "resource-audit-logs": _seed_resource_audit_logs,
}


def collect_strings(value) -> set[str]:
    """Every string reachable in a decoded JSON body, keys included.

    A leaked row surfaces its uuid/slug/name somewhere in the payload — nested
    under ``data``, ``tag``, ``pagination``, or even as a dict *key* (an
    id-keyed map) — so flattening every string (values and keys) and checking
    the forbidden token set against it catches leaks regardless of an endpoint's
    response shape.
    """

    found: set[str] = set()

    def _visit(node) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if isinstance(key, str):
                    found.add(key)
                _visit(item)
        elif isinstance(node, list | tuple):
            for item in node:
                _visit(item)
        elif isinstance(node, str):
            found.add(node)

    _visit(value)
    return found
