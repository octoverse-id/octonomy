from __future__ import annotations

from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response


@extend_schema(responses={200: OpenApiResponse(description="Service is live.")})
@api_view(["GET"])
@permission_classes([])
def live(request):
    return Response({"status": "ok"})


@extend_schema(responses={200: OpenApiResponse(description="Service is ready.")})
@api_view(["GET"])
@permission_classes([])
def ready(request):
    try:
        connections["default"].cursor()
    except OperationalError:
        return Response({"status": "unavailable"}, status=503)
    return Response({"status": "ok"})
