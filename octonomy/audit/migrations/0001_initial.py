# Generated for Octonomy audit logs.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AuditLog",
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
                ("action", models.CharField(max_length=100)),
                ("entity_type", models.CharField(max_length=100)),
                ("entity_id", models.CharField(max_length=255)),
                ("tag_id", models.UUIDField(blank=True, null=True)),
                ("resource_type", models.CharField(blank=True, max_length=100, null=True)),
                ("resource_id", models.CharField(blank=True, max_length=255, null=True)),
                ("actor_id", models.CharField(blank=True, max_length=255, null=True)),
                ("request_id", models.CharField(blank=True, max_length=100, null=True)),
                ("operation_id", models.UUIDField(default=uuid.uuid4)),
                ("changes", models.JSONField(default=dict)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "audit_logs",
                "ordering": ["-created_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "-created_at"],
                name="audit_tenant_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "action", "-created_at"],
                name="audit_action_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "entity_type", "entity_id", "-created_at"],
                name="audit_entity_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "tag_id", "-created_at"],
                name="audit_tag_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "application_id", "-created_at"],
                name="audit_app_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["tenant_id", "resource_type", "resource_id", "-created_at"],
                name="audit_res_created_idx",
            ),
        ),
    ]
