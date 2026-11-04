from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Registers the OpenAPI security scheme for the gym-aware JWT class.
        from accounts import schema  # noqa: F401
