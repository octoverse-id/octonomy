from __future__ import annotations

import warnings

from django.core.paginator import UnorderedObjectListWarning
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response


def warn_if_unordered(queryset) -> None:
    """Re-arm Django's unordered-pagination detector on the DRF slicing path.

    django.core.paginator.Paginator raises UnorderedObjectListWarning for exactly this
    hazard, but DRF's LimitOffsetPagination never builds a Paginator — it slices the
    queryset directly — so the warning could not fire anywhere in this codebase. That is
    how an unordered GET /tags survived to 3.2.0 (issue #162). Every paginated list view
    routes through this class, so the check belongs here.

    Limit worth knowing: `ordered` is True for order_by("?") and for a partial ordering
    like order_by("name"). It proves an ORDER BY exists, not that it is total, so
    endpoint tests still have to pin the full tiebreaker chain.
    """

    ordered = getattr(queryset, "ordered", None)
    if ordered is None or ordered:
        return
    queryset_repr = (
        f"{queryset.model} {queryset.__class__.__name__}"
        if hasattr(queryset, "model")
        else repr(queryset)
    )
    warnings.warn(
        "Pagination may yield inconsistent results with an unordered "
        f"object_list: {queryset_repr}.",
        UnorderedObjectListWarning,
        stacklevel=3,
    )


class OctonomyLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200

    def paginate_queryset(self, queryset, request, view=None):
        # Warn rather than raise: an unordered page is degraded, not unserveable, and a
        # 500 on a live read path is the worse failure. CI turns this into an error via
        # the filterwarnings entry in pyproject.toml, so it fails where it is cheap.
        warn_if_unordered(queryset)
        return super().paginate_queryset(queryset, request, view)

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
