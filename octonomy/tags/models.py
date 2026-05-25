from __future__ import annotations

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q


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


class Vocabulary(models.Model):
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
        ordering = ["name", "slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                condition=Q(application_id__isnull=True, is_active=True),
                name="uniq_active_shared_vocab_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "slug"],
                condition=Q(application_id__isnull=False, is_active=True),
                name="uniq_active_app_vocab_slug",
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
        ]

    def __str__(self) -> str:
        scope = self.application_id or "shared"
        return f"{self.tenant_id}/{scope}/{self.slug}"


class Tag(models.Model):
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
        ordering = ["name", "slug"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(parent_id=models.F("id")),
                name="tag_parent_cannot_be_self",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "type", "slug"],
                condition=Q(application_id__isnull=True, is_active=True),
                name="uniq_active_shared_tag_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "type", "slug"],
                condition=Q(application_id__isnull=False, is_active=True),
                name="uniq_active_app_tag_slug",
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


class TagAlias(models.Model):
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
        ordering = ["name", "slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "slug"],
                condition=Q(application_id__isnull=True, is_active=True),
                name="uniq_active_shared_alias_slug",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "slug"],
                condition=Q(application_id__isnull=False, is_active=True),
                name="uniq_active_app_alias_slug",
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
            GinIndex(fields=["metadata"], name="alias_metadata_gin_idx"),
        ]

    def __str__(self) -> str:
        scope = self.application_id or "shared"
        return f"{self.tenant_id}/{scope}/{self.slug}->{self.tag_id}"
