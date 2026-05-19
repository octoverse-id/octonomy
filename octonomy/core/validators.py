from __future__ import annotations

import re

from rest_framework import serializers

SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def validate_external_id(value: str, field_name: str) -> str:
    if not value or not str(value).strip():
        raise serializers.ValidationError({field_name: "This value cannot be blank."})
    return value


def validate_slug_like(value: str, field_name: str) -> str:
    validate_external_id(value, field_name)
    if not SLUG_PATTERN.match(value):
        raise serializers.ValidationError(
            {field_name: "Use lowercase letters, numbers, underscores, or hyphens."}
        )
    return value
