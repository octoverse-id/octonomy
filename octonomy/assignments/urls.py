from django.urls import path

from octonomy.assignments.views import (
    assignment_collection,
    bulk_assign,
    bulk_remove,
    resource_tags,
)

urlpatterns = [
    path("tag-assignments", assignment_collection, name="tag-assignments"),
    path("tag-assignments/bulk-assign", bulk_assign, name="tag-assignments-bulk-assign"),
    path("tag-assignments/bulk-remove", bulk_remove, name="tag-assignments-bulk-remove"),
    path(
        "resources/<str:resource_type>/<str:resource_id>/tags",
        resource_tags,
        name="resource-tags",
    ),
]
