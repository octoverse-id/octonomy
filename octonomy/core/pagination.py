from __future__ import annotations

from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response


class OctonomyLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200

    def get_paginated_response(self, data):
        # v1 list endpoints expose limit/offset metadata beside the data envelope
        # so clients can page without learning DRF default response shape.
        return Response(
            {
                "data": data,
                "pagination": {
                    "limit": self.limit,
                    "offset": self.offset,
                    "count": self.count,
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
            }
        )
