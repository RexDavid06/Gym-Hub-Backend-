from .base import *  # noqa: F403

DEBUG = False
if not SECRET_KEY or SECRET_KEY == "insecure-development-key-change-me":  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY must be set to a non-empty value in production")
if not env("DATABASE_URL", default="").strip():  # noqa: F405
    raise RuntimeError("DATABASE_URL must be set in production")
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":  # noqa: F405
    raise RuntimeError("Production requires PostgreSQL via DATABASE_URL")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
