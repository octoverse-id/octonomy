from __future__ import annotations

from django.core.management.base import BaseCommand

from octonomy.assignments.services import assign_tag
from octonomy.tags.models import Tag, Vocabulary


class Command(BaseCommand):
    help = "Seed local demo tags and assignments."

    def handle(self, *args, **options):
        tenant_id = "tenant_demo"
        vocabularies = {
            "labels": {"application_id": None, "name": "Labels", "slug": "labels"},
            "product-labels": {
                "application_id": "commerce",
                "name": "Product Labels",
                "slug": "product-labels",
            },
            "content-labels": {
                "application_id": "cms",
                "name": "Content Labels",
                "slug": "content-labels",
            },
        }
        created_vocabularies = {}
        for key, data in vocabularies.items():
            vocabulary, _ = Vocabulary.objects.get_or_create(
                tenant_id=tenant_id,
                application_id=data["application_id"],
                slug=data["slug"],
                defaults={**data, "metadata": {}},
            )
            created_vocabularies[key] = vocabulary

        tags = [
            {
                "application_id": None,
                "name": "Featured",
                "slug": "featured",
                "type": "label",
                "vocabulary": created_vocabularies["labels"],
            },
            {
                "application_id": None,
                "name": "Archived",
                "slug": "archived",
                "type": "state",
                "vocabulary": created_vocabularies["labels"],
            },
            {
                "application_id": "commerce",
                "name": "Sale",
                "slug": "sale",
                "type": "label",
                "vocabulary": created_vocabularies["product-labels"],
            },
            {
                "application_id": "commerce",
                "name": "New Arrival",
                "slug": "new-arrival",
                "type": "label",
                "vocabulary": created_vocabularies["product-labels"],
            },
            {
                "application_id": "cms",
                "name": "Editorial",
                "slug": "editorial",
                "type": "label",
                "vocabulary": created_vocabularies["content-labels"],
            },
            {
                "application_id": "cms",
                "name": "Breaking News",
                "slug": "breaking-news",
                "type": "label",
                "vocabulary": created_vocabularies["content-labels"],
            },
        ]

        created_tags = []
        for data in tags:
            tag, _ = Tag.objects.get_or_create(
                tenant_id=tenant_id,
                application_id=data["application_id"],
                slug=data["slug"],
                type=data["type"],
                defaults={**data, "metadata": {}},
            )
            if tag.vocabulary_id != data["vocabulary"].id:
                tag.vocabulary = data["vocabulary"]
                tag.save(update_fields=["vocabulary", "updated_at"])
            created_tags.append(tag)

        featured = next(tag for tag in created_tags if tag.slug == "featured")
        sale = next(tag for tag in created_tags if tag.slug == "sale")
        editorial = next(tag for tag in created_tags if tag.slug == "editorial")

        assign_tag(tenant_id, "commerce", featured, "product", "prod_123", "seed")
        assign_tag(tenant_id, "commerce", sale, "product", "prod_123", "seed")
        assign_tag(tenant_id, "cms", featured, "article", "article_123", "seed")
        assign_tag(tenant_id, "cms", editorial, "article", "article_123", "seed")

        self.stdout.write(self.style.SUCCESS("Seeded Octonomy demo data."))
