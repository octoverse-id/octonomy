from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, register

DEFAULT_SECRET_KEY = "local-dev-secret"
DEFAULT_SERVICE_TOKEN_PEPPER = "local-dev-service-token-pepper"


@register(Tags.security, deploy=True)
def production_settings_check(app_configs, **kwargs):
    if settings.DEBUG:
        return []

    messages = []
    messages.extend(_check_secret_key())
    messages.extend(_check_service_token_pepper())
    messages.extend(_check_allowed_hosts())
    messages.extend(_check_database_engine())
    return messages


def _check_secret_key():
    if settings.SECRET_KEY and settings.SECRET_KEY != DEFAULT_SECRET_KEY:
        return []
    return [
        Error(
            "DJANGO_SECRET_KEY must be set to a non-default value when DJANGO_DEBUG=false.",
            id="octonomy.E001",
        )
    ]


def _check_service_token_pepper():
    pepper = getattr(settings, "SERVICE_TOKEN_PEPPER", "")
    if pepper and pepper != DEFAULT_SERVICE_TOKEN_PEPPER:
        return []
    return [
        Error(
            "SERVICE_TOKEN_PEPPER must be set to a non-default value when DJANGO_DEBUG=false.",
            id="octonomy.E002",
        )
    ]


def _check_allowed_hosts():
    allowed_hosts = [host for host in getattr(settings, "ALLOWED_HOSTS", []) if host]
    if not allowed_hosts:
        return [
            Error(
                "ALLOWED_HOSTS must include at least one production host when DJANGO_DEBUG=false.",
                id="octonomy.E003",
            )
        ]
    if "*" not in allowed_hosts:
        return []
    return [
        Error(
            "ALLOWED_HOSTS must not use '*' for production deployments.",
            id="octonomy.E004",
        )
    ]


def _check_database_engine():
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if "sqlite" not in engine:
        return []
    return [
        Error(
            "Production deployments must use PostgreSQL instead of SQLite.",
            id="octonomy.E005",
        )
    ]
