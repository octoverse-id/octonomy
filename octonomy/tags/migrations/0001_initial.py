# Generated for Octonomy initial schema.

import uuid

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tag",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("tenant_id", models.CharField(max_length=100)),
                ("application_id", models.CharField(blank=True, max_length=100, null=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.CharField(max_length=255)),
                ("type", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="children",
                        to="tags.tag",
                    ),
                ),
            ],
            options={
                "db_table": "tags",
                "ordering": ["name", "slug"],
            },
        ),
        migrations.AddConstraint(
            model_name="tag",
            constraint=models.CheckConstraint(
                condition=~models.Q(("parent_id", models.F("id"))),
                name="tag_parent_cannot_be_self",
            ),
        ),
        migrations.AddConstraint(
            model_name="tag",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", True), ("is_active", True)),
                fields=("tenant_id", "type", "slug"),
                name="uniq_active_shared_tag_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="tag",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", False), ("is_active", True)),
                fields=("tenant_id", "application_id", "type", "slug"),
                name="uniq_active_app_tag_slug",
            ),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(
                fields=["tenant_id", "application_id", "type", "slug"],
                name="tags_tenant__e1fc6f_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(fields=["tenant_id", "type", "slug"], name="tag_shared_lookup_idx"),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(
                fields=["tenant_id", "application_id", "is_active"], name="tags_tenant__7959d5_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(
                fields=["tenant_id", "type", "is_active"], name="tags_tenant__7a27de_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(fields=["tenant_id", "parent"], name="tags_tenant__5fdd65_idx"),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["metadata"], name="tag_metadata_gin_idx"
            ),
        ),
    ]
