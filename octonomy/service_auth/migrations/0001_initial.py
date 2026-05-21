# Generated for Octonomy service auth.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ServiceClient",
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
                ("name", models.CharField(max_length=255)),
                ("key_prefix", models.CharField(max_length=32, unique=True)),
                ("hashed_key", models.CharField(max_length=128, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(default=dict)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "service_clients",
            },
        ),
        migrations.CreateModel(
            name="ServiceClientGrant",
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
                ("scopes", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "service_client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grants",
                        to="service_auth.serviceclient",
                    ),
                ),
            ],
            options={
                "db_table": "service_client_grants",
            },
        ),
        migrations.AddIndex(
            model_name="serviceclient",
            index=models.Index(fields=["key_prefix"], name="svc_client_prefix_idx"),
        ),
        migrations.AddIndex(
            model_name="serviceclient",
            index=models.Index(
                fields=["is_active", "expires_at"],
                name="svc_client_active_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceclientgrant",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", False)),
                fields=("service_client", "tenant_id", "application_id"),
                name="uniq_service_app_grant",
            ),
        ),
        migrations.AddConstraint(
            model_name="serviceclientgrant",
            constraint=models.UniqueConstraint(
                condition=models.Q(("application_id__isnull", True)),
                fields=("service_client", "tenant_id"),
                name="uniq_service_tenant_grant",
            ),
        ),
        migrations.AddIndex(
            model_name="serviceclientgrant",
            index=models.Index(
                fields=["tenant_id", "application_id"],
                name="svc_grant_tenant_app_idx",
            ),
        ),
    ]
