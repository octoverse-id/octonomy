import uuid

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tags', '0002_vocabulary_tag_vocabulary'),
    ]

    operations = [
        migrations.CreateModel(
            name="TagAlias",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.CharField(max_length=100)),
                ("application_id", models.CharField(blank=True, max_length=100, null=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.CharField(max_length=255)),
                ("metadata", models.JSONField(default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tag",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="aliases",
                        to="tags.tag",
                    ),
                ),
            ],
            options={
                "db_table": "tag_aliases",
                "ordering": ["name", "slug"],
                "indexes": [
                    models.Index(
                        fields=["tenant_id", "application_id", "slug"],
                        name="alias_tenant_app_slug_idx",
                    ),
                    models.Index(
                        fields=["tenant_id", "tag", "is_active"],
                        name="alias_tenant_tag_active_idx",
                    ),
                    models.Index(
                        fields=["tenant_id", "is_active"],
                        name="alias_tenant_active_idx",
                    ),
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["metadata"],
                        name="alias_metadata_gin_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("application_id__isnull", True),
                            ("is_active", True),
                        ),
                        fields=("tenant_id", "slug"),
                        name="uniq_active_shared_alias_slug",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("application_id__isnull", False),
                            ("is_active", True),
                        ),
                        fields=("tenant_id", "application_id", "slug"),
                        name="uniq_active_app_alias_slug",
                    ),
                ],
            },
        ),
    ]
