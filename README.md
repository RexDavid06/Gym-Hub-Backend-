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

## Rate limiting

Login allows 30 attempts per minute per IP and 5 per minute per phone number.
Gym self-registration defaults to 5 per hour per IP and is configurable with
`REGISTER_GYM_THROTTLE_RATE`. Invite redemption allows 5 per hour.

`NUM_PROXIES` (default `0`) is the number of trusted proxies in front of the
API and must be set to match the host. With `0`, `X-Forwarded-For` is ignored
and limits key on `REMOTE_ADDR`. On Render, set `NUM_PROXIES=1` so limits key
on the real client address rather than the proxy. A mismatch either lets a
client forge its own identity through the header or throttles every request
against the proxy's address.
