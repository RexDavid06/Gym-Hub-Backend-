import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEYS = {"DJANGO_SECRET_KEY", "DATABASE_URL"}


def load_prod_settings(**env_overrides):
    env = {key: value for key, value in os.environ.items() if key not in SECRET_KEYS}
    env.update(env_overrides)
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_prod_settings_refuse_to_start_without_secret_key_and_database_url():
    result = load_prod_settings(DJANGO_SECRET_KEY="", DATABASE_URL="")

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY must be set" in result.stderr


def test_prod_settings_refuse_to_start_without_database_url():
    result = load_prod_settings(DJANGO_SECRET_KEY="a-real-secret-for-the-test", DATABASE_URL="")

    assert result.returncode != 0
    assert "DATABASE_URL must be set" in result.stderr


def test_prod_settings_refuse_to_start_on_sqlite():
    result = load_prod_settings(
        DJANGO_SECRET_KEY="a-real-secret-for-the-test", DATABASE_URL="sqlite:///db.sqlite3"
    )

    assert result.returncode != 0
    assert "Production requires PostgreSQL" in result.stderr


def test_prod_settings_start_with_secret_and_postgres_url():
    result = load_prod_settings(
        DJANGO_SECRET_KEY="a-real-secret-for-the-test",
        DATABASE_URL="postgres://user:pass@localhost:5432/gymhub",
    )

    assert result.returncode == 0, result.stderr
