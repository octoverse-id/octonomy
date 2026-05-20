from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from octonomy.service_auth.services import create_service_client_token


class Command(BaseCommand):
    help = "Create a service API token and print the raw token once."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--tenant", required=True)
        parser.add_argument("--application", required=False)
        parser.add_argument("--scope", action="append", required=True)
        parser.add_argument("--expires-at", required=False)
        parser.add_argument("--metadata", required=False, help="Optional JSON object.")

    def handle(self, *args, **options):
        expires_at = None
        if options.get("expires_at"):
            expires_at = parse_datetime(options["expires_at"])
            if expires_at is None:
                raise CommandError("--expires-at must be an ISO 8601 datetime.")

        metadata = {}
        if options.get("metadata"):
            try:
                metadata = json.loads(options["metadata"])
            except json.JSONDecodeError as exc:
                raise CommandError("--metadata must be a valid JSON object.") from exc
            if not isinstance(metadata, dict):
                raise CommandError("--metadata must be a valid JSON object.")

        token, client = create_service_client_token(
            name=options["name"],
            expires_at=expires_at,
            metadata=metadata,
            grants=[
                {
                    "tenant_id": options["tenant"],
                    "application_id": options.get("application"),
                    "scopes": options["scope"],
                }
            ],
        )
        self.stdout.write(f"Service client: {client.id}")
        self.stdout.write(f"Key prefix: {client.key_prefix}")
        self.stdout.write(f"Token: {token}")
        self.stdout.write("Store this token now. It cannot be shown again.")
