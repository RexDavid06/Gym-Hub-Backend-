import hashlib

import pytest
from rest_framework.test import APIClient

from accounts.models import Invite, StaffPermission, User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym):
    return User.objects.create_user(phone=phone, password="password123", role=role, gym=gym)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_invite(owner, phone):
    code = f"invite-{phone}"
    return Invite.objects.create(
        gym=owner.gym,
        role=Invite.Role.TRAINER,
        phone=phone,
        name="Invitee",
        created_by=owner,
        token_hash=hashlib.sha256(code.encode()).hexdigest(),
    )


def test_owner_can_list_invites_and_other_users_cannot():
    gym = Gym.objects.create(name="Gym")
    other_gym = Gym.objects.create(name="Other Gym")
    owner = make_user("+2348500000001", User.Role.OWNER, gym)
    other_owner = make_user("+2348500000002", User.Role.OWNER, other_gym)
    admin = make_user("+2348500000003", User.Role.ADMIN, gym)
    invite = make_invite(owner, "+2348500000010")
    make_invite(other_owner, "+2348500000011")

    response = client_for(owner).get("/api/invites/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [invite.pk]
    assert response.data[0]["role"] == Invite.Role.TRAINER
    assert response.data[0]["created_by"] == owner.pk
    assert client_for(admin).get("/api/invites/").status_code == 403


def test_admin_list_and_permission_update_include_permission_codes():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2348500000021", User.Role.OWNER, gym)
    admin = make_user("+2348500000022", User.Role.ADMIN, gym)
    StaffPermission.objects.create(user=admin, code="manage_members")

    response = client_for(owner).get("/api/admins/")
    update = client_for(owner).put(
        f"/api/admins/{admin.pk}/permissions/",
        {"codes": ["manage_trainers", "view_reports"]},
        format="json",
    )

    assert response.status_code == 200
    assert response.data[0]["permission_codes"] == ["manage_members"]
    assert update.status_code == 200
    assert set(update.data["permission_codes"]) == {"manage_trainers", "view_reports"}
