# Generated for Octonomy vocabularies.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tags", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Vocabulary",
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
                ("description", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "vocabularies",
                "ordering": ["name", "slug"],
            },
        ),
        migrations.AddField(
            model_name="tag",
            name="vocabulary",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="tags",
                to="tags.vocabulary",
            ),
        ),
        migrations.AddConstraint(
            model_name="vocabulary",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", True), ("is_active", True)),
                fields=("tenant_id", "slug"),
                name="uniq_active_shared_vocab_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="vocabulary",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", False), ("is_active", True)),
                fields=("tenant_id", "application_id", "slug"),
                name="uniq_active_app_vocab_slug",
            ),
        ),
        migrations.AddIndex(
            model_name="vocabulary",
            index=models.Index(
                fields=["tenant_id", "application_id", "slug"],
                name="vocab_tenant_app_slug_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="vocabulary",
            index=models.Index(
                fields=["tenant_id", "application_id", "is_active"],
                name="vocab_tenant_app_active_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tag",
            index=models.Index(
                fields=["tenant_id", "vocabulary"],
                name="tags_tenant_vocab_idx",
            ),
        ),
    ]
