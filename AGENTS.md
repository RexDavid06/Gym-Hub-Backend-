# Gym Management SaaS (project name TBD)

Multi-tenant gym management platform for Nigerian gyms (small to large).

## Monorepo
- /backend: Django + DRF (API only), Postgres, Celery + Redis
- /web: React + TypeScript (Vite), admin dashboard
- /mobile: React Native (Expo) + TypeScript, one app for trainers and members
- /packages/api-client: typed client generated from the OpenAPI schema

## Roles
super_admin (platform owner) > owner (first paying admin of a gym) > admin > trainer > member.
- Owner creates admins and restricts them via StaffPermission codes.
- Admins onboard trainers and members and assign trainers to members.
- Trainers cannot choose clients. They create workout plans ONLY for members actively assigned to them.
- Members see only their own data.
- Signup is closed. Users join through one-time invites tied to a gym and role.

## Non-negotiable rules
1. Every tenant-owned model has a `gym` FK. Managers are not implicitly gym-scoped: `GymFilterableManager.for_gym()` filters explicitly, and API code must scope through `GymScopedQuerysetMixin`. `backend/tests/test_tenancy_guard.py` fails if any ViewSet over a tenant model does not. Never query across gyms.
2. Permissions are enforced in the backend (DRF permission classes), never only in the UI.
3. Login by phone number. Custom User model from the first migration.
4. Every feature ships with tests, including cross-gym leakage tests and "trainer cannot access non-assigned member" tests.
5. Settings split: base/dev/prod. Secrets only via environment variables. Never commit secrets.
6. API documented with drf-spectacular. Frontends use the generated client, not hand-written fetch calls.
7. Small, focused commits. Work one phase at a time. Do not build features outside the current phase.
8. Before finishing a task, run tests and linters and report results honestly, including failures.

## Brand (web and mobile)
Navy #0B1F3A, navy light #16345C, orange #FF6B1A (actions only), orange dark #E55A0F,
background #F4F6FA, text #0F172A / #64748B, success #16A34A, warning #F59E0B, danger #DC2626.
Fonts: Poppins (headings), Inter (body). Rounded corners ~12px.
