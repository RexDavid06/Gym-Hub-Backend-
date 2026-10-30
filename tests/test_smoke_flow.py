import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.models import User

pytestmark = pytest.mark.django_db


def auth_client(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def login(phone, password):
    return APIClient().post("/api/token/", {"phone": phone, "password": password}, format="json")


def test_end_to_end_onboarding_and_permission_flow():
    cache.clear()
    owner_phone = "+2349000000001"
    admin_phone = "+2349000000002"
    trainer_phone = "+2349000000003"
    owner_password = "owner-password"
    admin_password = "admin-password"
    trainer_password = "trainer-password"

    registered = APIClient().post(
        "/api/auth/register-gym/",
        {
            "name": "Smoke Owner",
            "phone": owner_phone,
            "password": owner_password,
            "gym_name": "Smoke Gym",
        },
        format="json",
    )
    assert registered.status_code == 201
    gym_id = registered.data["gym_id"]

    owner_login = login(owner_phone, owner_password)
    assert owner_login.status_code == 200
    owner = auth_client(owner_login.data["access"])

    me = owner.get("/api/me/")
    assert me.status_code == 200
    assert me.data["role"] == User.Role.OWNER
    assert me.data["gym"] == {"id": gym_id, "name": "Smoke Gym"}

    admin_invite = owner.post(
        "/api/invites/",
        {"role": "admin", "phone": admin_phone, "name": "Smoke Admin"},
        format="json",
    )
    trainer_invite = owner.post(
        "/api/invites/",
        {"role": "trainer", "phone": trainer_phone, "name": "Smoke Trainer"},
        format="json",
    )
    assert admin_invite.status_code == 201
    assert trainer_invite.status_code == 201

    admin_redeem = APIClient().post(
        "/api/auth/redeem-invite/",
        {"code": admin_invite.data["code"], "password": admin_password},
        format="json",
    )
    trainer_redeem = APIClient().post(
        "/api/auth/redeem-invite/",
        {"code": trainer_invite.data["code"], "password": trainer_password},
        format="json",
    )
    assert admin_redeem.status_code == 200
    assert trainer_redeem.status_code == 200

    admin_login = login(admin_phone, admin_password)
    assert admin_login.status_code == 200
    admin = auth_client(admin_login.data["access"])
    admin_me = admin.get("/api/me/")
    assert admin_me.data["role"] == User.Role.ADMIN
    assert admin_me.data["gym"] == {"id": gym_id, "name": "Smoke Gym"}

    trainer_login = login(trainer_phone, trainer_password)
    assert trainer_login.status_code == 200
    trainer_access = trainer_login.data["access"]
    trainer = auth_client(trainer_access)
    trainer_me = trainer.get("/api/me/")
    assert trainer_me.data["role"] == User.Role.TRAINER
    assert trainer_me.data["gym"] == {"id": gym_id, "name": "Smoke Gym"}

    admin_user_id = admin_me.data["id"]
    grant = owner.put(
        f"/api/admins/{admin_user_id}/permissions/",
        {"codes": ["manage_members"]},
        format="json",
    )
    assert grant.status_code == 200
    assert grant.data["permission_codes"] == ["manage_members"]
    assert admin.get("/api/users/?role=member").status_code == 200

    revoke = owner.delete(f"/api/admins/{admin_user_id}/permissions/manage_members/")
    assert revoke.status_code == 204
    assert admin.get("/api/users/").status_code == 403

    trainer_user_id = trainer_me.data["id"]
    deactivated = owner.post(f"/api/users/{trainer_user_id}/deactivate/")
    assert deactivated.status_code == 200
    assert deactivated.data["is_active"] is False

    assert login(trainer_phone, trainer_password).status_code == 401
    assert trainer.get("/api/me/").status_code == 401
    assert auth_client(trainer_access).get("/api/me/").status_code == 401
