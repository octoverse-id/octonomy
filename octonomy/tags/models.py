from __future__ import annotations

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q

from octonomy.core.models import NamespaceScopedModel, namespace_scope_constraint


class TagQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)

    def active(self):
        return self.filter(is_active=True)


class VocabularyQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)

    def active(self):
        return self.filter(is_active=True)


class Vocabulary(NamespaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VocabularyQuerySet.as_manager()

    class Meta:
        db_table = "vocabularies"
        ordering = ["name", "slug", "id"]
        constraints = [
            namespace_scope_constraint(),
            # Shared records store application_id as NULL, so they need a separate
            # active-only uniqueness rule from app-scoped records. Relying on a
            # single nullable column in a unique constraint would permit duplicate
            # shared slugs under PostgreSQL NULL semantics.
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                condition=Q(
                    application_id__isnull=True,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_shared_global_vocab_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "slug"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_app_global_vocab_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "namespace_type", "namespace_id", "slug"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=False,
                    namespace_id__isnull=False,
                    is_active=True,
                ),
                name="uniq_app_ns_vocab_slug",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id", "slug"],
                name="vocab_tenant_app_slug_idx",
            ),
            models.Index(
                fields=["tenant_id", "application_id", "is_active"],
                name="vocab_tenant_app_active_idx",
            ),
            models.Index(
                fields=["tenant_id", "application_id", "namespace_type", "namespace_id", "slug"],
                name="vocab_scope_slug_idx",
            ),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "is_active",
                ],
                name="vocab_scope_active_idx",
            ),
        ]

    def __str__(self) -> str:
        scope = self.application_id or "shared"
        return f"{self.tenant_id}/{scope}/{self.slug}"


class Tag(NamespaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.RESTRICT,
    )
    vocabulary = models.ForeignKey(
        Vocabulary,
        null=True,
        blank=True,
        related_name="tags",
        on_delete=models.RESTRICT,
    )
    metadata = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TagQuerySet.as_manager()

    class Meta:
        db_table = "tags"
        ordering = ["name", "slug", "id"]
        constraints = [
            namespace_scope_constraint(),
            models.CheckConstraint(
                condition=~Q(parent_id=models.F("id")),
                name="tag_parent_cannot_be_self",
            ),
            # Shared tags are tenant-wide because application_id is NULL. Keep the
            # active uniqueness rules split so app-specific tags can reuse a slug
            # without weakening the one-canonical-shared-tag invariant.
            models.UniqueConstraint(
                fields=["tenant_id", "type", "slug"],
                condition=Q(
                    application_id__isnull=True,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_shared_global_tag_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "type", "slug"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_app_global_tag_slug",
            ),
            models.UniqueConstraint(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "type",
                    "slug",
                ],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=False,
                    namespace_id__isnull=False,
                    is_active=True,
                ),
                name="uniq_app_ns_tag_slug",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id", "type", "slug"],
                name="tags_tenant__e1fc6f_idx",
            ),
            models.Index(fields=["tenant_id", "type", "slug"], name="tag_shared_lookup_idx"),
            models.Index(
                fields=["tenant_id", "application_id", "is_active"],
                name="tags_tenant__7959d5_idx",
            ),
            models.Index(
                fields=["tenant_id", "type", "is_active"],
                name="tags_tenant__7a27de_idx",
            ),
            models.Index(fields=["tenant_id", "parent"], name="tags_tenant__5fdd65_idx"),
            models.Index(fields=["tenant_id", "vocabulary"], name="tags_tenant_vocab_idx"),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "type",
                    "slug",
                ],
                name="tag_scope_slug_idx",
            ),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "is_active",
                ],
                name="tag_scope_active_idx",
            ),
            GinIndex(fields=["metadata"], name="tag_metadata_gin_idx"),
        ]

    def __str__(self) -> str:
        scope = self.application_id or "shared"
        return f"{self.tenant_id}/{scope}/{self.type}/{self.slug}"


class TagAliasQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)

    def active(self):
        return self.filter(is_active=True)


class TagAlias(NamespaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    tag = models.ForeignKey(
        Tag,
        related_name="aliases",
        on_delete=models.RESTRICT,
    )
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TagAliasQuerySet.as_manager()

    class Meta:
        db_table = "tag_aliases"
        ordering = ["name", "slug", "id"]
        constraints = [
            namespace_scope_constraint(),
            # Aliases are alternate identifiers for canonical tags, and their
            # uniqueness follows the same shared-vs-application scope model as
            # tags. The constraints are active-only so deactivated aliases do not
            # permanently reserve a slug.
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                condition=Q(
                    application_id__isnull=True,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_shared_global_alias_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "slug"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    is_active=True,
                ),
                name="uniq_app_global_alias_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "namespace_type", "namespace_id", "slug"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=False,
                    namespace_id__isnull=False,
                    is_active=True,
                ),
                name="uniq_app_ns_alias_slug",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id", "slug"],
                name="alias_tenant_app_slug_idx",
            ),
            models.Index(
                fields=["tenant_id", "tag", "is_active"],
                name="alias_tenant_tag_active_idx",
            ),
            models.Index(fields=["tenant_id", "is_active"], name="alias_tenant_active_idx"),
            models.Index(
                fields=["tenant_id", "application_id", "namespace_type", "namespace_id", "slug"],
                name="alias_scope_slug_idx",
            ),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "is_active",
                ],
                name="alias_scope_active_idx",
            ),
            GinIndex(fields=["metadata"], name="alias_metadata_gin_idx"),
        ]

    def __str__(self) -> str:
        scope = self.application_id or "shared"
        return f"{self.tenant_id}/{scope}/{self.slug}->{self.tag_id}"
