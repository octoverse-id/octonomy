from __future__ import annotations

from django.core.management.base import BaseCommand

from octonomy.assignments.services import assign_tag
from octonomy.tags.models import Tag


class Command(BaseCommand):
    help = "Seed local demo tags and assignments."

    def handle(self, *args, **options):
        tenant_id = "tenant_demo"
        tags = [
            {"application_id": None, "name": "Featured", "slug": "featured", "type": "label"},
            {"application_id": None, "name": "Archived", "slug": "archived", "type": "state"},
            {"application_id": "commerce", "name": "Sale", "slug": "sale", "type": "label"},
            {
                "application_id": "commerce",
                "name": "New Arrival",
                "slug": "new-arrival",
                "type": "label",
            },
            {"application_id": "cms", "name": "Editorial", "slug": "editorial", "type": "label"},
            {
                "application_id": "cms",
                "name": "Breaking News",
                "slug": "breaking-news",
                "type": "label",
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
            created_tags.append(tag)

        featured = next(tag for tag in created_tags if tag.slug == "featured")
        sale = next(tag for tag in created_tags if tag.slug == "sale")
        editorial = next(tag for tag in created_tags if tag.slug == "editorial")

        assign_tag(tenant_id, "commerce", featured, "product", "prod_123", "seed")
        assign_tag(tenant_id, "commerce", sale, "product", "prod_123", "seed")
        assign_tag(tenant_id, "cms", featured, "article", "article_123", "seed")
        assign_tag(tenant_id, "cms", editorial, "article", "article_123", "seed")

        self.stdout.write(self.style.SUCCESS("Seeded Octonomy demo data."))
