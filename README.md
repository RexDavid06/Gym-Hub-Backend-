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

## Notifications, Celery, and Redis

Phase 8 adds tenant-scoped SMS and WhatsApp reminders and payment receipts.

```sh
celery -A config worker -Q default,notifications -l info
celery -A config beat -l info
```

Development defaults to `CELERY_TASK_ALWAYS_EAGER=true`, which runs tasks inline without Redis. For a real broker and worker, set `CELERY_TASK_ALWAYS_EAGER=false` and provide `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` (both default to local Redis). Celery Beat schedules the reminder task daily at 08:00 Africa/Lagos.

Provider settings:

- SMS: `SMS_PROVIDER=console` in development; production accepts `termii` (requires `TERMII_API_KEY` and `TERMII_SENDER_ID`) or `disabled`.
- WhatsApp: `WHATSAPP_PROVIDER=console` in development; production accepts `meta` (requires `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and the three approved template names) or `disabled`.
- `NOTIFICATION_HTTP_TIMEOUT` bounds provider HTTP calls.

Delivery semantics: notification rows move `pending → sending → sent | failed | skipped`. Transient provider failures retry up to three attempts; permanent failures are not retried automatically. `sent` means the provider accepted the message, not that the handset received it. Owners can retry failed rows from the API or web. WhatsApp is sent only to opted-in members. Suspended/inactive gyms do not send, and skipped messages are not backfilled after reactivation. Logs mask phone numbers, and provider credentials and raw responses are never returned by the API.

`tests/test_phase8_redis_smoke.py` is skipped by default. CI runs it with a live Redis service and a Celery worker by setting `RUN_REDIS_SMOKE=1`.

## Shared API client and web dashboard

The admin dashboard lives in the separate `web/` repository (React +
TypeScript on Vite). This backend repository also hosts the canonical
`@gym-hub/api-client` package in `api-client/`. The backend is the source of
truth for `schema.yml`; the package generates its TypeScript schema from that
file, and web and mobile consume the package.

After an API change, regenerate the schema and the package types:

```sh
python manage.py spectacular --file schema.yml --validate
cp schema.yml api-client/schema.yml
npm --prefix api-client run gen:api
```

Build the package before typechecking, testing, or building web or mobile.
Each app's CI stages the package from this repository and builds it first.

```sh
npm --prefix api-client ci
npm --prefix api-client run build
```

From the web repository:

```sh
npm install
npm run dev            # http://localhost:5173
```

Start the Django server first: Vite proxies `/api` to `http://127.0.0.1:8000`.
Set `VITE_API_BASE_URL` to call a different origin.

Sign in with a phone number and password, the same credentials as the API.
Owners and admins get members, memberships and payments; an admin only sees
the sections their staff permissions allow. A `super_admin` lands on the
platform screens for gym summary, listing, and suspend/reactivate. The API
enforces the same rules, so hiding a section is a convenience, never the
security boundary.

Run the web checks with `npm run lint`, `npm run typecheck`,
`npm run test:run`, and `npm run build`.

## Deploying to Render

The repository root contains a `render.yaml` Blueprint. Deploy it from the
backend repository in the Render Dashboard (New → Blueprint). It provisions:

- a Django web service running Gunicorn, with `/api/health/` as the health check;
- a Celery worker on the `default,notifications` queues and a Celery beat scheduler;
- Render Postgres and Render Key Value (Redis);
- the static web dashboard from the `Gym-Hub-Frontend` repository.

The Blueprint uses paid `0.5c-512mb` service plans and a `basic-256mb` database.
Free web services sleep and free Postgres instances expire, so they are not
production-safe.

The web service runs migrations with `preDeployCommand`, serves Django admin
static files through WhiteNoise, and trusts Render's proxy headers. Production
settings fail fast when `DJANGO_SECRET_KEY`, `DATABASE_URL`, or
`CELERY_BROKER_URL` is missing.

After the first deploy:

1. If Render had to append a suffix to the `gym-hub-web` name, update
   `CORS_ALLOWED_ORIGINS` on the API service to the actual dashboard origin.
2. For custom domains, add them to `DJANGO_ALLOWED_HOSTS` and
   `CSRF_TRUSTED_ORIGINS` on the API service.
3. Create the first platform superuser from the Render shell:
   `python manage.py createsuperuser`.
4. Enable SMS or WhatsApp by overriding `SMS_PROVIDER` / `WHATSAPP_PROVIDER`
   and setting the provider credentials from the dashboard.

If the API client schema changes, update `API_CLIENT_REF` on the static site to
the backend branch or tag that contains the matching `api-client` package.

To run the production build locally against a PostgreSQL URL:

```sh
DJANGO_SETTINGS_MODULE=config.settings.prod \
DJANGO_SECRET_KEY=replace-me \
DATABASE_URL=postgres://user:pass@localhost:5432/gymhub \
CELERY_BROKER_URL=redis://localhost:6379/0 \
bash build.sh
```
