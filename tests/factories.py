from __future__ import annotations

from octonomy.tags.models import Tag


def make_tag(
    *,
    tenant_id: str = "tenant_a",
    application_id: str | None = None,
    slug: str = "featured",
    type: str = "label",
    name: str | None = None,
    is_active: bool = True,
) -> Tag:
    return Tag.objects.create(
        tenant_id=tenant_id,
        application_id=application_id,
        slug=slug,
        type=type,
        name=name or slug.replace("-", " ").title(),
        metadata={},
        is_active=is_active,
    )
