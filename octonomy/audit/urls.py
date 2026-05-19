from django.urls import path

from octonomy.audit.views import audit_logs_collection, resource_audit_logs, tag_audit_logs

urlpatterns = [
    path("audit-logs", audit_logs_collection, name="audit-logs"),
    path("tags/<uuid:tag_id>/audit-logs", tag_audit_logs, name="tag-audit-logs"),
    path(
        "resources/<str:resource_type>/<str:resource_id>/audit-logs",
        resource_audit_logs,
        name="resource-audit-logs",
    ),
]
