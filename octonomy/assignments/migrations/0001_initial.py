# Generated for Octonomy initial schema.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tags", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TagAssignment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("tenant_id", models.CharField(max_length=100)),
                ("application_id", models.CharField(max_length=100)),
                ("resource_type", models.CharField(max_length=100)),
                ("resource_id", models.CharField(max_length=255)),
                ("assigned_by", models.CharField(blank=True, max_length=255, null=True)),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "tag",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="assignments",
                        to="tags.tag",
                    ),
                ),
            ],
            options={
                "db_table": "tag_assignments",
                "ordering": ["-assigned_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="tagassignment",
            constraint=models.CheckConstraint(
                condition=models.Q(("resource_type__regex", "^[a-z][a-z0-9_-]*$")),
                name="assignment_resource_type_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="tagassignment",
            constraint=models.CheckConstraint(
                condition=~models.Q(("resource_id", "")),
                name="assignment_resource_id_not_blank",
            ),
        ),
        migrations.AddConstraint(
            model_name="tagassignment",
            constraint=models.UniqueConstraint(
                fields=("tenant_id", "application_id", "resource_type", "resource_id", "tag"),
                name="uniq_assignment_per_resource_tag",
            ),
        ),
        migrations.AddIndex(
            model_name="tagassignment",
            index=models.Index(
                fields=["tenant_id", "application_id", "resource_type", "resource_id"],
                name="tag_assignm_tenant__5fd6df_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tagassignment",
            index=models.Index(fields=["tenant_id", "tag"], name="tag_assignm_tenant__cd908e_idx"),
        ),
        migrations.AddIndex(
            model_name="tagassignment",
            index=models.Index(
                fields=["tenant_id", "application_id", "tag"], name="tag_assignm_tenant__cd770b_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="tagassignment",
            index=models.Index(
                fields=["tenant_id", "resource_type", "resource_id"],
                name="tag_assignm_tenant__32ebe4_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tagassignment",
            index=models.Index(
                fields=["tenant_id", "-assigned_at"], name="tag_assignm_tenant__2d9d49_idx"
            ),
        ),
    ]
