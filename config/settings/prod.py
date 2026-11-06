from .base import *  # noqa: F403

DEBUG = False
if not SECRET_KEY or SECRET_KEY == "insecure-development-key-change-me":  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY must be set to a non-empty value in production")
if not env("DATABASE_URL", default="").strip():  # noqa: F405
    raise RuntimeError("DATABASE_URL must be set in production")
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":  # noqa: F405
    raise RuntimeError("Production requires PostgreSQL via DATABASE_URL")
SMS_PROVIDER = env("SMS_PROVIDER", default="disabled")  # noqa: F405
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", default="disabled")  # noqa: F405
if not CELERY_BROKER_URL.strip():  # noqa: F405
    raise RuntimeError("CELERY_BROKER_URL must be set in production")
if SMS_PROVIDER not in {"termii", "disabled"}:
    raise RuntimeError("Production SMS provider must be termii or disabled")
if WHATSAPP_PROVIDER not in {"meta", "disabled"}:
    raise RuntimeError("Production WhatsApp provider must be meta or disabled")
if SMS_PROVIDER == "termii" and not (TERMII_API_KEY and TERMII_SENDER_ID):  # noqa: F405
    raise RuntimeError("Termii credentials must be set when SMS is enabled in production")
if WHATSAPP_PROVIDER == "meta":  # noqa: F405
    missing_whatsapp = not all(
        [
            WHATSAPP_ACCESS_TOKEN,  # noqa: F405
            WHATSAPP_PHONE_NUMBER_ID,  # noqa: F405
            WHATSAPP_TEMPLATE_MEMBERSHIP_REMINDER,  # noqa: F405
            WHATSAPP_TEMPLATE_MEMBERSHIP_EXPIRED,  # noqa: F405
            WHATSAPP_TEMPLATE_PAYMENT_RECEIPT,  # noqa: F405
        ]
    )
    if missing_whatsapp:
        raise RuntimeError(
            "Meta WhatsApp credentials and template names must be set when WhatsApp is enabled"
        )
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
