from django.urls import path

from octonomy.assignments.views import tag_resources
from octonomy.tags.views import tag_detail, tags_collection
from octonomy.tags.vocabulary_views import vocabularies_collection, vocabulary_detail

urlpatterns = [
    path("vocabularies", vocabularies_collection, name="vocabularies-collection"),
    path("vocabularies/<uuid:vocabulary_id>", vocabulary_detail, name="vocabulary-detail"),
    path("tags", tags_collection, name="tags-collection"),
    path("tags/<uuid:tag_id>", tag_detail, name="tag-detail"),
    path("tags/<uuid:tag_id>/resources", tag_resources, name="tag-resources"),
]
