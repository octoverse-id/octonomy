from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="outboxevent",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("published", "Published"),
                    ("failed", "Failed"),
                    ("dead_letter", "Dead letter"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="claim_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="recoveries",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Expired claim recoveries that did not reach the delivery transport.",
            ),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="claim_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(
                fields=["status", "claim_expires_at"],
                name="outbox_claim_exp_idx",
            ),
        ),
    ]
