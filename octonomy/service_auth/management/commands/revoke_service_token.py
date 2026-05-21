from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from octonomy.service_auth.models import ServiceClient


class Command(BaseCommand):
    help = "Deactivate a service API token by key prefix."

    def add_arguments(self, parser):
        parser.add_argument("--prefix", required=True)

    def handle(self, *args, **options):
        try:
            client = ServiceClient.objects.get(key_prefix=options["prefix"])
        except ServiceClient.DoesNotExist:
            raise CommandError("Service client was not found.")

        client.is_active = False
        client.save(update_fields=["is_active", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Revoked service client {client.id}."))
