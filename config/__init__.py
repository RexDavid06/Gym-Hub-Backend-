"""Django app package init.

This module deliberately avoids importing the Celery app at import time.
config.celery is imported lazily by the caller (manage.py / gunicorn / asgi.py
already import config.celery first), or the Celery app is started only after
Django settings are fully loaded. Importing config/__init__.py must never
raise before DJANGO_SETTINGS_MODULE is configured.
"""
