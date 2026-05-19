from __future__ import annotations

from django.conf import settings
from rest_framework.permissions import BasePermission


class BearerTokenPermission(BasePermission):
    """Development bearer-token placeholder for service-to-service auth."""

    message = "Bearer authentication is required."

    def has_permission(self, request, view) -> bool:
        if getattr(view, "allow_unauthenticated", False):
            return True

        expected_token = getattr(settings, "AUTH_BEARER_TOKEN_DEV", "")
        authorization = request.headers.get("Authorization", "")

        if not authorization.startswith("Bearer "):
            return False

        if not expected_token:
            return True

        return authorization.removeprefix("Bearer ").strip() == expected_token
