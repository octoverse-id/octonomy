from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

RESERVED_NAMESPACE_TYPE_GLOBAL = "global"


def namespace_scope_check() -> Q:
    return Q(namespace_type__isnull=True, namespace_id__isnull=True) | (
        Q(namespace_type__isnull=False, namespace_id__isnull=False, application_id__isnull=False)
        & ~Q(namespace_type="")
        & ~Q(namespace_id="")
        & ~Q(namespace_type=RESERVED_NAMESPACE_TYPE_GLOBAL)
    )


def namespace_scope_constraint() -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=namespace_scope_check(),
        name="%(app_label)s_%(class)s_namespace_scope",
    )


class NamespaceScopedModel(models.Model):
    namespace_type = models.CharField(max_length=100, null=True, blank=True)
    namespace_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        abstract = True
        constraints = [
            # Global rows are represented as NULL/NULL, not by a string sentinel.
            # Namespaced rows always sit below an application so tenant-shared
            # rows cannot accidentally become merchant-specific.
            namespace_scope_constraint(),
        ]

    def clean(self) -> None:
        super().clean()
        if self.namespace_type is None and self.namespace_id is None:
            return
        errors = {}
        if not self.namespace_type or not str(self.namespace_type).strip():
            errors["namespace_type"] = "This value cannot be blank."
        if self.namespace_type == RESERVED_NAMESPACE_TYPE_GLOBAL:
            errors["namespace_type"] = "The literal 'global' is reserved; omit namespace fields."
        if not self.namespace_id or not str(self.namespace_id).strip():
            errors["namespace_id"] = "This value cannot be blank."
        if getattr(self, "application_id", None) is None:
            errors["application_id"] = "Namespaced rows require an application_id."
        if errors:
            raise ValidationError(errors)
