from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from octonomy.service_auth.models import ServiceClient, ServiceClientGrant

TOKEN_PREFIX = "octo"
MAX_SERVICE_TOKEN_LENGTH = 200


@dataclass(frozen=True)
class AuthenticatedService:
    client: ServiceClient
    token: str


def hash_service_token(token: str) -> str:
    pepper = settings.SERVICE_TOKEN_PEPPER.encode()
    return hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest()


def generate_service_token() -> tuple[str, str]:
    key_prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"{TOKEN_PREFIX}_{key_prefix}_{secret}", key_prefix


def parse_service_token(token: str) -> tuple[str, str] | None:
    if len(token) > MAX_SERVICE_TOKEN_LENGTH:
        return None

    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
        return None
    return token, parts[1]


def create_service_client_token(
    *,
    name: str,
    grants: list[dict],
    is_active: bool = True,
    expires_at=None,
    metadata: dict | None = None,
) -> tuple[str, ServiceClient]:
    token, key_prefix = generate_service_token()
    with transaction.atomic():
        client = ServiceClient.objects.create(
            name=name,
            key_prefix=key_prefix,
            hashed_key=hash_service_token(token),
            is_active=is_active,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        ServiceClientGrant.objects.bulk_create(
            [
                ServiceClientGrant(
                    service_client=client,
                    tenant_id=grant["tenant_id"],
                    application_id=grant.get("application_id"),
                    scopes=grant.get("scopes", []),
                )
                for grant in grants
            ]
        )
    return token, client


def authenticate_service_token(token: str) -> ServiceClient | None:
    parsed = parse_service_token(token)
    if parsed is None:
        return None

    raw_token, key_prefix = parsed
    hashed_key = hash_service_token(raw_token)
    try:
        client = ServiceClient.objects.prefetch_related("grants").get(
            key_prefix=key_prefix,
            hashed_key=hashed_key,
        )
    except ServiceClient.DoesNotExist:
        return None

    if not client.is_active:
        return None

    if client.expires_at and client.expires_at <= timezone.now():
        return None

    return client


def grant_allows(
    client: ServiceClient,
    *,
    tenant_id: str,
    application_id: str | None,
    scope: str,
) -> bool:
    grants = client.grants.all()
    tenant_wide = [
        grant
        for grant in grants
        if grant.tenant_id == tenant_id and grant.application_id is None and grant.has_scope(scope)
    ]
    if tenant_wide:
        return True

    if application_id is None:
        return False

    return any(
        grant.tenant_id == tenant_id
        and grant.application_id == application_id
        and grant.has_scope(scope)
        for grant in grants
    )
