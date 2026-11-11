from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env(DEBUG=(bool, False))
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-development-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "rest_framework_simplejwt.token_blacklist",
    "gyms",
    "accounts",
    "workouts",
    "memberships",
    "notifications",
    "platform_admin",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3")}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # Gym-aware JWT authentication rejects tokens issued before a gym was
    # suspended, so suspension does not wait for access tokens to expire.
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.authentication.GymAwareJWTAuthentication"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Proxies in front of the API. 0 ignores X-Forwarded-For and throttles on
    # REMOTE_ADDR; set to match the host (e.g. 1 on Render) so per-IP limits key
    # on the real client. Must equal the number of trusted proxies.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "DEFAULT_THROTTLE_RATES": {
        "invite_redeem": "5/hour",
        "login": "30/min",
        "login_phone": "5/min",
        "register_gym": env("REGISTER_GYM_THROTTLE_RATE", default="5/hour"),
    },
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Gym Management API",
    "DESCRIPTION": "Foundation API for the Gym Management SaaS.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "UserRoleEnum": "accounts.models.USER_ROLE_CHOICES",
        "InviteRoleEnum": "accounts.models.INVITE_ROLE_CHOICES",
    },
}
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:5173"])

CELERY_BROKER_URL = env(
    "CELERY_BROKER_URL", default=env("REDIS_URL", default="redis://localhost:6379/0")
)
CELERY_RESULT_BACKEND = env(
    "CELERY_RESULT_BACKEND", default=env("REDIS_URL", default="redis://localhost:6379/1")
)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = env.bool("CELERY_TASK_EAGER_PROPAGATES", default=False)
CELERY_TIMEZONE = "Africa/Lagos"
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "notifications.tasks.deliver_notification": {"queue": "notifications"},
}
CELERY_BEAT_SCHEDULE = {
    "schedule-membership-reminders": {
        "task": "notifications.tasks.schedule_membership_reminders",
        "schedule": crontab(hour=8, minute=0),
    }
}

SMS_PROVIDER = env("SMS_PROVIDER", default="console")
TERMII_API_KEY = env("TERMII_API_KEY", default="")
TERMII_SENDER_ID = env("TERMII_SENDER_ID", default="")
TERMII_BASE_URL = env("TERMII_BASE_URL", default="https://api.ng.termii.com")
TERMII_CHANNEL = env("TERMII_CHANNEL", default="generic")

WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", default="console")
WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_API_VERSION = env("WHATSAPP_API_VERSION", default="v20.0")
WHATSAPP_TEMPLATE_LANGUAGE = env("WHATSAPP_TEMPLATE_LANGUAGE", default="en_US")
WHATSAPP_TEMPLATE_MEMBERSHIP_REMINDER = env(
    "WHATSAPP_TEMPLATE_MEMBERSHIP_REMINDER", default="membership_reminder"
)
WHATSAPP_TEMPLATE_MEMBERSHIP_EXPIRED = env(
    "WHATSAPP_TEMPLATE_MEMBERSHIP_EXPIRED", default="membership_expired"
)
WHATSAPP_TEMPLATE_PAYMENT_RECEIPT = env(
    "WHATSAPP_TEMPLATE_PAYMENT_RECEIPT", default="payment_receipt"
)
NOTIFICATION_HTTP_TIMEOUT = env.int("NOTIFICATION_HTTP_TIMEOUT", default=10)
