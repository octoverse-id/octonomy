from __future__ import annotations

from octonomy.tags.models import Tag, TagAlias, Vocabulary


def make_vocabulary(
    *,
    tenant_id: str = "tenant_a",
    application_id: str | None = None,
    slug: str = "labels",
    name: str | None = None,
    is_active: bool = True,
) -> Vocabulary:
    return Vocabulary.objects.create(
        tenant_id=tenant_id,
        application_id=application_id,
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        metadata={},
        is_active=is_active,
    )


def make_tag(
    *,
    tenant_id: str = "tenant_a",
    application_id: str | None = None,
    slug: str = "featured",
    type: str = "label",
    name: str | None = None,
    is_active: bool = True,
    vocabulary: Vocabulary | None = None,
) -> Tag:
    return Tag.objects.create(
        tenant_id=tenant_id,
        application_id=application_id,
        slug=slug,
        type=type,
        name=name or slug.replace("-", " ").title(),
        vocabulary=vocabulary,
        metadata={},
        is_active=is_active,
    )


def make_alias(
    *,
    tag: Tag | None = None,
    tenant_id: str = "tenant_a",
    application_id: str | None = None,
    slug: str = "promoted",
    name: str | None = None,
    is_active: bool = True,
) -> TagAlias:
    return TagAlias.objects.create(
        tenant_id=tenant_id,
        application_id=application_id,
        tag=tag or make_tag(tenant_id=tenant_id, slug=f"{slug}-tag"),
        slug=slug,
        name=name or slug.replace("-", " ").title(),
        metadata={},
        is_active=is_active,
    )
