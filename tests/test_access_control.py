import hashlib
import json

import pytest
from rest_framework.test import APIClient

from accounts.models import Invite, User
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


@pytest.mark.parametrize("role", [User.Role.ADMIN, User.Role.TRAINER, User.Role.MEMBER])
def test_invite_list_forbidden_for_non_owner_roles(role):
    gym = Gym.objects.create(name="Gym")
    user = make_user(f"+23486000000{list(User.Role.values).index(role)}", role, gym)

    assert client_for(user).get("/api/invites/").status_code == 403


def test_owner_invite_list_excludes_secret_fields_and_other_gyms():
    gym = Gym.objects.create(name="Gym")
    other_gym = Gym.objects.create(name="Other Gym")
    owner = make_user("+2348700000001", User.Role.OWNER, gym)
    other_owner = make_user("+2348700000002", User.Role.OWNER, other_gym)
    own_invite = make_invite(owner, "+2348700000010")
    make_invite(other_owner, "+2348700000011")

    response = client_for(owner).get("/api/invites/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [own_invite.pk]
    payload = json.dumps(response.data)
    assert "code" not in payload
    assert "token_hash" not in payload


def test_trainer_and_member_cannot_create_invites():
    gym = Gym.objects.create(name="Gym")
    trainer = make_user("+2348800000001", User.Role.TRAINER, gym)
    member = make_user("+2348800000002", User.Role.MEMBER, gym)
    payload = {"role": Invite.Role.MEMBER, "phone": "+2348800000010", "name": "Invitee"}

    assert client_for(trainer).post("/api/invites/", payload, format="json").status_code == 403
    assert client_for(member).post("/api/invites/", payload, format="json").status_code == 403
    assert not Invite.objects.exists()


@pytest.mark.parametrize("role", [User.Role.TRAINER, User.Role.MEMBER, User.Role.ADMIN])
def test_user_list_forbidden_for_trainer_member_and_admin_without_permissions(role):
    gym = Gym.objects.create(name="Gym")
    user = make_user(f"+23489000000{list(User.Role.values).index(role)}", role, gym)

    assert client_for(user).get("/api/users/").status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/invites/", None),
        ("post", "/api/invites/", {"role": "member", "phone": "+2348000000100", "name": "X"}),
        ("get", "/api/admins/", None),
        ("put", "/api/admins/1/permissions/", {"codes": []}),
        ("delete", "/api/admins/1/permissions/manage_members/", None),
        ("get", "/api/users/", None),
        ("post", "/api/users/1/deactivate/", None),
        ("get", "/api/me/", None),
    ],
)
def test_unauthenticated_business_endpoints_return_401(method, path, payload):
    client = APIClient()

    response = getattr(client, method)(path, payload, format="json")

    assert response.status_code == 401
