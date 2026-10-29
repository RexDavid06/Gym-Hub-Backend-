# Backend

Django REST API foundation. Requires Python 3.12.

From this folder:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

Development uses SQLite. Production must set a non-empty `DJANGO_SECRET_KEY` and a PostgreSQL `DATABASE_URL`; settings fail during startup when either is missing. Never copy a real secret into `.env.example`.

Run checks with `pytest`, `ruff check .`, and `black --check .`.
