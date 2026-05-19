from __future__ import annotations

from rest_framework.response import Response


def data_response(data, status=None) -> Response:
    return Response({"data": data}, status=status)
