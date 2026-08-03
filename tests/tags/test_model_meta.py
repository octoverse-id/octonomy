"""Model verbose names — guard the admin labels against Django's naive pluralizer.

Without explicit ``verbose_name_plural`` the admin renders "Vocabularys" / "Tag aliass".
"""

from __future__ import annotations

from octonomy.tags.models import TagAlias, Vocabulary


def test_vocabulary_plural_label():
    assert str(Vocabulary._meta.verbose_name_plural) == "vocabularies"


def test_tag_alias_plural_label():
    assert str(TagAlias._meta.verbose_name_plural) == "tag aliases"
