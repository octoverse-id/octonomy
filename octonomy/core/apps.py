from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "octonomy.core"

    def ready(self):
        from . import checks  # noqa: F401
